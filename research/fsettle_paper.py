"""Paper tracker: funding-settlement trade (reports/scalping_prereg.md, tests C / C-X / C-X-tick).

Public Binance USDT-M websocket market data only: no API keys, no REST calls, no orders.
Rule (C-X-tick, registered 2026-10-06): at settlement s of a perp whose PREVIOUS settled funding |f_prev| >= 0.10%,
trade direction d = sign(f_prev); entry = first entry-side aggTrade at/after s+250 ms (short: first sell-aggressor
trade = bid; long: first buy-aggressor = ask); exit = first exit-side trade at/after s+30m, or at/after the first trade
>= 200 bp adverse to entry (stop). Net = gross - 10 bp fees. Variants are simulated alongside on the same tape.
pre60 (C-pre, registered 2026-10-07; confirmation C-pre-X pending): a -d leg from the first -d entry-side trade at/after
s-60 s (and before s) to the rule's entry trade at/after s+250 ms, receiving the funding settled at s:
net = -d*(exit/entry - 1)*1e4 + d*f(s)*1e4 - 10 bp. f(s) = last stream `r` before the rollover at s.

f_prev is learned from the stream itself: the last `r` of !markPrice@arr@1s before a symbol's next-funding time T rolls
over is that settlement's rate. A symbol qualifies only after the tracker has seen one settlement for it (warm-up). The learned rates are saved to
OUT/fprev_state.json every 30 s and reloaded at start while their next settlement has not happened yet (no warm-up
after a quick restart).
Output: one CSV row per event in research_cache/scalp/paper_fsettle/events.csv (+ settlements.csv for validation).
"""
import asyncio
import csv
import json
import logging
import math
import os
import statistics
import time

import websockets

BASE = 'wss://fstream.binance.com/market/ws'
OUT = os.environ.get('FSETTLE_OUT', '/root/bitana/research_cache/scalp/paper_fsettle/')
THETA = 0.001
H = 1_800_000
FEE = 10.0
SUB_LEAD = 90_000           # subscribe this long before settlement (the pre60 leg needs trades from s-60 s)
PRE_X = 60_000              # pre60 leg entry: first -d entry-side trade at/after s - PRE_X
EVENT_TIMEOUT = H + 600_000  # give up on an event 40 min after settlement
VARIANTS = {'rule': (250, 200), 'nostop': (250, 0), 'L0': (0, 200), 'L1s': (1000, 200), 'L5s': (5000, 200)}
PATH = [1000, 5000, 30000, 60000, 300000, 1800000]

log = logging.getLogger('fsettle')


def append_row(fn, row):
    """Append a CSV row; if the row has columns the file lacks (new variant), rewrite the file with the wider header."""
    if os.path.exists(fn):
        with open(fn, newline='') as f:
            old = list(csv.DictReader(f))
            f.seek(0)
            header = next(csv.reader(f), [])
        if [k for k in row if k not in header]:
            header = header + [k for k in row if k not in header]
            with open(fn + '.tmp', 'w', newline='') as f:
                w = csv.DictWriter(f, fieldnames=header)
                w.writeheader()
                w.writerows(old)
            os.replace(fn + '.tmp', fn)
    else:
        header = list(row)
        with open(fn, 'w', newline='') as f:
            csv.DictWriter(f, fieldnames=header).writeheader()
    with open(fn, 'a', newline='') as f:
        csv.DictWriter(f, fieldnames=header, restval='').writerow(row)


class Variant:
    def __init__(self, L, stop):
        self.L, self.stop = L, stop
        self.state, self.en, self.ex, self.stopped = 'wait', None, None, False

    def on_trade(self, ev, t, p, entry_side):
        if self.state == 'wait':
            if t >= ev.s + self.L and entry_side:
                self.en, self.state = p, 'open'
            return
        if self.state == 'open':
            if t >= ev.s + H:
                self.state = 'closing'
            elif self.stop and ev.d * (p / self.en - 1) * 1e4 <= -self.stop:
                self.state, self.stopped = 'closing', True
        if self.state == 'closing' and not entry_side:
            self.ex, self.state = p, 'done'

    def net(self, d):
        return d * (self.ex / self.en - 1) * 1e4 - FEE if self.state == 'done' else None


class Event:
    def __init__(self, sym, s, f_prev, warm_ok):
        self.sym, self.s, self.f_prev = sym, s, f_prev
        self.d = 1 if f_prev > 0 else -1
        self.var = {k: Variant(*v) for k, v in VARIANTS.items()}
        self.ref = self.last_p = None
        self.path = {}
        self.lags = []
        self.n_trades = 0
        self.gap = 0
        self.last_a = None
        self.warm_ok = warm_ok
        self.pre_ok = int(time.time() * 1000) <= s - PRE_X - 5000     # armed in time to see trades from s-60 s
        self.pre_en = self.f_s = None
        self.f_s_fresh = 0

    def on_trade(self, msg, recv_ms):
        t, p, m, a = msg['T'], float(msg['p']), msg['m'], msg['a']
        if self.last_a is not None and a > self.last_a + 1:
            self.gap += a - self.last_a - 1
        self.last_a = a
        entry_side = m if self.d < 0 else not m      # short enters on sell-aggressor trades (buyer is maker)
        if t < self.s:
            if self.pre_en is None and t >= self.s - PRE_X and not entry_side:   # pre60 leg (-d) buys at the ask for a short event
                self.pre_en = p
            self.ref = self.last_p = p
            return
        for off in PATH:
            if off not in self.path and t > self.s + off and self.ref:
                self.path[off] = self.d * (self.last_p / self.ref - 1) * 1e4
        if t <= self.s + 5000 and len(self.lags) < 500:
            self.lags.append(recv_ms - t)
        self.n_trades += 1
        for v in self.var.values():
            v.on_trade(self, t, p, entry_side)
        self.last_p = p

    def done(self):
        return all(v.state == 'done' for v in self.var.values())


class Tracker:
    def __init__(self):
        os.makedirs(OUT, exist_ok=True)
        self.r, self.T, self.lastE = {}, {}, {}
        self.f_prev = {}             # sym -> (settlement time, rate) of the most recent settlement seen
        self.events = {}             # (sym, s) -> Event
        self.subs = set()
        self.trade_ws = None
        self.sub_queue = asyncio.Queue()
        self.n_written = 0
        self.rule_nets = []

    # ---- markPrice stream: learn settled rates, schedule events ----
    def on_mark(self, items):
        now = 0
        for x in items:
            s, r, T, E = x['s'], float(x['r']), int(x['T']), int(x['E'])
            now = max(now, E)
            if s in self.T and T > self.T[s] and self.T[s] > 0:
                settled_at = self.T[s]
                fresh = settled_at - self.lastE.get(s, 0) <= 5000     # last r seen within 5 s of settlement
                self.f_prev[s] = (settled_at, self.r[s], fresh)
                self.write_settlement(s, settled_at, self.r[s], fresh)
                ev = self.events.get((s, settled_at))
                if ev is not None:
                    ev.f_s, ev.f_s_fresh = self.r[s], int(fresh)
            self.r[s], self.T[s], self.lastE[s] = r, T, E
        for s, T in self.T.items():
            fp = self.f_prev.get(s)
            if fp is None or abs(fp[1]) < THETA or (s, T) in self.events:
                continue
            if 0 < T - now <= SUB_LEAD:
                self.events[(s, T)] = Event(s, T, fp[1], fp[2])
                self.subscribe(s)
                log.info('event armed %s s=%s f_prev=%.4f%%', s, time.strftime('%H:%M', time.gmtime(T / 1000)), fp[1] * 100)
        # expire events
        for key, ev in list(self.events.items()):
            if now > ev.s + EVENT_TIMEOUT:
                self.finish(key, complete=False)

    def subscribe(self, sym):
        st = sym.lower() + '@aggTrade'
        if st not in self.subs:
            self.subs.add(st)
            self.sub_queue.put_nowait(('SUBSCRIBE', st))

    def unsubscribe_if_idle(self, sym):
        if any(k[0] == sym for k in self.events):
            return
        st = sym.lower() + '@aggTrade'
        if st in self.subs:
            self.subs.discard(st)
            self.sub_queue.put_nowait(('UNSUBSCRIBE', st))

    # ---- trade stream ----
    def on_trade(self, msg):
        recv = int(time.time() * 1000)
        sym = msg.get('s')
        for key, ev in list(self.events.items()):
            if key[0] == sym:
                ev.on_trade(msg, recv)
                if ev.done():
                    self.finish(key, complete=True)

    def mark_gap_all(self):
        for ev in self.events.values():
            if ev.s - 5000 <= time.time() * 1000:
                ev.gap += 10**6      # reconnect during an active window: flag as incomplete tape

    # ---- output ----
    def write_settlement(self, sym, s, rate, fresh):
        fn = OUT + 'settlements.csv'
        new = not os.path.exists(fn)
        with open(fn, 'a', newline='') as f:
            w = csv.writer(f)
            if new:
                w.writerow(['sym', 's_utc', 's_ms', 'rate', 'fresh'])
            w.writerow([sym, time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(s / 1000)), s, rate, int(fresh)])

    def finish(self, key, complete):
        ev = self.events.pop(key)
        same_s = sorted([e for e in self.events.values() if e.s == ev.s] + [ev], key=lambda e: -abs(e.f_prev))
        row = {'sym': ev.sym, 's_utc': time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(ev.s / 1000)), 's_ms': ev.s,
               'd': ev.d, 'f_prev': ev.f_prev, 'f_prev_fresh': int(ev.warm_ok), 'complete': int(complete),
               'gap_trades': ev.gap, 'n_trades': ev.n_trades, 'concurrent_armed': len(same_s),
               'lag_med_ms': statistics.median(ev.lags) if ev.lags else '', 'lag_max_ms': max(ev.lags) if ev.lags else ''}
        for k, v in ev.var.items():
            n = v.net(ev.d)
            row[f'net_{k}'] = '' if n is None else round(n, 2)
        rv = ev.var['rule']
        row.update({'entry': rv.en or '', 'exit': rv.ex or '', 'stopped': int(rv.stopped)})
        pre_px = -ev.d * (rv.en / ev.pre_en - 1) * 1e4 if ev.pre_en and rv.en else None
        pre_fund = ev.d * ev.f_s * 1e4 if ev.f_s is not None else None
        ok = pre_px is not None and pre_fund is not None and ev.pre_ok
        row.update({'pre_ok': int(ev.pre_ok), 'pre_entry': ev.pre_en or '', 'f_s': '' if ev.f_s is None else ev.f_s,
                    'f_s_fresh': ev.f_s_fresh, 'pre_px': '' if pre_px is None else round(pre_px, 2),
                    'pre_fund': '' if pre_fund is None else round(pre_fund, 2),
                    'net_pre60': round(pre_px + pre_fund - FEE, 2) if ok else ''})
        for off in PATH:
            row[f'path_{off // 1000}s'] = round(ev.path[off], 2) if off in ev.path else ''
        append_row(OUT + 'events.csv', row)
        self.n_written += 1
        if row['net_rule'] != '':
            self.rule_nets.append(row['net_rule'])
        log.info('event done %s %s complete=%d rule=%s stopped=%d gap=%d', ev.sym, row['s_utc'], complete,
                 row['net_rule'], row['stopped'], ev.gap)
        self.unsubscribe_if_idle(ev.sym)

    # ---- connections ----
    async def mark_loop(self):
        while True:
            try:
                async with websockets.connect(BASE + '/!markPrice@arr@1s', max_size=None, open_timeout=10) as ws:
                    log.info('markPrice connected')
                    async for raw in ws:
                        self.on_mark(json.loads(raw))
            except Exception as e:
                log.warning('markPrice stream error %s: %s; reconnecting', type(e).__name__, e)
                await asyncio.sleep(5)

    async def trade_loop(self):
        while True:
            try:
                async with websockets.connect(BASE, max_size=None, open_timeout=10) as ws:
                    self.trade_ws = ws
                    log.info('trade stream connected; resubscribing %d', len(self.subs))
                    if self.subs:
                        await ws.send(json.dumps({'method': 'SUBSCRIBE', 'params': sorted(self.subs), 'id': 1}))
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg.get('e') == 'aggTrade':
                            self.on_trade(msg)
            except Exception as e:
                log.warning('trade stream error %s: %s; reconnecting', type(e).__name__, e)
                self.trade_ws = None
                self.mark_gap_all()
                await asyncio.sleep(2)

    async def sub_loop(self):
        n = 10
        while True:
            method, st = await self.sub_queue.get()
            batch = [(method, st)]
            while not self.sub_queue.empty() and len(batch) < 50:
                batch.append(self.sub_queue.get_nowait())
            for meth in ('SUBSCRIBE', 'UNSUBSCRIBE'):
                params = [b[1] for b in batch if b[0] == meth]
                if params and self.trade_ws is not None:
                    n += 1
                    try:
                        await self.trade_ws.send(json.dumps({'method': meth, 'params': params, 'id': n}))
                    except Exception as e:
                        log.warning('sub send failed: %s', e)
            await asyncio.sleep(0.25)    # stays well under 10 messages/s per connection

    async def heartbeat(self):
        while True:
            await asyncio.sleep(3600)
            warm = sum(1 for v in self.f_prev.values() if abs(v[1]) >= THETA)
            nets = self.rule_nets
            log.info('heartbeat: symbols %d, warm %d, qualifying now %d, active events %d, written %d, rule mean %s',
                     len(self.T), len(self.f_prev), warm, len(self.events), self.n_written,
                     round(sum(nets) / len(nets), 2) if nets else '-')

    def load_state(self):
        fn = OUT + 'fprev_state.json'
        try:
            st = json.load(open(fn))
        except (OSError, ValueError):
            return
        now = int(time.time() * 1000)
        for sym, (settled_at, rate, fresh, nxt) in st.items():
            if nxt > now:                                  # no settlement since it was saved: still the latest rate
                self.f_prev[sym] = (settled_at, rate, fresh)
        log.info('reloaded %d settled rates from %s', len(self.f_prev), fn)

    async def save_state_loop(self):
        fn = OUT + 'fprev_state.json'
        while True:
            await asyncio.sleep(30)
            st = {sym: [v[0], v[1], v[2], self.T.get(sym, 0)] for sym, v in self.f_prev.items() if self.T.get(sym, 0) > v[0]}
            with open(fn + '.tmp', 'w') as f:
                json.dump(st, f)
            os.replace(fn + '.tmp', fn)

    async def run(self):
        self.load_state()
        await asyncio.gather(self.mark_loop(), self.trade_loop(), self.sub_loop(), self.heartbeat(), self.save_state_loop())


if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                        handlers=[logging.FileHandler(OUT + 'tracker.log')])
    log.info('fsettle paper tracker starting (public market data only)')
    asyncio.run(Tracker().run())

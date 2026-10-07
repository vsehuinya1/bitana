"""Funding-settlement LIVE TEST engine (owner-approved test 2026-10-06; evidence: reports/scalping_prereg.md C / C-X / C-X-tick).

Rule: at settlement s of a crypto USDT perp whose PREVIOUS settled funding |f_prev| >= theta, trade d = sign(f_prev)
(mostly short: negative funding). Entry: IOC LIMIT at s + entry_delay_ms (Binance clock), price capped ioc_cap_bps
beyond the last trade. Protection: STOP_MARKET reduce-only stop_bps adverse (algo endpoint, last-trade trigger), placed
right after the fill; if it cannot be placed the position is closed at once. Exit: MARKET reduce-only at s + hold_min.

Modes (config.yaml): dry = no account side effects, every order simulated on the live trade stream; live = real
orders, only after the owner's "go". Keys (separate sub-account) are read from keys_env, never logged, and refused if
identical to the WD engine's keys. When keys appear, a read-only key check runs and its result goes to Telegram.
Telegram: key check, errors, halts, daily summary. Market data: public websocket (/market/ws route).
"""
from __future__ import annotations

import asyncio
import calendar
import hashlib
import hmac
import json
import logging
import math
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

import aiohttp
import websockets
import yaml

CFG_PATH = os.environ.get('FSETTLE_CFG', '/root/bitana/fsettle_live/config.yaml')
FAPI = 'https://fapi.binance.com'
SAPI = 'https://api.binance.com'
WS = 'wss://fstream.binance.com/market/ws'
log = logging.getLogger('fsettle_live')


def now_ms() -> int:
    return int(time.time() * 1000)


def read_env(path: str, key: str) -> str | None:
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line.startswith(key + '='):
                    return line.split('=', 1)[1].strip().strip('"').strip("'")
    except OSError:
        return None
    return None


def q_round(x: float, step: str, up: bool) -> Decimal:
    st = Decimal(step)
    v = (Decimal(str(round(x, 12))) / st).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR) * st
    return v.normalize() if v != 0 else v


def fmt(d: Decimal) -> str:
    s = format(d, 'f')
    return s.rstrip('0').rstrip('.') if '.' in s else s


# ------------------------------------------------------------------------------------------------------- REST
class Rest:
    """Minimal signed client. Weight-aware logging only (the bots share this IP)."""

    def __init__(self, key: str | None, secret: str | None):
        self.key, self.secret = key, secret
        self.s: aiohttp.ClientSession | None = None
        self.offset = 0          # Binance ms − local ms
        self.unc = 10**6         # offset uncertainty (half the best round trip)
        self.used_weight = 0

    async def start(self):
        h = {'X-MBX-APIKEY': self.key} if self.key else {}
        self.s = aiohttp.ClientSession(headers=h, timeout=aiohttp.ClientTimeout(total=10),
                                       connector=aiohttp.TCPConnector(keepalive_timeout=120))

    async def close(self):
        if self.s:
            await self.s.close()

    async def req(self, method: str, path: str, params: dict | None = None, signed: bool = False, base: str = FAPI):
        p = dict(params or {})
        if signed:
            p['timestamp'] = now_ms() + self.offset
            p['recvWindow'] = 5000
            qs = urllib.parse.urlencode(p)
            p['signature'] = hmac.new(self.secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
        url = base + path
        try:
            async with self.s.request(method, url, params=p if method in ('GET', 'DELETE') else None,
                                      data=p if method not in ('GET', 'DELETE') else None) as r:
                w = r.headers.get('X-MBX-USED-WEIGHT-1M')
                if w:
                    self.used_weight = int(w)
                    if self.used_weight > 1500:
                        log.warning('IP weight high: %s/2400 (shared with the live bots)', w)
                try:
                    return await r.json(content_type=None)
                except Exception:
                    return {'code': r.status, 'msg': (await r.text())[:200]}
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            return {'code': -1, 'msg': f'{type(e).__name__}: {e}'}

    async def sync_clock(self, n: int = 5) -> tuple[int, int]:
        best = None
        for _ in range(n):
            t0 = time.time() * 1000
            r = await self.req('GET', '/fapi/v1/time')
            t1 = time.time() * 1000
            if isinstance(r, dict) and 'serverTime' in r:
                rtt = t1 - t0
                if best is None or rtt < best[0]:
                    best = (rtt, int(r['serverTime'] - (t0 + rtt / 2)))
        if best:
            self.unc, self.offset = int(best[0] / 2) + 1, best[1]
        return self.offset, self.unc


def err(r) -> tuple[int | None, str]:
    if isinstance(r, dict) and 'code' in r and str(r.get('code')) not in ('200', '0'):
        try:
            return int(r['code']), str(r.get('msg', ''))
        except (TypeError, ValueError):
            return -1, str(r)
    return None, ''


# ------------------------------------------------------------------------------------------------------- model
@dataclass
class Spec:
    tick: str
    step: str
    min_notional: float


@dataclass
class Sim:
    """Rule simulated on the trade tape (paper = uncapped, dry = with the IOC cap). Same logic as the paper tracker."""
    delay: int
    stop: float
    cap_bps: float | None
    state: str = 'wait'
    en: float | None = None
    ex: float | None = None
    stopped: bool = False
    ref: float | None = None

    def on_trade(self, s: int, d: int, H: int, t: int, p: float, m: bool):
        entry_side = m if d < 0 else not m
        if self.state == 'wait':
            if t < s:
                self.ref = p
                return
            if t >= s + self.delay and entry_side:
                if self.cap_bps is not None and self.ref and d * (p / self.ref - 1) * 1e4 > self.cap_bps:   # past the cap
                    self.state = 'nofill'          # IOC would not fill beyond the cap
                    return
                self.en, self.state = p, 'open'
            return
        if self.state == 'open':
            if t >= s + H:
                self.state = 'closing'
            elif d * (p / self.en - 1) * 1e4 <= -self.stop:
                self.state, self.stopped = 'closing', True
        if self.state == 'closing' and not entry_side:
            self.ex, self.state = p, 'done'

    def gross_bps(self, d: int) -> float | None:
        return d * (self.ex / self.en - 1) * 1e4 if self.state == 'done' else None


@dataclass
class Ev:
    sym: str
    s: int
    f_prev: float
    d: int
    rank: int = 0
    selected: bool = False
    skip: str = ''
    last_p: float | None = None
    paper: Sim | None = None
    dry: Sim | None = None
    row: dict = field(default_factory=dict)


# ------------------------------------------------------------------------------------------------------- engine
class Engine:
    def __init__(self, cfg_path: str = CFG_PATH):
        self.cfg_path = cfg_path
        self.cfg = self.load_cfg()
        self.data = self.cfg['data_dir']
        os.makedirs(self.data, exist_ok=True)
        self.key = read_env(self.cfg['keys_env'], 'FS_API_KEY')
        self.secret = read_env(self.cfg['keys_env'], 'FS_API_SECRET')
        self.rest = Rest(self.key, self.secret)
        self.spec: dict[str, Spec] = {}
        self.r, self.T, self.lastE = {}, {}, {}
        self.f_prev: dict[str, tuple[int, float]] = {}
        self.events: dict[tuple[str, int], Ev] = {}
        self.subs: set[str] = set()
        self.trade_ws = None
        self.subq: asyncio.Queue = asyncio.Queue()
        self.prepared: set[str] = set()       # symbols with leverage/margin set (live)
        self.open_live: dict[str, dict] = {}   # sym -> open live trade (also persisted)
        self.consec_err = 0
        self.halted = ''
        self.day_pnl: dict[str, float] = {}
        self.n_live = 0
        self.keycheck_done = False
        self.keys_ok = False
        self.wallet = None
        self.tasks: set = set()
        self.state_file = os.path.join(self.data, 'state.json')
        self.load_state()

    # ---- config / state -------------------------------------------------------------------------------------
    def load_cfg(self) -> dict:
        with open(self.cfg_path) as f:
            return yaml.safe_load(f)

    @property
    def live(self) -> bool:
        return self.cfg.get('mode') == 'live'

    def load_state(self):
        try:
            with open(self.state_file) as f:
                st = json.load(f)
            self.open_live = st.get('open_live', {})
            self.day_pnl = st.get('day_pnl', {})
            self.n_live = st.get('n_live', 0)
        except (OSError, ValueError):
            pass

    def save_state(self):
        tmp = self.state_file + '.tmp'
        with open(tmp, 'w') as f:
            json.dump({'open_live': self.open_live, 'day_pnl': self.day_pnl, 'n_live': self.n_live}, f)
        os.replace(tmp, self.state_file)

    # ---- telegram -------------------------------------------------------------------------------------------
    async def tg(self, text: str):
        log.info('TG: %s', text.replace('\n', ' | '))
        if not self.cfg.get('telegram'):
            return
        tok = read_env(self.cfg['telegram_env'], 'TELEGRAM_BOT_TOKEN')
        chat = read_env(self.cfg['telegram_env'], 'TELEGRAM_CHAT_ID')
        if not tok or not chat:
            return
        data = urllib.parse.urlencode({'chat_id': chat, 'text': 'FSETTLE ' + text[:3500]}).encode()

        def send():
            try:
                urllib.request.urlopen(f'https://api.telegram.org/bot{tok}/sendMessage', data=data, timeout=15).read()
            except Exception as e:                      # never log the URL (it carries the token)
                log.warning('telegram send failed: %s', type(e).__name__)
        await asyncio.get_running_loop().run_in_executor(None, send)

    # ---- startup: symbols + funding bootstrap ---------------------------------------------------------------
    async def load_specs(self):
        info = await self.rest.req('GET', '/fapi/v1/exchangeInfo')
        spec = {}
        for s in (info or {}).get('symbols', []):
            if (s.get('underlyingType') == 'COIN' and s['symbol'].endswith('USDT') and s.get('status') == 'TRADING'
                    and s.get('contractType') == 'PERPETUAL'):
                f = {x['filterType']: x for x in s['filters']}
                spec[s['symbol']] = Spec(f['PRICE_FILTER']['tickSize'], f['LOT_SIZE']['stepSize'],
                                         float(f['MIN_NOTIONAL']['notional']))
        if spec:
            self.spec = spec
        log.info('specs: %d crypto USDT perps', len(self.spec))

    async def bootstrap_funding(self):
        st, n = now_ms() - 9 * 3600 * 1000, 0
        for _ in range(10):
            r = await self.rest.req('GET', '/fapi/v1/fundingRate', {'startTime': st, 'limit': 1000})
            if not isinstance(r, list) or not r:
                break
            for x in r:
                t = int(x['fundingTime']) // 1000 * 1000
                if x['symbol'] not in self.f_prev or t > self.f_prev[x['symbol']][0]:
                    self.f_prev[x['symbol']] = (t, float(x['fundingRate']))
            n += len(r)
            if len(r) < 1000:
                break
            st = int(r[-1]['fundingTime']) + 1
        log.info('funding bootstrap: %d rows, %d symbols', n, len(self.f_prev))

    # ---- key check (read-only) ------------------------------------------------------------------------------
    async def key_check(self) -> list[str]:
        probs = []
        wd = read_env(self.cfg['wd_keys_env'], 'WD_API_KEY')
        if wd and wd == self.key:
            return ['these are the WD engine keys; the test needs its own sub-account']
        a = await self.rest.req('GET', '/fapi/v2/account', signed=True)
        c, m = err(a)
        if c is not None:
            return [f'futures account read failed: {c} {m}']
        bal = float(a.get('totalWalletBalance', 0))
        if not a.get('canTrade'):
            probs.append('canTrade is false')
        open_pos = [p['symbol'] for p in a.get('positions', []) if float(p.get('positionAmt', 0)) != 0]
        if open_pos and not self.open_live:
            probs.append(f'unexpected open positions: {open_pos}')
        dual = await self.rest.req('GET', '/fapi/v1/positionSide/dual', signed=True)
        if isinstance(dual, dict) and dual.get('dualSidePosition'):
            probs.append('Hedge Mode is on; needs One-way Mode')
        rs = await self.rest.req('GET', '/sapi/v1/account/apiRestrictions', signed=True, base=SAPI)
        if isinstance(rs, dict) and 'ipRestrict' in rs:
            if not rs.get('ipRestrict'):
                probs.append('key is not IP-restricted')
            if not rs.get('enableFutures'):
                probs.append('futures permission off')
            for k in ('enableWithdrawals', 'enableInternalTransfer', 'permitsUniversalTransfer'):
                if rs.get(k):
                    probs.append(f'{k} is ON (should be off)')
        else:
            probs.append(f'cannot read key restrictions: {err(rs)}')
        t = await self.rest.req('POST', '/fapi/v1/order/test',
                                {'symbol': 'BTCUSDT', 'side': 'BUY', 'type': 'MARKET', 'quantity': '0.001'}, signed=True)
        c, m = err(t)
        if c is not None:
            probs.append(f'order permission test failed: {c} {m}')
        self.wallet = bal
        return probs

    async def key_watch(self):
        """Wait for the owner's keys; when they appear, run the read-only check once and report."""
        while True:
            if not self.keycheck_done:
                k = read_env(self.cfg['keys_env'], 'FS_API_KEY')
                s = read_env(self.cfg['keys_env'], 'FS_API_SECRET')
                if k and s:
                    if (k, s) != (self.key, self.secret):
                        self.key, self.secret = k, s
                        await self.rest.close()
                        self.rest = Rest(k, s)
                        await self.rest.start()
                        await self.rest.sync_clock()
                    probs = await self.key_check()
                    self.keycheck_done = True
                    self.keys_ok = not probs
                    with open(os.path.join(self.data, 'keycheck.json'), 'w') as f:
                        json.dump({'utc': time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime()), 'problems': probs,
                                   'wallet': getattr(self, 'wallet', None), 'mode': self.cfg['mode']}, f)
                    if probs:
                        await self.tg('key check FAILED:\n- ' + '\n- '.join(probs))
                    else:
                        tail = ('LIVE: trading enabled.' if self.live else 'Mode: dry. Waiting for the owner\'s go.')
                        await self.tg(f'key check OK: wallet {self.wallet:.2f} USDT, one-way mode, IP-restricted, '
                                      f'no withdrawals. {tail}')
            await asyncio.sleep(60)

    # ---- market data ----------------------------------------------------------------------------------------
    def on_mark(self, items: list):
        now = 0
        for x in items:
            s, r, T, E = x['s'], float(x['r']), int(x['T']), int(x['E'])
            now = max(now, E)
            if s in self.T and T > self.T[s] > 0:
                self.f_prev[s] = (self.T[s], self.r[s])
            self.r[s], self.T[s], self.lastE[s] = r, T, E
        for s, T in self.T.items():
            fp = self.f_prev.get(s)
            if s not in self.spec or fp is None or abs(fp[1]) < self.cfg['theta'] or (s, T) in self.events:
                continue
            if fp[0] >= T:            # the "previous" settlement must be before this one
                continue
            if 0 < T - now <= 60_000:
                ev = Ev(s, T, fp[1], 1 if fp[1] > 0 else -1)
                H = self.cfg['hold_min'] * 60_000
                ev.paper = Sim(self.cfg['entry_delay_ms'], self.cfg['stop_bps'], None)
                # dry: the order reaches Binance ~one-way latency (≈ clock uncertainty) after it is sent
                ev.dry = Sim(self.cfg['entry_delay_ms'] + min(self.rest.unc, 500), self.cfg['stop_bps'], self.cfg['ioc_cap_bps'])
                self.events[(s, T)] = ev
                self.subscribe(s)
        for T in sorted({k[1] for k, e in self.events.items() if not e.row and not e.selected and not e.skip}):
            if T - now <= 55_000:
                self.select(T)

    def subscribe(self, sym):
        st = sym.lower() + '@aggTrade'
        if st not in self.subs:
            self.subs.add(st)
            self.subq.put_nowait(('SUBSCRIBE', st))

    def unsubscribe_if_idle(self, sym):
        if any(k[0] == sym for k in self.events) or sym in self.open_live:
            return
        st = sym.lower() + '@aggTrade'
        if st in self.subs:
            self.subs.discard(st)
            self.subq.put_nowait(('UNSUBSCRIBE', st))

    def on_trade(self, msg: dict):
        sym, t, p, m = msg['s'], int(msg['T']), float(msg['p']), bool(msg['m'])
        H = self.cfg['hold_min'] * 60_000
        for (s_, T), ev in list(self.events.items()):
            if s_ != sym:
                continue
            ev.last_p = p
            ev.paper.on_trade(T, ev.d, H, t, p, m)
            ev.dry.on_trade(T, ev.d, H, t, p, m)

    # ---- selection + limits ---------------------------------------------------------------------------------
    def entry_block_reason(self) -> str:
        c = self.cfg
        if not c.get('enabled', True):
            return 'disabled in config'
        if self.halted:
            return 'halted: ' + self.halted
        day = time.strftime('%Y-%m-%d', time.gmtime())
        if self.day_pnl.get(day, 0.0) <= -c['daily_loss_cap_usd']:
            return f'daily loss cap hit ({self.day_pnl[day]:.2f} USD)'
        if self.live:
            if not self.keys_ok:
                return 'live mode but keys not verified'
            if self.wallet is not None and self.wallet < c['equity_floor_usd']:
                return f'wallet {self.wallet:.2f} below floor {c["equity_floor_usd"]}'
            if self.n_live >= c['max_trades']:
                return 'test complete (max trades)'
            if c.get('go_live_utc'):
                start = calendar.timegm(time.strptime(c['go_live_utc'], '%Y-%m-%d %H:%M'))
                if time.time() > start + c['test_days'] * 86400:
                    return 'test complete (days)'
        return ''

    def spawn(self, coro):
        t = asyncio.get_running_loop().create_task(coro)
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)

    def select(self, T: int):
        evs = sorted([e for k, e in self.events.items() if k[1] == T], key=lambda e: -abs(e.f_prev))
        for i, e in enumerate(evs):
            e.rank = i + 1
        active = {e.sym for e in self.events.values() if e.selected} | set(self.open_live)
        slots = self.cfg['max_open'] - len(active)
        block = self.entry_block_reason()
        for e in evs:
            if e.selected or e.skip:
                continue                      # decided earlier for this settlement
            if block:
                e.skip = block
            elif e.sym in active:
                e.skip = 'coin already open'
            elif slots <= 0:
                e.skip = 'max_open reached'
            else:
                e.selected = True
                slots -= 1
                log.info('selected %s s=%s f_prev=%.4f%% rank %d [%s]', e.sym, time.strftime('%H:%M', time.gmtime(e.s / 1000)),
                         e.f_prev * 100, e.rank, self.cfg['mode'])
                self.spawn(self.run_event(e))
                continue
            self.spawn(self.finish_unselected(e))

    async def finish_unselected(self, e: Ev):
        H = self.cfg['hold_min'] * 60_000
        await self.sleep_until_exchange(e.s + H + 60_000)
        pg = e.paper.gross_bps(e.d)
        self.write_row(e, {'mode': self.cfg['mode'], 'selected': 0, 'skip': e.skip, 'paper_entry': e.paper.en,
                           'paper_net_bps': None if pg is None else round(pg - 10, 2)})
        self.events.pop((e.sym, e.s), None)
        self.unsubscribe_if_idle(e.sym)

    # ---- timing ---------------------------------------------------------------------------------------------
    async def loop_probe(self, row: dict, t0: int, t1: int):
        """Diagnostic: largest event-loop stall between t0 and t1 (Binance clock), to explain late order sends."""
        await self.sleep_until_exchange(t0)
        mx, at = 0, None
        while now_ms() + self.rest.offset < t1:
            a = now_ms()
            await asyncio.sleep(0.005)
            lag = now_ms() - a - 5
            if lag > mx:
                mx, at = lag, now_ms() + self.rest.offset - (t0 + 2000)
        row['loop_lag_max_ms'], row['loop_lag_at_ms'] = mx, at

    async def sleep_until_exchange(self, t_ex: int):
        while True:
            dt = t_ex - (now_ms() + self.rest.offset)
            if dt <= 0:
                return
            await asyncio.sleep(min(dt / 1000, 30) if dt > 30 else dt / 1000)

    # ---- one event ------------------------------------------------------------------------------------------
    async def run_event(self, e: Ev):
        c, H = self.cfg, self.cfg['hold_min'] * 60_000
        row = {'mode': c['mode'], 'selected': 1, 'skip': ''}
        try:
            if self.live:
                await self.live_trade(e, row)
            else:
                await self.sleep_until_exchange(e.s + H + 60_000)
                g = e.dry.gross_bps(e.d)
                row.update({'dry_state': e.dry.state, 'entry': e.dry.en, 'exit': e.dry.ex, 'stopped': int(e.dry.stopped),
                            'net_bps': None if g is None else round(g - 10, 2),
                            'pnl_usd': None if g is None else round((g - 10) / 1e4 * c['notional_usd'], 4)})
                # dry results are recorded but never booked against the live daily loss cap
        except Exception as ex:
            log.exception('event %s failed', e.sym)
            row['error'] = f'{type(ex).__name__}: {ex}'
            await self.on_error(f'{e.sym} event crashed: {type(ex).__name__}: {ex}')
        pg = e.paper.gross_bps(e.d)
        row['paper_entry'], row['paper_net_bps'] = e.paper.en, None if pg is None else round(pg - 10, 2)
        self.write_row(e, row)
        self.events.pop((e.sym, e.s), None)
        self.unsubscribe_if_idle(e.sym)

    async def live_trade(self, e: Ev, row: dict):
        c, H = self.cfg, self.cfg['hold_min'] * 60_000
        sp = self.spec[e.sym]
        side, close_side = ('SELL', 'BUY') if e.d < 0 else ('BUY', 'SELL')
        # 1) margin type + leverage (once per symbol per process)
        if e.sym not in self.prepared:
            r = await self.rest.req('POST', '/fapi/v1/marginType', {'symbol': e.sym, 'marginType': c['margin_type']}, signed=True)
            cc, m = err(r)
            if cc not in (None, -4046):
                row['skip'] = f'margin type {cc} {m}'
                return await self.on_error(f'{e.sym} margin type: {cc} {m}')
            r = await self.rest.req('POST', '/fapi/v1/leverage', {'symbol': e.sym, 'leverage': c['leverage']}, signed=True)
            cc, m = err(r)
            if cc is not None:
                row['skip'] = f'leverage {cc} {m}'
                return await self.on_error(f'{e.sym} leverage: {cc} {m}')
            self.prepared.add(e.sym)
        # 2) clock: refuse if the Binance offset is not known precisely enough (an early fill would pay the funding)
        await self.sleep_until_exchange(e.s - 8000)
        off, unc = await self.rest.sync_clock(5)
        row['clock_offset_ms'], row['clock_unc_ms'] = off, unc
        if c['entry_delay_ms'] - unc < c['min_send_margin_ms']:
            row['skip'] = f'clock uncertainty {unc} ms'
            log.warning('%s skipped: clock uncertainty %d ms', e.sym, unc)
            return
        # 3) order at s + delay (Binance clock), price from the last trade seen
        self.spawn(self.loop_probe(row, e.s - 2000, e.s + 1000))
        await self.sleep_until_exchange(e.s + c['entry_delay_ms'] - 3)
        woke = now_ms() + self.rest.offset - e.s
        ref = e.last_p
        if not ref:
            row['skip'] = 'no trade price'
            return
        cap = ref * (1 + e.d * c['ioc_cap_bps'] / 1e4)
        px = q_round(cap, sp.tick, up=e.d > 0)                 # buy cap rounded up, sell cap rounded down
        qty = q_round(c['notional_usd'] / ref, sp.step, up=True)
        notl = float(qty) * ref
        if notl < sp.min_notional * 1.02 or notl > c['max_notional_usd']:
            row['skip'] = f'notional {notl:.2f} outside [{sp.min_notional * 1.02:.2f}, {c["max_notional_usd"]}]'
            return
        cid = f'fs{e.sym[:12]}{e.s // 1000 % 10**8}'
        t_send = now_ms()
        r = await self.rest.req('POST', '/fapi/v1/order', {'symbol': e.sym, 'side': side, 'type': 'LIMIT',
                                                           'timeInForce': 'IOC', 'quantity': fmt(qty), 'price': fmt(px),
                                                           'newClientOrderId': cid, 'newOrderRespType': 'RESULT'}, signed=True)
        t_back = now_ms()
        cc, m = err(r)
        row.update({'ref': ref, 'cap_px': fmt(px), 'qty': fmt(qty), 'send_ex_ms': t_send + self.rest.offset - e.s,
                    'woke_ex_ms': woke, 'rtt_ms': t_back - t_send})
        if cc is not None:
            row['skip'] = f'entry rejected {cc} {m}'
            return await self.on_error(f'{e.sym} entry rejected: {cc} {m}')
        self.consec_err = 0
        filled = Decimal(str(r.get('executedQty', '0')))
        avg, known = await self.fill_price(e.sym, cid, r, filled, ref)
        upd = int(r.get('updateTime', 0) or 0)
        row.update({'status': r.get('status'), 'filled_qty': fmt(filled), 'entry': avg, 'fill_ms_after_s': upd - e.s})
        log.info('ENTRY %s %s %s qty %s @ %s (ref %s, cap %s) woke %d / sent %d / fill %d ms after s, rtt %d ms', e.sym, side,
                 r.get('status'), fmt(filled), avg, ref, fmt(px), woke, row['send_ex_ms'], upd - e.s, t_back - t_send)
        if filled <= 0:
            return
        self.n_live += 1
        if upd and upd < e.s:
            await self.tg(f'WARNING {e.sym}: entry filled {e.s - upd} ms BEFORE settlement (funding may be charged)')
        self.open_live[e.sym] = {'s': e.s, 'qty': fmt(filled), 'side': side, 'close_side': close_side,
                                 'stop_cid': None, 'exit_due': e.s + H, 'entry': avg}
        self.save_state()
        # 4) protective stop right away; if it cannot be placed, close now
        trig = q_round(avg * (1 - e.d * c['stop_bps'] / 1e4), sp.tick, up=e.d < 0)   # short: above entry
        scid = 's' + cid
        sr = await self.place_stop(e.sym, close_side, filled, trig, scid)
        cc, m = err(sr)
        if cc is not None:
            sr = await self.place_stop(e.sym, close_side, filled, trig, scid + 'b')
            cc2, m2 = err(sr)
            if cc2 is not None:
                row['stop'] = f'FAILED {cc} {m} / {cc2} {m2}'
                await self.tg(f'{e.sym}: stop could not be placed ({cc2} {m2}); closing at market now')
                await self.close_position(e.sym, row, reason='stop_failed')
                return
            scid += 'b'
        self.open_live[e.sym]['stop_cid'] = scid
        self.save_state()
        row['stop_trigger'] = fmt(trig)
        log.info('STOP placed %s %s trigger %s (%s)', e.sym, close_side, fmt(trig), scid)
        if not known:
            await self.correct_stop(e, row, sp, side, close_side, filled, r.get('orderId'), trig)
        # 5) time exit
        await self.sleep_until_exchange(e.s + H)
        await self.close_position(e.sym, row, reason='time')

    async def fill_price(self, sym, cid, r: dict, filled: Decimal, ref: float) -> tuple[float, bool]:
        """(average fill of the entry, known?). The IOC response can carry avgPrice 0 although FILLED (API3 and NMR
        2026-10-07): then cumQuote/qty, then the order queried by client id, and only as a last resort the reference
        price (the stop is then corrected from the account's fills, see correct_stop)."""
        avg = float(r.get('avgPrice', 0) or 0)
        if avg > 0 or filled <= 0:
            return avg, True
        cq = float(r.get('cumQuote', 0) or 0)
        if cq > 0:
            return cq / float(filled), True
        q = await self.rest.req('GET', '/fapi/v1/order', {'symbol': sym, 'origClientOrderId': cid}, signed=True)
        avg = float(q.get('avgPrice', 0) or 0) if isinstance(q, dict) else 0.0
        if avg > 0:
            return avg, True
        log.warning('%s: fill price unknown (order query: %s), stop based on the reference price %s', sym, str(q)[:300], ref)
        return ref, False

    async def entry_from_fills(self, sym, order_id, side, tries=5) -> float | None:
        """Average entry price from the account's fills of this order (they appear within a few hundred ms)."""
        for _ in range(tries):
            tr = await self.rest.req('GET', '/fapi/v1/userTrades', {'symbol': sym, 'orderId': order_id}, signed=True)
            fills = [x for x in tr if x.get('side') == side] if isinstance(tr, list) else []
            q = sum(float(x['qty']) for x in fills)
            if q > 0:
                return sum(float(x['price']) * float(x['qty']) for x in fills) / q
            await asyncio.sleep(0.5)
        return None

    async def correct_stop(self, e, row, sp, side, close_side, filled: Decimal, order_id, trig: Decimal):
        """The stop was placed from the reference price; move it to entry*(1 -/+ stop_bps) once the real fill is known.
        The new stop is placed before the old one is cancelled, so the position is never without a stop."""
        real = await self.entry_from_fills(e.sym, order_id, side) if order_id else None
        if real is None:
            await self.tg(f'{e.sym}: real entry price still unknown; stop stays at {fmt(trig)} (from the reference price)')
            return
        row['entry'] = real
        self.open_live[e.sym]['entry'] = real
        trig2 = q_round(real * (1 - e.d * self.cfg['stop_bps'] / 1e4), sp.tick, up=e.d < 0)
        if trig2 == trig:
            return
        old = self.open_live[e.sym]['stop_cid']
        new = old + 'r'
        sr = await self.place_stop(e.sym, close_side, filled, trig2, new)
        cc, m = err(sr)
        if cc is not None:
            log.warning('%s: corrected stop %s rejected (%s %s); keeping %s', e.sym, fmt(trig2), cc, m, fmt(trig))
            return
        self.open_live[e.sym]['stop_cid'] = new
        self.save_state()
        await self.rest.req('DELETE', '/fapi/v1/algoOrder', {'clientAlgoId': old}, signed=True)
        row['stop_trigger'] = fmt(trig2)
        log.info('STOP moved %s %s -> %s (real entry %s)', e.sym, fmt(trig), fmt(trig2), real)

    async def place_stop(self, sym, side, qty: Decimal, trig: Decimal, cid: str):
        return await self.rest.req('POST', '/fapi/v1/algoOrder', {
            'algoType': 'CONDITIONAL', 'symbol': sym, 'side': side, 'type': 'STOP_MARKET', 'quantity': fmt(qty),
            'triggerPrice': fmt(trig), 'workingType': 'CONTRACT_PRICE', 'reduceOnly': 'true',
            'clientAlgoId': cid, 'newOrderRespType': 'ACK'}, signed=True)

    async def position_amt(self, sym) -> float | None:
        r = await self.rest.req('GET', '/fapi/v2/positionRisk', {'symbol': sym}, signed=True)
        if isinstance(r, list):
            return sum(float(x.get('positionAmt', 0)) for x in r)
        return None

    async def close_position(self, sym: str, row: dict, reason: str):
        st = self.open_live.get(sym)
        if not st:
            return
        amt = await self.position_amt(sym)
        row['exit_reason'] = reason
        if amt is None:
            await self.on_error(f'{sym}: cannot read position at exit')
        elif amt != 0:
            q = Decimal(str(abs(amt)))
            r = await self.rest.req('POST', '/fapi/v1/order', {'symbol': sym, 'side': st['close_side'], 'type': 'MARKET',
                                                               'quantity': fmt(q), 'reduceOnly': 'true',
                                                               'newOrderRespType': 'RESULT'}, signed=True)
            cc, m = err(r)
            if cc is not None:
                await self.on_error(f'{sym}: exit order rejected {cc} {m}')
            else:
                row['exit'] = float(r.get('avgPrice', 0) or 0)
        else:
            row['exit_reason'] = 'stop' if reason == 'time' else reason
        if st.get('stop_cid'):
            await self.rest.req('DELETE', '/fapi/v1/algoOrder', {'clientAlgoId': st['stop_cid']}, signed=True)
        await asyncio.sleep(1)
        amt2 = await self.position_amt(sym)
        if amt2 not in (None, 0.0):
            await self.tg(f'ALERT {sym}: position still {amt2} after exit; retrying market close')
            await self.rest.req('POST', '/fapi/v1/order', {'symbol': sym, 'side': st['close_side'], 'type': 'MARKET',
                                                           'quantity': fmt(Decimal(str(abs(amt2)))), 'reduceOnly': 'true'},
                                signed=True)
        self.open_live.pop(sym, None)
        self.save_state()
        await asyncio.sleep(3)
        await self.settle_accounting(sym, st, row)
        log.info('EXIT %s reason %s exit %s pnl %s USD net %s bp funding %s', sym, row.get('exit_reason'), row.get('exit'),
                 row.get('pnl_usd'), row.get('net_bps'), row.get('funding_usd'))

    async def settle_accounting(self, sym: str, st: dict, row: dict):
        """Realized PnL + commission from account trades; funding charged around settlement (should be none)."""
        s = st['s']
        tr = await self.rest.req('GET', '/fapi/v1/userTrades', {'symbol': sym, 'startTime': s - 5000, 'endTime': now_ms() + self.rest.offset},
                                 signed=True)
        if isinstance(tr, list):
            pnl = sum(float(x.get('realizedPnl', 0)) for x in tr)
            fee = sum(float(x.get('commission', 0)) for x in tr if x.get('commissionAsset') == 'USDT')
            row.update({'realized_usd': round(pnl, 6), 'commission_usd': round(fee, 6), 'n_fills': len(tr)})
            ents = [x for x in tr if x.get('side') == st['side']]
            if ents:
                qe = sum(float(x['qty']) for x in ents)
                row['entry'] = sum(float(x['price']) * float(x['qty']) for x in ents) / qe
            exits = [x for x in tr if x.get('side') == st['close_side']]
            if exits:
                qx = sum(float(x['qty']) for x in exits)
                row['exit'] = sum(float(x['price']) * float(x['qty']) for x in exits) / qx
            net = pnl - fee
            row['pnl_usd'] = round(net, 6)
            self.book_pnl(net, s)
        inc = await self.rest.req('GET', '/fapi/v1/income', {'symbol': sym, 'incomeType': 'FUNDING_FEE',
                                                             'startTime': s - 120_000, 'endTime': s + 600_000}, signed=True)
        if isinstance(inc, list):
            row['funding_usd'] = round(sum(float(x.get('income', 0)) for x in inc), 6)
            if inc:
                await self.tg(f'NOTE {sym}: funding was charged/credited at settlement ({row["funding_usd"]} USDT)')
        if row.get('entry') and row.get('exit'):
            d = -1 if st['side'] == 'SELL' else 1
            row['net_bps'] = round(d * (row['exit'] / row['entry'] - 1) * 1e4 - 10, 2)

    def book_pnl(self, usd: float | None, s: int):
        if usd is None:
            return
        day = time.strftime('%Y-%m-%d', time.gmtime(s / 1000))
        self.day_pnl[day] = round(self.day_pnl.get(day, 0.0) + usd, 6)
        self.save_state()
        if self.day_pnl[day] <= -self.cfg['daily_loss_cap_usd']:
            self.spawn(self.tg(f'daily loss cap hit: {self.day_pnl[day]:.2f} USD; no new entries today'))

    async def on_error(self, msg: str):
        self.consec_err += 1
        log.error(msg)
        await self.tg('ERROR ' + msg)
        if self.consec_err >= self.cfg['max_consecutive_errors'] and not self.halted:
            self.halted = f'{self.consec_err} consecutive errors'
            await self.tg(f'HALTED new entries after {self.consec_err} consecutive errors; restart the unit to clear')

    # ---- output ---------------------------------------------------------------------------------------------
    def write_row(self, e: Ev, extra: dict):
        row = {'sym': e.sym, 's_utc': time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(e.s / 1000)), 's_ms': e.s, 'd': e.d,
               'f_prev': e.f_prev, 'rank': e.rank}
        row.update(extra)
        fn = os.path.join(self.data, 'events.jsonl')
        with open(fn, 'a') as f:
            f.write(json.dumps(row, default=str) + '\n')

    # ---- restart recovery (live) ----------------------------------------------------------------------------
    async def recover(self):
        if not self.live or not self.key:
            return
        for sym, st in list(self.open_live.items()):
            row = {'mode': 'live', 'recovered': 1}
            e = Ev(sym, st['s'], 0.0, -1 if st['side'] == 'SELL' else 1)
            if now_ms() + self.rest.offset >= st['exit_due']:
                await self.close_position(sym, row, reason='recovered_late')
            else:
                async def later(sym=sym, st=st, row=row, e=e):
                    await self.sleep_until_exchange(st['exit_due'])
                    await self.close_position(sym, row, reason='time')
                    self.write_row(e, row)
                self.spawn(later())
            if 'exit_reason' in row:
                self.write_row(e, row)
        a = await self.rest.req('GET', '/fapi/v2/positionRisk', signed=True)
        if isinstance(a, list):
            stray = [x['symbol'] for x in a if float(x.get('positionAmt', 0)) != 0 and x['symbol'] not in self.open_live]
            if stray:
                await self.tg(f'ALERT unknown open positions on the test sub-account: {stray}. Not touching them; '
                              f'halting new entries.')
                self.halted = 'unknown positions'

    # ---- loops ----------------------------------------------------------------------------------------------
    async def mark_loop(self):
        while True:
            try:
                async with websockets.connect(WS + '/!markPrice@arr@1s', max_size=None, open_timeout=10) as ws:
                    log.info('markPrice stream connected')
                    async for raw in ws:
                        self.on_mark(json.loads(raw))
            except Exception as ex:
                log.warning('markPrice stream: %s; reconnecting', type(ex).__name__)
                await asyncio.sleep(5)

    async def trade_loop(self):
        while True:
            try:
                async with websockets.connect(WS, max_size=None, open_timeout=10) as ws:
                    self.trade_ws = ws
                    log.info('trade stream connected (%d subs)', len(self.subs))
                    if self.subs:
                        await ws.send(json.dumps({'method': 'SUBSCRIBE', 'params': sorted(self.subs), 'id': 1}))
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg.get('e') == 'aggTrade':
                            self.on_trade(msg)
            except Exception as ex:
                log.warning('trade stream: %s; reconnecting', type(ex).__name__)
                self.trade_ws = None
                await asyncio.sleep(2)

    async def sub_loop(self):
        n = 10
        while True:
            meth, st = await self.subq.get()
            batch = [(meth, st)]
            while not self.subq.empty() and len(batch) < 50:
                batch.append(self.subq.get_nowait())
            for mth in ('SUBSCRIBE', 'UNSUBSCRIBE'):
                ps = [b[1] for b in batch if b[0] == mth]
                if ps and self.trade_ws is not None:
                    n += 1
                    try:
                        await self.trade_ws.send(json.dumps({'method': mth, 'params': ps, 'id': n}))
                    except Exception as ex:
                        log.warning('subscribe send failed: %s', type(ex).__name__)
            await asyncio.sleep(0.25)

    async def housekeeping(self):
        last_day = time.strftime('%Y-%m-%d', time.gmtime())
        while True:
            await asyncio.sleep(60)
            try:
                self.cfg = self.load_cfg()
            except Exception as ex:
                log.warning('config reload failed: %s', ex)
            if int(time.time()) % 600 < 60:
                await self.rest.sync_clock(3)
            if int(time.time()) % 21600 < 60:
                await self.load_specs()
            if self.live and self.keys_ok and int(time.time()) % 600 < 60:
                a = await self.rest.req('GET', '/fapi/v2/account', signed=True)
                if isinstance(a, dict) and 'totalWalletBalance' in a:
                    self.wallet = float(a['totalWalletBalance'])
            day = time.strftime('%Y-%m-%d', time.gmtime())
            if day != last_day:
                await self.daily_summary(last_day)
                last_day = day

    async def daily_summary(self, day: str):
        rows = []
        try:
            with open(os.path.join(self.data, 'events.jsonl')) as f:
                rows = [json.loads(l) for l in f if l.startswith('{')]
        except OSError:
            pass
        rows = [r for r in rows if r.get('s_utc', '').startswith(day)]
        sel = [r for r in rows if r.get('selected')]
        traded = [r for r in sel if r.get('net_bps') is not None]
        pnl = sum(r.get('pnl_usd') or 0 for r in traded)
        paper = [r['paper_net_bps'] for r in sel if r.get('paper_net_bps') is not None]
        nb = [r['net_bps'] for r in traded]
        lat = [r['fill_ms_after_s'] for r in traded if r.get('fill_ms_after_s') is not None]
        fund = [r for r in traded if r.get('funding_usd')]
        msg = (f'daily {day} [{self.cfg["mode"]}]: events {len(rows)}, selected {len(sel)}, traded {len(traded)}, '
               f'net {pnl:+.3f} USD, mean {sum(nb) / len(nb):+.1f} bp' if nb else
               f'daily {day} [{self.cfg["mode"]}]: events {len(rows)}, selected {len(sel)}, traded 0')
        if paper:
            msg += f'; paper same events {sum(paper) / len(paper):+.1f} bp'
        if lat:
            msg += f'; fill after settlement median {sorted(lat)[len(lat) // 2]} ms'
        if fund:
            msg += f'; FUNDING CHARGED on {len(fund)} trades'
        if self.halted:
            msg += f'; HALTED ({self.halted})'
        await self.tg(msg)

    async def run(self):
        await self.rest.start()
        off, unc = await self.rest.sync_clock()
        log.info('start: mode %s, clock offset %d ms ±%d, keys %s', self.cfg['mode'], off, unc,
                 'present' if self.key else 'absent')
        await self.load_specs()
        await self.bootstrap_funding()
        await self.recover()
        await asyncio.gather(self.mark_loop(), self.trade_loop(), self.sub_loop(), self.housekeeping(), self.key_watch())


def main():
    cfg = yaml.safe_load(open(CFG_PATH))
    os.makedirs(cfg['data_dir'], exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                        handlers=[logging.FileHandler(os.path.join(cfg['data_dir'], 'engine.log'))])
    log.info('fsettle_live starting')
    asyncio.run(Engine().run())


if __name__ == '__main__':
    main()

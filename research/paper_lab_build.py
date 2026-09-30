#!/usr/bin/env python3
"""Paper Lab snapshot for the dashboard's /paper tab (2026-09-26, owner request).

One JSON (dashboard/paper_lab.json) with the three paper systems, every trade and every non-trade:
  capitulation  PREREG-CAPITULATION-BASKET: forward events (20-coin / 5 majors / BTC), hourly breadth (non-trades,
                near-misses >= 50%), stats + verdict.
  breakout      PREREG-BREAKOUT-4H: every paper trade (entry, stop, exit, reason, R), skipped signals, per-coin watch
                (distance to the swing-high trigger, EMA200 filter), stats vs random longs, verdict, R curve.
  carry         PREREG-FUNDING-CARRY: per-coin trailing funding vs the entry bar, open/closed paper trades, verdict.
Readers of record do the maths; this file only collects. Public data only; rebuilt hourly by the risk watch
(fire-and-forget subprocess) or by hand: venv/bin/python research/paper_lab_build.py
"""
import json
import math
import os
import sys
import traceback
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.append('/root/bitana/research')
import capitulation_reader as capr  # noqa: E402
import breakout_4h_reader as bo4  # noqa: E402
import funding_carry_reader as fcr  # noqa: E402
import breakout_4h_vol_reader as bov  # noqa: E402
import wick_catcher_reader as wcr  # noqa: E402
import perp_discount_reader as pdr  # noqa: E402
import paper_klines as pk  # noqa: E402

OUT = '/root/bitana/dashboard/paper_lab.json'


def _j(x):
    """JSON-safe scalars."""
    if isinstance(x, (pd.Timestamp, datetime)):
        return x.isoformat()
    if isinstance(x, (np.floating, float)):
        return None if (x is None or not math.isfinite(float(x))) else round(float(x), 6)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    if isinstance(x, dict):
        return {k: _j(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_j(v) for v in x]
    return x


def capitulation(today):
    tr, breadth = capr.forward_read()
    rows = []
    for _, r in (tr.iterrows() if len(tr) else []):
        rows.append({'event_hour': r.event_bar, 'entry': r.entry_bar, 'exit': r.entry_bar + pd.Timedelta(hours=capr.HOLD_H),
                     'closed': pd.notna(r.basket_net), 'basket_net': r.basket_net, 'majors_net': r.majors_net,
                     'btc_net': r.btc_net, 'breadth': float(breadth.get(r.event_bar, np.nan))})
    done = tr.dropna(subset=['basket_net']) if len(tr) else tr
    s20 = capr.stats(done.basket_net) if len(done) else {'n': 0}
    s5 = capr.stats(done.majors_net) if len(done) else {'n': 0}
    b = breadth.dropna()
    last7 = b[b.index >= b.index[-1] - pd.Timedelta(days=7)] if len(b) else b
    near = b[(b >= 0.5) & (b.index >= capr.FORWARD_FROM - pd.Timedelta(hours=1))]
    return {'name': 'Capitulation basket', 'prereg': 'PREREG-CAPITULATION-BASKET', 'forward_from': capr.FORWARD_FROM,
            'rule': ('Event: >= 75% of 20 coins with hourly z <= -3 (vs trailing 30d sd), 24h cooldown. Buy the '
                     'equal-weight basket at the next hour open, hold 24h. 20 bps (20-coin), 15 bps (5 majors).'),
            'verdict': capr.decide(s20, {}, today) if s20.get('n', 0) < 12 else 'formal read due: run the reader',
            'stats_20': s20, 'stats_majors': s5, 'trades': rows,
            'breadth_7d': [{'t': t, 'v': float(v)} for t, v in last7.items()],
            'near_misses': [{'t': t, 'v': float(v)} for t, v in near[near < capr.BREADTH].items()],
            'last_hour': {'t': b.index[-1], 'v': float(b.iloc[-1])} if len(b) else None,
            'threshold': capr.BREADTH}


_FRAMES = {}


def _frames():
    if not _FRAMES:
        start = min(bo4.FORWARD_FROM, bov.FORWARD_FROM) - pd.Timedelta(days=150)
        _FRAMES.update({s: bo4.api_4h(s, start) for s in bo4.UNIVERSE})
    return _FRAMES


def breakout(today, vol=False):
    now = pd.Timestamp.now(tz='UTC')
    ff = bov.FORWARD_FROM if vol else bo4.FORWARD_FROM
    vm = bov.VOL_MULT if vol else None
    trades, skipped, watch, ctrl = [], [], [], []
    for s, df in _frames().items():
        if len(df) < bo4.EMA_SPAN + 50:
            continue
        for x in bo4.run(df, ff, detail=True, vol_mult=vm):
            x['sym'] = s.replace('USDT', '')
            (trades if x['kind'] == 'trade' else skipped).append(x)
        ctrl += [x for x in bo4.run(df, ff, every_bar=True) if x[2]]
        w = bo4.watch(df)
        if w:
            w['vol_ratio'] = float(df.v.iloc[-1] / df.v.iloc[-21:-1].mean()) if 'v' in df and df.v.iloc[-21:-1].mean() > 0 else None
            w['sym'] = s.replace('USDT', '')
            w['open_position'] = any(t['sym'] == w['sym'] and not t['closed'] for t in trades)
            watch.append(w)
    ctrl_E = float(np.mean([x[1] for x in ctrl])) if ctrl else None
    if trades:
        bo4.tag_funding(trades)
    closed = [t for t in trades if t['closed']]
    fc = [t for t in closed if t.get('fund') is not None and t['fund'] <= bo4.FUND_LINE_MAX]
    fund_line = bo4.summarize([(t['entry_time'], t['R'], True) for t in fc], ctrl_E if ctrl_E is not None else float('nan'),
                              ff, now.normalize() + pd.Timedelta(days=1)) if fc else {'n': 0}
    st = bo4.summarize([(t['entry_time'], t['R'], True) for t in closed], ctrl_E if ctrl_E is not None else float('nan'),
                       ff, now.normalize() + pd.Timedelta(days=1)) if closed else {'n': 0}
    curve, cum = [], 0.0
    for t in sorted(closed, key=lambda t: t['exit_time']):
        cum += t['R']; curve.append({'t': t['exit_time'], 'v': cum})
    watch.sort(key=lambda w: (not w['above_ema'], w['to_trigger_pct'] is None or w['to_trigger_pct'] <= 0, abs(w['to_trigger_pct'] or 1e9)))
    rule = ('4h close crosses above the last pivot-3 swing high while close > EMA200. Buy next open, stop 2x ATR14, '
            'exit on the stop or after 36 bars (6 days). 20 bps. One position per coin. Control: a long at '
            'every 4h open with the same exit.')
    if vol:
        plain = bo4.pooled(_frames(), str(ff), str(now.normalize() + pd.Timedelta(days=1)), entries_from=ff)
        verdict = bov.decide(st, plain, today)
        rule = rule.replace('while close > EMA200.', 'while close > EMA200 AND the bar\'s volume >= 1.5x its prior-20-bar mean.')
    else:
        plain, verdict = None, bo4.decide(st, today)
    return {'name': '4h breakout + volume' if vol else '4h breakout', 'prereg': 'PREREG-BREAKOUT-4H-VOL' if vol else 'PREREG-BREAKOUT-4H',
            'forward_from': ff, 'vol': vol, 'plain_same_window': plain,
            'rule': rule, 'verdict': verdict, 'stats': st, 'control_E': ctrl_E, 'fund_line': fund_line,
            'open_R': sum(t['R'] for t in trades if not t['closed']),
            'trades': sorted(trades, key=lambda t: t['entry_time'], reverse=True), 'skipped': skipped,
            'watch': watch, 'curve': curve}


def carry(today):
    r = fcr.read()
    cur = sorted(r['current'].items(), key=lambda x: -x[1])
    open_syms = {t['sym'] for t in r['open']}
    vopen = {t['sym'] for t in r['venue']['open']}
    watch = [{'sym': s.replace('USDT', ''), 'trailing_ann': v, 'gap_to_entry': fcr.ENTER - v, 'in_position': s in open_syms,
              'hl_trailing_ann': r['current_hl'].get(s), 'in_venue_book': s in vopen}
             for s, v in cur]
    def row(t):
        return {'sym': t['sym'].replace('USDT', ''), 'entry': t['entry'], 'exit': t['exit'], 'funding': t['funding'],
                'basis': t['basis'], 'net': t['net'], 'closed': t['exit'] is not None}
    return {'name': 'Funding carry', 'prereg': 'PREREG-FUNDING-CARRY', 'forward_from': fcr.FORWARD_FROM,
            'rule': ('Long spot + short perp, equal notional, when a coin\'s trailing-24h funding >= 15%/yr; exit < 3%/yr. '
                     'Act 1h after the settlement. 0.30% round trip. 20 slots, 1.25x capital per notional.'),
            'verdict': fcr.decide(r, today), 'enter': fcr.ENTER, 'exit': fcr.EXIT,
            'net_on_capital': r['net_on_capital'], 'ann_on_deployed': r['ann_on_deployed'], 'drag_share': r['drag_share'],
            'trades': [row(t) for t in r['open'] + r['closed']], 'watch': watch,
            'venue': {'net_on_capital': r['venue']['net_on_capital'], 'hl_trades': r['venue']['hl_trades'],
                      'funding_only_net': r['funding_only_net'],
                      'trades': [{'sym': t['sym'].replace('USDT', ''), 'venue': t['venue'], 'entry': t['entry'], 'exit': t['exit'],
                                  'funding': t['funding'], 'net': t['net'], 'closed': t['exit'] is not None}
                                 for t in r['venue']['open'] + r['venue']['closed']]}}


_PERP, _SPOT = {}, {}


def wick(today):
    rows, rows2, frames = wcr.read()
    _PERP.update(frames)
    wcr.tag_market(rows, wcr.market_selloff_hours())
    st, st2 = wcr.stats(rows), wcr.stats(rows2)
    st_m, st_c = wcr.stats([r for r in rows if r['mkt']]), wcr.stats([r for r in rows if not r['mkt']])
    spot = {s: pk.live(s, 'spot', wcr.FORWARD_FROM - pd.Timedelta(days=2)) for s in wcr.UNIVERSE}
    _SPOT.update(spot)
    sig = wcr.signature_lines(wcr.tag_signatures(rows, frames, spot))
    addons = sorted([dict(r['addon'], sym=r['sym'].replace('USDT', '')) for r in rows if r.get('addon')], key=lambda r: r['t'], reverse=True)
    watch, near = [], []
    for sym, df in frames.items():
        if len(df) < 400:
            continue
        H = df.resample('h').agg({'h': 'max', 'l': 'min', 'c': 'last'}).dropna()
        tr = np.maximum(H.h, H.c.shift(1)) - np.minimum(H.l, H.c.shift(1)); atr = tr.rolling(14).mean()
        a_, c_ = atr.iloc[-1], H.c.iloc[-1]
        lvl = c_ - wcr.K * a_
        watch.append({'sym': sym.replace('USDT', ''), 'close': float(c_), 'atr_pct': float(a_ / c_ * 100), 'level': float(lvl),
                      'dist_pct': float(lvl / c_ - 1) * 100, 'level8': float(c_ - wcr.K2 * a_), 'bar': H.index[-1] + pd.Timedelta(hours=1)})
        for i in range(max(15, len(H) - 25), len(H) - 1):                  # last 24h: closest approach to the level, in ATR
            L = H.c.iloc[i] - wcr.K * atr.iloc[i]
            gap = (H.l.iloc[i + 1] - L) / atr.iloc[i]
            if np.isfinite(gap) and gap <= 1.0:
                near.append({'sym': sym.replace('USDT', ''), 'hour': H.index[i + 1], 'gap_atr': float(gap)})
    watch.sort(key=lambda w: w['atr_pct'], reverse=True)
    def row(r):
        return {'sym': r['sym'].replace('USDT', ''), 't': r['t'], 'fill': r['fill'], 'ref': r.get('ref'), 'depth_pct': r.get('depth_pct'),
                'exit_t': r['exit_t'], 'exit': r['exit'], 'net': r['net'], 'why': r['why'], 'closed': r['closed'], 'mkt': r.get('mkt', False),
                'tier': r.get('tier'), 'btc_move': r.get('btc_move'), 'taker_sell': r.get('taker_sell'), 'basis_pre': r.get('basis_pre')}
    return {'name': 'Wick catcher', 'prereg': 'PREREG-WICK-CATCHER', 'forward_from': wcr.FORWARD_FROM,
            'rule': ('Every hour, on each of the 20 coins: a resting limit buy at the last hourly close minus 5 x ATR(1h), live '
                     'for the next hour. Filled only if price trades 0.1 ATR through it. Sell back at the pre-wick price, else '
                     'after 24h. 0.12% round trip (maker entry). Report-only second book: -8 ATR, 4h hold.'),
            'verdict': wcr.decide(st, today), 'stats': st, 'stats2': st2, 'stats_mkt': st_m, 'stats_coin': st_c,
            'sig': sig, 'addons': addons, 'sig_thr': {'btc_dump': wcr.BTC_DUMP, 'low_sell': wcr.LOW_SELL, 'discount': wcr.DISCOUNT},
            'trades': sorted([row(r) for r in rows], key=lambda r: r['t'], reverse=True),
            'trades2': sorted([row(r) for r in rows2], key=lambda r: r['t'], reverse=True),
            'watch': watch, 'near': sorted(near, key=lambda x: x['hour'], reverse=True)[:40]}


def discount(today):
    pf = _PERP or {s: pk.live(s, 'perp', pdr.FORWARD_FROM - pd.Timedelta(days=2)) for s in pdr.UNIVERSE}
    rows, rows2, cur = pdr.read(perp_frames=pf)
    pdr.tag_btc(rows, pf)
    st, st2 = wcr.stats(rows), wcr.stats(rows2)
    st_f, st_n = wcr.stats([r for r in rows if r['btc_fall']]), wcr.stats([r for r in rows if not r['btc_fall']])
    watch = sorted([{'sym': k.replace('USDT', ''), **v} for k, v in cur.items()], key=lambda w: w['basis_now'])
    def row(r):
        return {'sym': r['sym'].replace('USDT', ''), 't': r['t'], 'fill': r['fill'], 'basis': r['basis'], 'exit_t': r['exit_t'],
                'exit': r['exit'], 'net': r['net'], 'why': r['why'], 'closed': r['closed'], 'btc1h': r.get('btc1h'),
                'btc_fall': r.get('btc_fall')}
    return {'name': 'Perp below spot', 'prereg': 'PREREG-PERP-DISCOUNT', 'forward_from': pdr.FORWARD_FROM,
            'rule': ('When a coin\'s perp closes a 5m bar 0.30% or more below its spot price (forced perp selling), buy the '
                     'perp at the next 5m open and hold 4h. 0.20% round trip. Report-only second book: exit when the gap '
                     'closes (>= -0.05%), else 24h.'),
            'verdict': pdr.decide(st, today), 'stats': st, 'stats2': st2, 'threshold': pdr.THR,
            'stats_btc_fall': st_f, 'stats_btc_not': st_n, 'btc_fall_thr': pdr.BTC_FALL,
            'trades': sorted([row(r) for r in rows], key=lambda r: r['t'], reverse=True),
            'trades2': sorted([row(r) for r in rows2], key=lambda r: r['t'], reverse=True), 'watch': watch}


def main():
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    out = {'built': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'systems': {}, 'errors': {}}
    for key, fn in (('capitulation', capitulation), ('breakout', breakout), ('breakout_vol', lambda d: breakout(d, vol=True)),
                    ('carry', carry), ('wick', wick), ('discount', discount)):
        try:
            out['systems'][key] = fn(today)
        except Exception as e:
            out['errors'][key] = f'{type(e).__name__}: {e}'
            traceback.print_exc()
    tmp = OUT + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(_j(out), f)
    os.replace(tmp, OUT)
    print(f"wrote {OUT}: {list(out['systems'])} errors={list(out['errors'])}")


if __name__ == '__main__':
    main()

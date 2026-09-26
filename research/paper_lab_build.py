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


def breakout(today):
    now = pd.Timestamp.now(tz='UTC')
    trades, skipped, watch, ctrl = [], [], [], []
    for s in bo4.UNIVERSE:
        df = bo4.api_4h(s, bo4.FORWARD_FROM - pd.Timedelta(days=150))
        if len(df) < bo4.EMA_SPAN + 50:
            continue
        for x in bo4.run(df, bo4.FORWARD_FROM, detail=True):
            x['sym'] = s.replace('USDT', '')
            (trades if x['kind'] == 'trade' else skipped).append(x)
        ctrl += [x for x in bo4.run(df, bo4.FORWARD_FROM, every_bar=True) if x[2]]
        w = bo4.watch(df)
        if w:
            w['sym'] = s.replace('USDT', '')
            w['open_position'] = any(t['sym'] == w['sym'] and not t['closed'] for t in trades)
            watch.append(w)
    ctrl_E = float(np.mean([x[1] for x in ctrl])) if ctrl else None
    closed = [t for t in trades if t['closed']]
    st = bo4.summarize([(t['entry_time'], t['R'], True) for t in closed], ctrl_E if ctrl_E is not None else float('nan'),
                       bo4.FORWARD_FROM, now.normalize() + pd.Timedelta(days=1)) if closed else {'n': 0}
    curve, cum = [], 0.0
    for t in sorted(closed, key=lambda t: t['exit_time']):
        cum += t['R']; curve.append({'t': t['exit_time'], 'v': cum})
    watch.sort(key=lambda w: (not w['above_ema'], w['to_trigger_pct'] is None or w['to_trigger_pct'] <= 0, abs(w['to_trigger_pct'] or 1e9)))
    return {'name': '4h breakout', 'prereg': 'PREREG-BREAKOUT-4H', 'forward_from': bo4.FORWARD_FROM,
            'rule': ('4h close crosses above the last pivot-3 swing high while close > EMA200. Buy next open, stop 2x ATR14, '
                     'exit on the stop or after 36 bars (6 days). 20 bps. One position per coin. Control: a long at '
                     'every 4h open with the same exit.'),
            'verdict': bo4.decide(st, today), 'stats': st, 'control_E': ctrl_E,
            'open_R': sum(t['R'] for t in trades if not t['closed']),
            'trades': sorted(trades, key=lambda t: t['entry_time'], reverse=True), 'skipped': skipped,
            'watch': watch, 'curve': curve}


def carry(today):
    r = fcr.read()
    cur = sorted(r['current'].items(), key=lambda x: -x[1])
    open_syms = {t['sym'] for t in r['open']}
    watch = [{'sym': s.replace('USDT', ''), 'trailing_ann': v, 'gap_to_entry': fcr.ENTER - v, 'in_position': s in open_syms}
             for s, v in cur]
    def row(t):
        return {'sym': t['sym'].replace('USDT', ''), 'entry': t['entry'], 'exit': t['exit'], 'funding': t['funding'],
                'basis': t['basis'], 'net': t['net'], 'closed': t['exit'] is not None}
    return {'name': 'Funding carry', 'prereg': 'PREREG-FUNDING-CARRY', 'forward_from': fcr.FORWARD_FROM,
            'rule': ('Long spot + short perp, equal notional, when a coin\'s trailing-24h funding >= 15%/yr; exit < 3%/yr. '
                     'Act 1h after the settlement. 0.30% round trip. 20 slots, 1.25x capital per notional.'),
            'verdict': fcr.decide(r, today), 'enter': fcr.ENTER, 'exit': fcr.EXIT,
            'net_on_capital': r['net_on_capital'], 'ann_on_deployed': r['ann_on_deployed'], 'drag_share': r['drag_share'],
            'trades': [row(t) for t in r['open'] + r['closed']], 'watch': watch}


def main():
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    out = {'built': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'systems': {}, 'errors': {}}
    for key, fn in (('capitulation', capitulation), ('breakout', breakout), ('carry', carry)):
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

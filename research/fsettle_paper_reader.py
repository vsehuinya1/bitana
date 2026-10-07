"""Reader for the funding-settlement paper tracker (research/fsettle_paper.py).

Usage: venv/bin/python research/fsettle_paper_reader.py
Compares live paper results with the C-X-tick backtest (rule +21.2 bp/trade net, day-mean +23.8, 24% stop-outs).
"""
import json
import os
import sys

import numpy as np
import pandas as pd

OUT = os.environ.get('FSETTLE_OUT', '/root/bitana/research_cache/scalp/paper_fsettle/')
BACKTEST = {'rule': 21.2, 'nostop': 18.2, 'L1s': 15.2, 'L5s': 8.2}

fn = OUT + 'events.csv'
if not os.path.exists(fn):
    sys.exit('no events yet')
e = pd.read_csv(fn)
e['day'] = pd.to_datetime(e.s_utc).dt.normalize()
# The backtest used crypto USDT perps only; TradFi perps (equities, gold, FX ...) are logged but reported separately.
INFO = '/root/bitana/research_cache/scalp/exchangeInfo_20261006.json'
utype = {x['symbol']: x['underlyingType'] for x in json.load(open(INFO))['symbols']} if os.path.exists(INFO) else {}
e['crypto'] = e.sym.map(lambda x: utype.get(x, 'COIN') == 'COIN' and x.endswith('USDT'))
other = e[~e.crypto]
if len(other):
    print(f'TradFi/other perps logged separately (exploratory, not in the backtest): {len(other)} events, '
          f"rule mean net {other.net_rule.mean():.2f} bp ({other.sym.nunique()} symbols)")
e = e[e.crypto]
clean = e[(e.complete == 1) & (e.gap_trades == 0) & (e.f_prev_fresh == 1)]
print(f'events {len(e)} (clean {len(clean)}: complete, no tape gap, fresh f_prev); days {e.day.nunique()}; '
      f'short share {(e.d < 0).mean():.0%}; span {e.s_utc.min()} → {e.s_utc.max()}')
print(f'concurrent armed per settlement: median {e.concurrent_armed.median():.0f}, max {e.concurrent_armed.max()}')
if len(clean):
    print(f'feed lag at settlement (local recv − trade time): median {clean.lag_med_ms.median():.0f} ms, '
          f'p90 of per-event max {clean.lag_max_ms.quantile(.9):.0f} ms')
print('\nvariant   n     event-mean  day-mean (t)   win   stopped   backtest event-mean')
for k in ('rule', 'nostop', 'L0', 'L1s', 'L5s'):
    c = f'net_{k}'
    y = clean.dropna(subset=[c])
    if y.empty:
        continue
    dm = y.groupby('day')[c].mean()
    t = dm.mean() / dm.std() * np.sqrt(len(dm)) if len(dm) > 2 else float('nan')
    stop = f"{y.stopped.mean():.0%}" if k == 'rule' else ''
    print(f'{k:8s} {len(y):4d}   {y[c].mean():8.2f}   {dm.mean():7.2f} ({t:4.2f})  {(y[c] > 0).mean():4.0%}   {stop:7s}  {BACKTEST.get(k, "")}')
if 'net_pre60' in clean.columns:
    y = clean.dropna(subset=['net_pre60'])
    if len(y):
        dm = y.groupby('day').net_pre60.mean()
        t = dm.mean() / dm.std() * np.sqrt(len(dm)) if len(dm) > 2 else float('nan')
        print(f'pre60    {len(y):4d}   {y.net_pre60.mean():8.2f}   {dm.mean():7.2f} ({t:4.2f})  {(y.net_pre60 > 0).mean():4.0%}'
              f'            (C-pre leg, added 2026-10-07 08:40Z; price {y.pre_px.mean():.1f} + funding {y.pre_fund.mean():.1f} - 10)')
paths = [c for c in clean.columns if c.startswith('path_')]
if len(clean) and paths:
    print('\nsigned move after settlement, mean bp:', '  '.join(f'{c[5:]} {clean[c].mean():.1f}' for c in paths))

# Robustness for C (added AFTER the C result; stricter checks only): placebo offsets, concentration, BTC context.
import numpy as np, pandas as pd
src = open('test_C_funding.py').read().split('rows, missing = [], []')[0]
exec(src)
import os
recs = []
OFFS = list(range(-16, 17))   # half-hour steps around settlement
for s, (p, cost) in P.items():
    f = pd.read_pickle(f'{E}funding_all/{s}.pkl').sort_index(); f = f[~f.index.duplicated()]
    if f.index.tz is None: f.index = f.index.tz_localize('UTC')
    f.index = f.index.floor('min'); fp = f.shift(1)
    st = f.index[(f.index >= '2023-12-01') & (f.index <= '2026-09-24')]; st = st[st.minute % 5 == 0]
    fv = fp.reindex(st).values; m = np.abs(fv) >= 0.001
    st, fv = st[m], fv[m]
    if not len(st): continue
    sg = np.sign(fv)
    d = {'sym': s, 'time': st, 'fprev': fv, 'cost': cost}
    for k in OFFS:
        t0 = st + pd.Timedelta(minutes=30 * k)
        d[k] = sg * (px(p, t0 + pd.Timedelta('30min')) / px(p, t0) - 1) * 1e4
    recs.append(pd.DataFrame(d))
ev = pd.concat(recs, ignore_index=True); ev['day'] = ev.time.dt.tz_convert(None).dt.normalize()
print('events', len(ev))
print('\n(a) placebo: same direction rule, 30-min window starting k half-hours from settlement (gross bps, day-mean):')
for per, m in (('disc', ev.day <= DISC_END), ('hold', ev.day >= HOLD_START)):
    x = ev[m]
    print(per, ' '.join(f'{k:+d}:{x.groupby("day")[k].mean().mean():5.1f}' for k in OFFS))
x = ev
print('\n(b) concentration, POST30 (k=0) gross:')
g = ev[0].dropna()
print(f'  mean {g.mean():.1f}  median {g.median():.1f}  trimmed(5%) {g[(g > g.quantile(.05)) & (g < g.quantile(.95))].mean():.1f}  win% {(g > 0).mean()*100:.0f}')
pc = ev.groupby('sym')[0].agg(['count', 'mean', 'sum']).sort_values('sum', ascending=False)
print('  events per coin (top 8 by total gross):'); print(pc.head(8).round(1).to_string())
print(f'  share of total gross from top 3 coins: {pc["sum"].head(3).sum() / pc["sum"].sum():.0%}')
dd = ev.groupby('day')[0].mean().sort_values()
print(f'  days {len(dd)}; mean {dd.mean():.1f}; without top 10 days {dd.iloc[:-10].mean():.1f}; without top+bottom 10 {dd.iloc[10:-10].mean():.1f}')
print('\n(c) by year/half, POST30 gross day-mean and n:')
ev['half'] = ev.time.dt.year.astype(str) + 'H' + np.where(ev.time.dt.month <= 6, '1', '2')
print(ev.groupby('half').apply(lambda x: pd.Series({'n': len(x), 'coins': x.sym.nunique(), 'gross_day': x.groupby('day')[0].mean().mean()})).round(1).to_string())
ev.to_pickle('/root/bitana/research_cache/scalp/robust_C_events.pkl')

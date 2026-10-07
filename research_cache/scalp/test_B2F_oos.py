# Test B2F — US-open fade on the 40 unseen k5m_x coins (rules: reports/scalping_prereg.md, registered before this run).
import glob, os
import numpy as np, pandas as pd
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_B_intraday_mom.py')).read().split('def main')[0]
exec(src)
P = {}
for f in sorted(glob.glob(f'{E}k5m_x/*.pkl')):
    x = pd.read_pickle(f)['perp'].o
    x = x[~x.index.duplicated()]
    P[os.path.basename(f)[:-4]] = x.reindex(pd.date_range(x.index[0], x.index[-1], freq='5min'))
days = pd.date_range('2023-12-04', '2026-09-24', freq='D', tz='UTC'); wk = days[days.weekday < 5]
s1, s2, e1, e2 = ny_times(wk, '09:30'), ny_times(wk, '10:00'), ny_times(wk, '10:00'), ny_times(wk, '11:00')
print(len(P), 'coins')
for tail in (True, False):
    pc = {s: -cell_trades(p, s1, s2, e1, e2, tail) for s, p in P.items()}
    gross = pd.DataFrame(pc).mean(axis=1).dropna()
    for name, sl in (('all', slice(None)), ('half1', slice(None, '2025-03-31')), ('half2', slice('2025-04-01', None))):
        g = gross[sl]; n = g - 12
        print(f'tail={tail!s:5} {name:5} days {len(g):4d} gross {g.mean():6.2f} (t {g.mean()/g.std()*np.sqrt(len(g)):5.2f})'
              f'  net12 {n.mean():6.2f} (t {n.mean()/n.std()*np.sqrt(len(n)):5.2f})  net_MT9 {g.mean()-9:6.2f}')

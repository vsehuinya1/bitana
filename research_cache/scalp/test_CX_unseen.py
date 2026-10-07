# Test C-X — C on unseen coins (rules: reports/scalping_prereg.md, registered before this run).
import glob, os, sys
import numpy as np, pandas as pd
sys.path.append('/root/bitana/research')
import capitulation_reader as capr
E = '/root/bitana/research_cache/edge/'
seen = set(capr.UNIVERSE) | {os.path.basename(f)[:-4] for f in glob.glob(f'{E}k5m_x/*.pkl')}
rows = []
for f in sorted(glob.glob(f'{E}k15m/*.npy')):
    s = os.path.basename(f)[:-4]
    if s in seen or not os.path.exists(f'{E}funding_all/{s}.pkl'):
        continue
    a = np.load(f)
    p = pd.Series(a[:, 1], index=pd.to_datetime(a[:, 0], unit='ms', utc=True))
    p = p[~p.index.duplicated()]
    fu = pd.read_pickle(f'{E}funding_all/{s}.pkl').sort_index(); fu = fu[~fu.index.duplicated()]
    if fu.index.tz is None: fu.index = fu.index.tz_localize('UTC')
    fu.index = fu.index.floor('min'); fp = fu.shift(1)
    st = fu.index[(fu.index >= p.index[0]) & (fu.index <= p.index[-1] - pd.Timedelta('30min')) & (fu.index.minute % 15 == 0)]
    fv = fp.reindex(st).values; m = np.abs(fv) >= 0.001
    st, fv = st[m], fv[m]
    if not len(st): continue
    g = np.sign(fv) * (p.reindex(st + pd.Timedelta('30min')).values / p.reindex(st).values - 1) * 1e4
    rows.append(pd.DataFrame({'sym': s, 'time': st, 'fprev': fv, 'g': g}))
ev = pd.concat(rows, ignore_index=True).dropna(subset=['g']); ev['day'] = ev.time.dt.tz_convert(None).dt.normalize()
print(f'unseen coins with events: {ev.sym.nunique()}, events {len(ev)}, days {ev.day.nunique()}, negative-funding share {(ev.fprev < 0).mean():.2f}')
for c in (0, 12, 20):
    d = (ev.g - c).groupby(ev.day).mean()
    print(f'cost {c:2d} bp: day-mean {d.mean():6.2f}  t {d.mean() / d.std() * np.sqrt(len(d)):5.2f}   (event mean {ev.g.mean() - c:6.2f}, median {ev.g.median() - c:6.2f}, win {(ev.g > c).mean():.0%})')
pc = ev.groupby('sym').g.agg(['count', 'mean', 'sum']).sort_values('sum', ascending=False)
print(f'top-3 coins share of gross: {pc["sum"].head(3).sum() / pc["sum"].sum():.0%}'); print(pc.head(6).round(1).to_string())
ev['month'] = ev.time.dt.strftime('%Y-%m')
print(ev.groupby('month').agg(n=('g', 'size'), coins=('sym', 'nunique'), gross=('g', 'mean')).round(1).T.to_string())
ev.to_pickle('/root/bitana/research_cache/scalp/test_CX.pkl')

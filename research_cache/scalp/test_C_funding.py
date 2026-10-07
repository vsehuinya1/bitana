# Test C — funding-settlement pressure (rules: reports/scalping_prereg.md, registered before this run).
import glob, os, sys
import numpy as np, pandas as pd
sys.path.append('/root/bitana/research')
import capitulation_reader as capr
E = '/root/bitana/research_cache/edge/'
DISC_END, HOLD_START = '2025-03-31', '2025-04-01'
COST = {'BTCUSDT': 10.1, 'ETHUSDT': 10.1, 'BNBUSDT': 10.1}


def grid(s):
    s = s[~s.index.duplicated()].sort_index()
    if s.index.tz is None:
        s.index = s.index.tz_localize('UTC')
    return s.reindex(pd.date_range(s.index[0], s.index[-1], freq='5min'))


P = {}
for s in capr.UNIVERSE:
    a = np.load(f'{E}k5m/{s}.npy')
    P[s] = (grid(pd.Series(a[:, 1], index=pd.to_datetime(a[:, 0], unit='ms', utc=True))), COST.get(s, 11.0))
for f in sorted(glob.glob(f'{E}k5m_x/*.pkl')):
    P[os.path.basename(f)[:-4]] = (grid(pd.read_pickle(f)['perp'].o), 12.0)


def px(p, t):
    d = pd.DatetimeIndex(t).as_unit('ns').asi8 - p.index[0].value
    i = d // 300_000_000_000; ok = (i >= 0) & (i < len(p)) & (d % 300_000_000_000 == 0)
    out = np.full(len(d), np.nan); out[ok] = p.values[i[ok]]; return out


rows, missing = [], []
for s, (p, cost) in P.items():
    fn = f'{E}funding_all/{s}.pkl'
    if not os.path.exists(fn):
        missing.append(s); continue
    f = pd.read_pickle(fn).sort_index()
    f = f[~f.index.duplicated()]
    if f.index.tz is None:
        f.index = f.index.tz_localize('UTC')
    f.index = f.index.floor('min')
    fp = f.shift(1)
    st = f.index[(f.index >= '2023-12-01') & (f.index <= '2026-09-24')]
    st = st[st.minute % 5 == 0]
    sg = np.sign(fp.reindex(st).values)
    o = {k: px(p, st + pd.Timedelta(k)) for k in ('-30min', '0min', '30min', '60min')}
    df = pd.DataFrame({'sym': s, 'time': st, 'fprev': fp.reindex(st).values, 'cost': cost,
                       'PRE': -sg * (o['0min'] / o['-30min'] - 1) * 1e4,
                       'POST30': sg * (o['30min'] / o['0min'] - 1) * 1e4,
                       'POST60': sg * (o['60min'] / o['0min'] - 1) * 1e4})
    rows.append(df)
ev = pd.concat(rows, ignore_index=True); ev['day'] = ev.time.dt.tz_convert(None).dt.normalize()
print(f'coins {ev.sym.nunique()} (missing funding: {missing}); settlements {len(ev)}')
out = []
for th in (0.0003, 0.0010):
    e = ev[ev.fprev.abs() >= th]
    print(f'theta {th:.2%}: events {len(e)}, coins {e.sym.nunique()}, positive-funding share {(e.fprev > 0).mean():.2f}')
    for w in ('PRE', 'POST30', 'POST60'):
        for per, m in (('disc', e.day <= DISC_END), ('hold', e.day >= HOLD_START)):
            x = e[m].dropna(subset=[w])
            dg = x.groupby('day')[w].mean(); dn = (x[w] - x.cost).groupby(x.day).mean()
            out.append(dict(theta=f'{th:.2%}', win=w, per=per, n=len(x), days=len(dn), gross=x[w].mean(),
                            gross_day=dg.mean(), t_gross=dg.mean() / dg.std() * np.sqrt(len(dg)),
                            net=dn.mean(), t_net=dn.mean() / dn.std() * np.sqrt(len(dn))))
out = pd.DataFrame(out); out.to_pickle('/root/bitana/research_cache/scalp/test_C.pkl')
pd.set_option('display.width', 200)
print(out.round(2).to_string(index=False))
# split by funding sign (secondary)
for th in (0.0003, 0.0010):
    e = ev[ev.fprev.abs() >= th]
    print(f'\ntheta {th:.2%} by sign, gross mean bps (disc / hold):')
    for sgn, m in (('f>0', e.fprev > 0), ('f<0', e.fprev < 0)):
        x = e[m]
        print('  ', sgn, ' '.join(f"{w} {x[x.day <= DISC_END][w].mean():6.2f} / {x[x.day >= HOLD_START][w].mean():6.2f} (n {len(x)})" for w in ('PRE', 'POST30', 'POST60')))

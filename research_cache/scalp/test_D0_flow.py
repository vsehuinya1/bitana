# Test D0 — taker-flow burst aftermath on 5m bars (rules: reports/scalping_prereg.md, registered before this run).
import sys
import numpy as np, pandas as pd
sys.path.append('/root/bitana/research')
import capitulation_reader as capr

E = '/root/bitana/research_cache/edge/'
DISC_END, HOLD_START = '2024-12-31', '2025-01-01'
COST = {'BTCUSDT': 10.1, 'ETHUSDT': 10.1, 'BNBUSDT': 10.1}
H = {'15m': 3, '30m': 6, '60m': 12}


def events(sym):
    a = np.load(f'{E}k5mv/{sym}.npy')
    d = pd.DataFrame(a[:, 1:], columns=['o', 'h', 'l', 'c', 'v', 'tb'], index=pd.to_datetime(a[:, 0], unit='ms'))
    d = d[~d.index.duplicated()]
    d = d.reindex(pd.date_range(d.index[0], d.index[-1], freq='5min'))
    d = d[d.index >= '2020-08-01']
    notl = d.v * d.c
    F = (2 * d.tb - d.v) * d.c / notl.rolling(288, min_periods=200).mean().shift(1)
    hi = F.rolling(8640, min_periods=4000).quantile(0.995).shift(1)
    lo = F.rolling(8640, min_periods=4000).quantile(0.005).shift(1)
    r = d.c / d.o - 1
    sig = pd.Series(0.0, index=d.index)
    sig[(F > hi) & (r > 0)] = 1.0
    sig[(F < lo) & (r < 0)] = -1.0
    idx = np.where(sig.values != 0)[0]
    keep, last = [], -10**9
    for i in idx:
        if i - last >= 12:
            keep.append(i); last = i
    keep = np.array(keep)
    o = d.o.values
    rows = {'time': d.index[keep], 'sgn': sig.values[keep]}
    for k, n in H.items():
        j = keep + 1 + n
        ok = j < len(o)
        g = np.full(len(keep), np.nan)
        g[ok] = sig.values[keep[ok]] * (o[j[ok]] / o[keep[ok] + 1] - 1) * 1e4
        rows[k] = g
    ev = pd.DataFrame(rows); ev['sym'] = sym; ev['cost'] = COST.get(sym, 11.0)
    return ev[ev.time >= '2020-09-01']


ev = pd.concat([events(s) for s in capr.UNIVERSE], ignore_index=True)
ev['day'] = ev.time.dt.normalize()
print(f'events: {len(ev)} ({len(ev) / ev.day.nunique():.2f}/day over {ev.day.nunique()} days), long share {(ev.sgn > 0).mean():.2f}')

btc = np.load(f'{E}k5m/BTCUSDT.npy'); bc = pd.Series(btc[:, 4], index=pd.to_datetime(btc[:, 0], unit='ms'))
rv = np.log(bc).diff().groupby(bc.index.normalize()).std().shift(1)
hv = set(rv[rv >= rv.rolling(250, min_periods=60).quantile(0.8)].index)

rows = []
for sub in ('all', 'highvol', 'long', 'short'):
    e = ev if sub == 'all' else ev[ev.day.isin(hv)] if sub == 'highvol' else ev[ev.sgn > 0] if sub == 'long' else ev[ev.sgn < 0]
    for k in H:
        for per, m in (('disc', e.day <= DISC_END), ('hold', e.day >= HOLD_START)):
            x = e[m].dropna(subset=[k])
            dg = x.groupby('day')[k].mean(); dn = (x[k] - x.cost).groupby(x.day).mean()
            rows.append(dict(sub=sub, h=k, per=per, n=len(x), days=len(dn), gross=x[k].mean(),
                             t_gross=dg.mean() / dg.std() * np.sqrt(len(dg)), net=dn.mean(),
                             t_net=dn.mean() / dn.std() * np.sqrt(len(dn))))
out = pd.DataFrame(rows); out.to_pickle('/root/bitana/research_cache/scalp/test_D0.pkl')
pd.set_option('display.width', 200)
print(out.round(2).to_string(index=False))

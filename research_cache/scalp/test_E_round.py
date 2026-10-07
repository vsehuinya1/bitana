# Test E — round-number levels (rules: reports/scalping_prereg.md, registered before this run).
import glob
import numpy as np, pandas as pd

G = '/root/bitana/research_cache/scalp/grid1s/'
START, DISC_END = '2026-07-24', '2026-08-29'
STEP = {'BTCUSDT': 1000, 'ETHUSDT': 50, 'SOLUSDT': 5, 'XRPUSDT': 0.05, 'DOGEUSDT': 0.005, '1000PEPEUSDT': 0.0001}
SPREAD = {'BTCUSDT': 0.01, 'ETHUSDT': 0.04, 'SOLUSDT': 0.84, 'XRPUSDT': 0.66, 'DOGEUSDT': 1.04, '1000PEPEUSDT': 0.23}
HS = [60, 300, 900]
LOOK = 4 * 3600
days = pd.date_range(START, '2026-10-05', freq='D')


def mids(sym):
    parts = [np.load(f'{G}{sym}/{d.date()}.npz')['mid'] for d in days]
    return pd.Series(np.concatenate(parts)).ffill().values


def events(m, levels, sym, kind):
    rows = []
    N = len(m)
    for L in levels:
        side = np.sign(m - L)
        side[side == 0] = np.nan
        side = pd.Series(side).ffill().values
        cross = np.nonzero(side[1:] != side[:-1])[0] + 1
        cross = cross[np.isfinite(side[cross - 1])]
        last = -10**9
        for t in cross:
            if t - last < LOOK:
                last = t
                continue
            last = t
            a = 1.0 if side[t - 1] < 0 else -1.0
            if t + 900 + max(HS) >= N:
                continue
            day = days[t // 86400]
            # bounce: limit at L against the approach, filled if the mid goes >= 2 bp through L within 60 s
            w = a * (m[t:t + 61] / L - 1) * 1e4
            f = np.nonzero(w >= 2)[0]
            rec = {'sym': sym, 'kind': kind, 'L': L, 'day': day, 'a': a}
            for h in HS:
                rec[f'bounce_{h}'] = (-a * (m[t + f[0] + h] / L - 1) * 1e4) if len(f) else np.nan
            w2 = a * (m[t:t + 901] / L - 1) * 1e4
            b = np.nonzero(w2 >= 10)[0]
            for h in HS:
                rec[f'break_{h}'] = (a * (m[t + b[0] + h] / m[t + b[0]] - 1) * 1e4) if len(b) else np.nan
            rows.append(rec)
    return rows


allr = []
for sym, st in STEP.items():
    m = mids(sym)
    lo, hi = np.nanmin(m), np.nanmax(m)
    base = np.arange(np.floor(lo / st) * st, hi + st, st)
    allr += events(m, base, sym, 'round')
    allr += events(m, np.r_[base + 0.37 * st, base + 0.63 * st], sym, 'placebo')
x = pd.DataFrame(allr)
x['cost_b'] = 7 + x.sym.map(SPREAD) / 2
x['cost_k'] = 10 + x.sym.map(SPREAD)
x.to_pickle('/root/bitana/research_cache/scalp/test_E.pkl')
print(x.groupby(['kind']).size().to_dict(), 'touch events;', x[x.kind == 'round'].groupby('sym').size().to_dict())
rows = []
for typ, cc in (('bounce', 'cost_b'), ('break', 'cost_k')):
    for h in HS:
        c = f'{typ}_{h}'
        for kind in ('round', 'placebo'):
            y = x[(x.kind == kind)].dropna(subset=[c])
            for per, msk in (('disc', y.day <= DISC_END), ('hold', y.day > DISC_END)):
                z = y[msk]
                dn = (z[c] - z[cc]).groupby(z.day).mean()
                rows.append(dict(cell=c, kind=kind, per=per, n=len(z), days=len(dn), gross=z[c].mean(),
                                 net=dn.mean(), t=dn.mean() / dn.std() * np.sqrt(len(dn)) if len(dn) > 2 else np.nan))
pd.set_option('display.width', 200)
print(pd.DataFrame(rows).round(2).to_string(index=False))
fill = x[x.kind == 'round'].bounce_60.notna().mean(); brk = x[x.kind == 'round'].break_60.notna().mean()
print(f'round levels: bounce fill rate {fill:.0%}, break trigger rate {brk:.0%}')

# Test C-tick — fill realism for C on aggTrades (rules: reports/scalping_prereg.md, registered before this run).
import os
import numpy as np, pandas as pd

S = '/root/bitana/research_cache/scalp/'
DISC_END, HOLD_START = '2025-03-31', '2025-04-01'
ev = pd.read_pickle(S + 'robust_C_events.pkl')
LS = {'1s': 1_000, '5s': 5_000, '30s': 30_000}
HS = {'30m': 1_800_000, '60m': 3_600_000}
PATH = {'1s': 1_000, '5s': 5_000, '30s': 30_000, '60s': 60_000, '5m': 300_000, '30m': 1_800_000}


def first(t, p, mask, at):
    i = np.searchsorted(t, at)
    j = np.nonzero(mask[i:])[0]
    return p[i + j[0]] if len(j) else np.nan


def last_before(t, p, at):
    i = np.searchsorted(t, at, side='right') - 1
    return p[i] if i >= 0 else np.nan


rows = []
for r in ev.itertuples():
    s = r.time.value // 10**6
    fn = f'{S}C_ticks/{r.sym}_{s}.npz'
    if not os.path.exists(fn):
        continue
    z = np.load(fn); t, p, ibm = z['t'], z['p'], z['ibm']
    if len(t) < 10:
        continue
    o = np.argsort(t, kind='stable'); t, p, ibm = t[o], p[o], ibm[o]
    d = np.sign(r.fprev)
    sell_side, buy_side = (ibm, ~ibm) if d < 0 else (~ibm, ibm)   # entry side trades, exit side trades
    ref = last_before(t, p, s - 1)
    rec = dict(sym=r.sym, time=r.time, day=r.day, d=d, bar5m=r[[c for c in ev.columns].index(0) + 1])
    for k, dt in PATH.items():
        rec[f'path_{k}'] = d * (last_before(t, p, s + dt) / ref - 1) * 1e4
    for lk, L in LS.items():
        en = first(t, p, sell_side, s + L)
        other = first(t, p, buy_side, s + L)
        rec[f'spread_{lk}'] = abs(other / en - 1) * 1e4
        for hk, H in HS.items():
            ex = first(t, p, buy_side, s + H)
            rec[f'g_{lk}_{hk}'] = d * (ex / en - 1) * 1e4
        if lk == '5s':
            ti = np.searchsorted(t, s + L)
            for stop in (200, 500):
                lim = en * (1 - d * stop / 1e4)          # adverse level: above entry for a short
                w = slice(ti, np.searchsorted(t, s + HS['30m']))
                hit = np.nonzero(d * (p[w] - lim) <= 0)[0]
                if len(hit):
                    ex = first(t, p, buy_side, t[w][hit[0]])
                    rec[f'stop{stop}'] = d * (ex / en - 1) * 1e4
                else:
                    rec[f'stop{stop}'] = rec['g_5s_30m']
    rows.append(rec)
x = pd.DataFrame(rows)
x.to_pickle(S + 'test_C_tick.pkl')
print(f'events with ticks: {len(x)} / {len(ev)}')
print('\nsigned move after settlement (bps, mean / median):')
print('  ' + '  '.join(f"{k}: {x[f'path_{k}'].mean():5.1f}/{x[f'path_{k}'].median():5.1f}" for k in PATH))
print(f"  5m-bar version (test C) on the same events: mean {x.bar5m.mean():.1f}")
print('\neffective spread at entry (bps, median / mean):', '  '.join(f"{k}: {x[f'spread_{k}'].median():.1f}/{x[f'spread_{k}'].mean():.1f}" for k in LS))
out = []
for lk in LS:
    for hk in HS:
        c = f'g_{lk}_{hk}'
        for per, m in (('disc', x.day <= DISC_END), ('hold', x.day >= HOLD_START), ('all', x.day.notna())):
            y = x[m].dropna(subset=[c]); dn = (y[c] - 10).groupby(y.day).mean()
            out.append(dict(L=lk, H=hk, per=per, n=len(y), days=len(dn), gross=y[c].mean(), median=y[c].median(),
                            win=(y[c] > 10).mean() * 100, net=dn.mean(), t=dn.mean() / dn.std() * np.sqrt(len(dn))))
pd.set_option('display.width', 200)
print(pd.DataFrame(out).round(2).to_string(index=False))
print('\nstop variants, L=5s H=30m (reported only):')
for c in ('g_5s_30m', 'stop200', 'stop500'):
    for per, m in (('disc', x.day <= DISC_END), ('hold', x.day >= HOLD_START)):
        y = x[m].dropna(subset=[c]); dn = (y[c] - 10).groupby(y.day).mean()
        print(f'  {c:9s} {per}  net {dn.mean():6.2f}  t {dn.mean() / dn.std() * np.sqrt(len(dn)):5.2f}  worst {y[c].min():8.0f}  p1 {y[c].quantile(.01):7.0f}')

# EXPLORATORY (after C-tick failed its rule): latency curve and pre-settlement entry economics.
import os
import numpy as np, pandas as pd
S = '/root/bitana/research_cache/scalp/'; DISC_END = '2025-03-31'
ev = pd.read_pickle(S + 'robust_C_events.pkl')
E = '/root/bitana/research_cache/edge/'
LAT = [-1000, 0, 100, 250, 500, 1000, 2000, 5000]
rows = []
fcache = {}
for r in ev.itertuples():
    s = r.time.value // 10**6; fn = f'{S}C_ticks/{r.sym}_{s}.npz'
    if not os.path.exists(fn): continue
    z = np.load(fn); t, p, ibm = z['t'], z['p'], z['ibm']
    if len(t) < 10: continue
    o = np.argsort(t, kind='stable'); t, p, ibm = t[o], p[o], ibm[o]
    d = np.sign(r.fprev); ent, ext = (ibm, ~ibm) if d < 0 else (~ibm, ibm)
    def first(mask, at):
        i = np.searchsorted(t, at); j = np.nonzero(mask[i:])[0]; return p[i + j[0]] if len(j) else np.nan
    if r.sym not in fcache:
        f = pd.read_pickle(f'{E}funding_all/{r.sym}.pkl'); f = f[~f.index.duplicated()]
        if f.index.tz is None: f.index = f.index.tz_localize('UTC')
        f.index = f.index.floor('min'); fcache[r.sym] = f
    fs = fcache[r.sym].get(r.time, np.nan)                     # the rate settled AT s (paid by a position held at s)
    ex = first(ext, s + 1_800_000)
    rec = dict(day=r.day, sym=r.sym, d=d, f_now=fs, f_prev=r.fprev)
    for L in LAT:
        rec[L] = d * (ex / first(ent, s + L) - 1) * 1e4
    rows.append(rec)
x = pd.DataFrame(rows)
# funding paid by a position opened before s: a short when f<0 pays |f_now|; a long when f>0 pays f_now
x['fund_cost'] = np.where(np.sign(x.f_now) == x.d, np.abs(x.f_now) * 1e4, -np.abs(x.f_now) * 1e4)   # d is the paying side
print(f'events {len(x)}; |f_now| median {x.f_now.abs().median()*1e4:.1f} bp, mean {x.f_now.abs().mean()*1e4:.1f} bp; '
      f'same sign as f_prev {(np.sign(x.f_now) == x.d).mean():.0%}')
print('\nnet per trade after 10 bp fees, H=30m (event-weighted mean | day-mean), by entry time relative to settlement:')
for L in LAT:
    line = f'  L={L:+5d} ms '
    for per, m in (('disc', x.day <= DISC_END), ('hold', x.day > DISC_END), ('all', x.day.notna())):
        y = x[m].dropna(subset=[L]); g = y[L] - 10
        if L < 0: g = g - y.fund_cost   # entering before s also pays/receives this interval's funding
        dm = g.groupby(y.day).mean()
        line += f'| {per} {g.mean():6.1f} | {dm.mean():6.1f} (t {dm.mean() / dm.std() * np.sqrt(len(dm)):4.1f}) '
    print(line)
print('\n(L=-1000 includes the funding paid at s; positive fund_cost = cost)')
x.to_pickle(S + 'explore_C_latency.pkl')

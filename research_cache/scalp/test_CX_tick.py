# Test C-X-tick — fixed fast-entry rule on unseen micro-cap events (rules: reports/scalping_prereg.md, registered before run).
import os
import numpy as np, pandas as pd

S = '/root/bitana/research_cache/scalp/'
ev = pd.read_pickle(S + 'CX_tick_sample.pkl')
H = 1_800_000; STOP = 200; FEE = 10


def sim(t, p, ent, ext, s, L, d, stop):
    i = np.searchsorted(t, s + L); j = np.nonzero(ent[i:])[0]
    if not len(j):
        return np.nan
    ie = i + j[0]; en = p[ie]
    iH = np.searchsorted(t, s + H)
    k = iH
    if stop:
        adverse = np.nonzero(d * (p[ie:iH] / en - 1) * 1e4 <= -stop)[0]
        if len(adverse):
            k = ie + adverse[0]
    jx = np.nonzero(ext[k:])[0]
    if not len(jx):
        return np.nan
    return d * (p[k + jx[0]] / en - 1) * 1e4


rows = []
for r in ev.itertuples():
    s = r.time.value // 10**6; fn = f'{S}CX_ticks/{r.sym}_{s}.npz'
    if not os.path.exists(fn):
        continue
    z = np.load(fn); t, p, ibm = z['t'], z['p'], z['ibm']
    if len(t) < 10:
        continue
    o = np.argsort(t, kind='stable'); t, p, ibm = t[o], p[o], ibm[o]
    if t[-1] < s + H:            # window must cover the hold
        continue
    d = np.sign(r.fprev); ent, ext = (ibm, ~ibm) if d < 0 else (~ibm, ibm)
    rows.append(dict(sym=r.sym, day=r.day, month=r.time.strftime('%Y-%m'), d=d, bar15m=r.g,
                     rule=sim(t, p, ent, ext, s, 250, d, STOP), nostop=sim(t, p, ent, ext, s, 250, d, 0),
                     L1s=sim(t, p, ent, ext, s, 1000, d, STOP), L5s=sim(t, p, ent, ext, s, 5000, d, STOP)))
x = pd.DataFrame(rows); x.to_pickle(S + 'test_CX_tick.pkl')
print(f'events with ticks: {len(x)} / {len(ev)}, coins {x.sym.nunique()}, days {x.day.nunique()}, short share {(x.d < 0).mean():.2f}')
print(f'15m-bar gross on the same events: {x.bar15m.mean():.1f}')
for c in ('rule', 'nostop', 'L1s', 'L5s'):
    y = x.dropna(subset=[c]); g = y[c] - FEE; dm = g.groupby(y.day).mean()
    q = g.quantile([.01, .05, .5]).round(0).tolist()
    print(f'{c:7s} n {len(y):5d}  event-mean net {g.mean():6.2f}  day-mean net {dm.mean():6.2f} (t {dm.mean() / dm.std() * np.sqrt(len(dm)):4.2f})'
          f'  win {(g > 0).mean():.0%}  p1/p5/median {q}  worst {g.min():7.0f}')
y = x.dropna(subset=['rule'])
print('\nrule by month (event-mean net, n):')
print(y.groupby('month').rule.agg(lambda v: round((v - FEE).mean(), 1)).to_frame('net').join(y.groupby('month').size().rename('n')).T.to_string())

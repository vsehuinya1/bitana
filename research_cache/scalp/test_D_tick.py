# Test D — fade forced selling on high-vol days, tick-level fills (rules: reports/scalping_prereg.md, registered first).
import os
import numpy as np, pandas as pd

S = '/root/bitana/research_cache/scalp/'
ev = pd.read_pickle(S + 'D_tick_sample.pkl')
HS = {'30m': 1_800_000, '60m': 3_600_000}


def first(t, p, mask, at):
    i = np.searchsorted(t, at); j = np.nonzero(mask[i:])[0]
    return (p[i + j[0]], t[i + j[0]]) if len(j) else (np.nan, None)


rows = []
for r in ev.itertuples():
    c = r.time.value // 10**6
    fn = f'{S}D_ticks/{r.sym}_{c}.npz'
    if not os.path.exists(fn):
        continue
    z = np.load(fn); t, p, ibm = z['t'], z['p'], z['ibm']
    if len(t) < 10:
        continue
    o = np.argsort(t, kind='stable'); t, p, ibm = t[o], p[o], ibm[o]
    buy, sell = ~ibm, ibm                                  # buy-aggressor trades print at the ask, sell-aggressor at the bid
    rec = {'sym': r.sym, 'day': r.day, 'bar_fade_60m': -r._5}       # _5 = D0 '60m' (follow-direction gross)
    # TAKER: buy at the ask after c+1s
    en, te = first(t, p, buy, c + 1000)
    for hk, H in HS.items():
        if te is not None and t[-1] >= te + H:
            ex, _ = first(t, p, sell, te + H)
            rec[f'taker_{hk}'] = (ex / en - 1) * 1e4
        else:
            rec[f'taker_{hk}'] = np.nan
    # MAKER: limit at the last trade before c, filled if a trade prints strictly below within 5 min
    i0 = np.searchsorted(t, c) - 1
    lim = p[i0] if i0 >= 0 else np.nan
    i1, i2 = np.searchsorted(t, c + 1000), np.searchsorted(t, c + 301_000)
    below = np.nonzero(p[i1:i2] < lim)[0]
    rec['maker_filled'] = int(len(below) > 0)
    for hk, H in HS.items():
        if len(below):
            tf = t[i1 + below[0]]
            if t[-1] >= tf + H:
                ex, _ = first(t, p, sell, tf + H)
                rec[f'maker_{hk}'] = (ex / lim - 1) * 1e4
                continue
        rec[f'maker_{hk}'] = np.nan
    rows.append(rec)
x = pd.DataFrame(rows)
x['period'] = np.where(x.day <= '2024-12-31', '≤2024', '≥2025')
x.to_pickle(S + 'test_D_tick.pkl')
print(f'events with ticks: {len(x)} / {len(ev)}; maker fill rate {x.maker_filled.mean():.0%}; 5m-bar fade gross 60m on these: {x.bar_fade_60m.mean():.1f}')
for typ, fee in (('taker', 10), ('maker', 7)):
    for hk in HS:
        c = f'{typ}_{hk}'
        y = x.dropna(subset=[c]); g = y[c] - fee; dm = g.groupby(y.day).mean()
        per = '  '.join(f"{k} {(v[c] - fee).mean():6.1f} (n {len(v)})" for k, v in y.groupby('period'))
        print(f'{c:10s} n {len(y):4d}  event-mean net {g.mean():6.2f}  day-mean {dm.mean():6.2f} (t {dm.mean() / dm.std() * np.sqrt(len(dm)):4.2f})'
              f'  win {(g > 0).mean():.0%}  | {per}')

# Test C-pre-X — confirmation of C-pre on the unseen C-X-tick sample (rules: reports/scalping_prereg.md, registered before this run).
import os
import numpy as np, pandas as pd

S = '/root/bitana/research_cache/scalp/'
E = '/root/bitana/research_cache/edge/'
DISC_END, HOLD_START = '2025-03-31', '2025-04-01'
ev = pd.read_pickle(S + 'CX_tick_sample.pkl')
XS = {'300s': 300_000, '60s': 60_000, '10s': 10_000}
EXIT_MS = 250


def first(t, p, mask, at):
    i = np.searchsorted(t, at)
    j = np.nonzero(mask[i:])[0]
    return p[i + j[0]] if len(j) else np.nan


FS = {}
for sym in ev.sym.unique():
    f = pd.read_pickle(f'{E}funding_all/{sym}.pkl').sort_index()
    if f.index.tz is None:
        f.index = f.index.tz_localize('UTC')
    f.index = f.index.floor('min'); f = f[~f.index.duplicated()]
    FS[sym] = f

rows = []
for r in ev.itertuples():
    s = r.time.value // 10**6
    fn = f'{S}CX_ticks/{r.sym}_{s}.npz'
    if not os.path.exists(fn):
        continue
    z = np.load(fn); t, p, ibm = z['t'], z['p'], z['ibm']
    if len(t) < 10:
        continue
    o = np.argsort(t, kind='stable'); t, p, ibm = t[o], p[o], ibm[o]
    d = np.sign(r.fprev)
    c_entry, pre_entry = (ibm, ~ibm) if d < 0 else (~ibm, ibm)    # C entry side (short: sells at the bid); pre leg = other side
    fs = FS[r.sym].get(r.time, np.nan)
    ex = first(t, p, c_entry, s + EXIT_MS)
    rec = dict(sym=r.sym, day=r.day, d=d, fprev=r.fprev, fs=fs, fund=d * fs * 1e4)
    for xk, X in XS.items():
        i = np.searchsorted(t, s - X); j = np.nonzero(pre_entry[i:np.searchsorted(t, s)])[0]
        en = p[i + j[0]] if len(j) else np.nan                     # first -d trade in [s-X, s): entry BEFORE s (fix 2026-10-07)
        rec[f'px_{xk}'] = -d * (ex / en - 1) * 1e4
        rec[f'net_{xk}'] = rec[f'px_{xk}'] + rec['fund'] - 10
    rows.append(rec)
x = pd.DataFrame(rows)
x.to_pickle(S + 'test_CpreX_tick.pkl')
print(f'pre-leg entry before s: ' + ', '.join(f"{k} {x[f'net_{k}'].notna().sum()}" for k in XS)); print(f'events with ticks: {len(x)} / {len(ev)}; f(s) known {x.fs.notna().mean():.1%}; '
      f'f(s) sign flipped vs f_prev {(np.sign(x.fs) != np.sign(x.fprev)).mean():.1%}')
print(f'funding received d·f(s): mean {x.fund.mean():.1f} bp, median {x.fund.median():.1f}  (|f_prev| mean {x.fprev.abs().mean() * 1e4:.1f})')
for xk in XS:
    y = x.dropna(subset=[f'net_{xk}', 'fund']); dn = y[f'net_{xk}'].groupby(y.day).mean()
    print(f'X {xk:4s} {"REGISTERED" if xk == "60s" else "reported  "}  n {len(y):4d} days {len(dn):3d}  price {y[f"px_{xk}"].mean():7.1f}  '
          f'funding {y.fund.mean():5.1f}  net event-mean {y[f"net_{xk}"].mean():6.1f}  day-mean {dn.mean():6.1f} '
          f'(t {dn.mean() / dn.std() * np.sqrt(len(dn)):5.2f})  worst {y[f"net_{xk}"].min():7.0f}  median {y[f"net_{xk}"].median():6.1f}')

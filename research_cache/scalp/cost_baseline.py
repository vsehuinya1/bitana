# Scalping cost-reality baseline (2026-10-06).
# Inputs: Binance USDT-M aggTrades (data.binance.vision) in research_cache/aggtrades/{SYM}/.
# Per symbol: effective spread, taker slippage beyond touch by order size, typical move at 10s..1h,
# and maker markouts (what a passive fill earns vs the later mid, before fees).
import glob, sys, zipfile
import numpy as np, pandas as pd

D = '/root/bitana/research_cache/'
H_MOVE = [10, 60, 300, 900, 3600]
H_MARK = [0, 10, 60, 300]          # seconds after the fill (0 = mid at end of the fill second)
SIZE_B = [0, 1e3, 1e4, 1e5, 1e6, np.inf]


def load(fn):
    with zipfile.ZipFile(fn) as z:
        df = pd.read_csv(z.open(z.namelist()[0]), usecols=['price', 'quantity', 'transact_time', 'is_buyer_maker'])
    df['ibm'] = df.is_buyer_maker.astype(str).str.lower().eq('true')
    return df.drop(columns='is_buyer_maker')


def grid(df, t0):
    """1s grid of mid proxy: (last buy-aggressor px + last sell-aggressor px)/2, both fresh <=10s, else last trade."""
    s = ((df.transact_time.values - t0) // 1000).astype(np.int64)
    n = 86400
    last = pd.Series(df.price.values).groupby(s).last().reindex(range(n)).ffill()
    out = {}
    for side, m in (('ask', ~df.ibm.values), ('bid', df.ibm.values)):
        g = pd.Series(df.price.values[m]).groupby(s[m]).last().reindex(range(n))
        ts = pd.Series(np.where(g.notna(), np.arange(n), np.nan)).ffill()
        out[side] = (g.ffill(), np.arange(n) - ts)
    a, aage = out['ask']; b, bage = out['bid']
    ok = (aage <= 10) & (bage <= 10) & (a >= b)
    return np.where(ok, (a + b) / 2, last).astype(float)


def orders(df):
    """Group aggTrades into taker orders: same ms timestamp + same side."""
    key = (df.transact_time.diff().ne(0) | df.ibm.ne(df.ibm.shift())).cumsum()
    df = df.assign(n=df.price * df.quantity, pq=df.price * df.quantity)
    g = df.groupby(key).agg(t=('transact_time', 'first'), ibm=('ibm', 'first'), p0=('price', 'first'),
                            p1=('price', 'last'), notional=('n', 'sum'), q=('quantity', 'sum'))
    g['vwap'] = g.notional / g.q
    g['slip_bps'] = (g.vwap / g.p0 - 1).abs() * 1e4
    return g.reset_index(drop=True)


def run(sym):
    files = sorted(glob.glob(f'{D}aggtrades/{sym}/*.zip'))
    days = [pd.Timestamp(f[-14:-4]) for f in files]
    t0s = [int(d.value // 10**6) for d in days]
    mids = []
    spreads, slips, marks = [], [], {h: [0.0, 0.0] for h in H_MARK}
    mark_size = {h: np.zeros((len(SIZE_B) - 1, 2)) for h in H_MARK}
    for f, t0 in zip(files, t0s):
        mids.append(grid(load(f), t0))
    mid = pd.Series(np.concatenate(mids)).ffill().bfill().values
    T0 = t0s[0]
    for f, t0 in zip(files, t0s):
        df = load(f)
        o = orders(df)
        # effective spread: buy order first px - preceding sell order first px within 100 ms (and vice versa)
        o_prev_side = o.ibm.shift(); dt = o.t.diff()
        m = o_prev_side.ne(o.ibm) & (dt <= 100) & o_prev_side.notna()
        sgn = np.where(o.ibm, -1, 1)          # taker buy: +1
        sp = (sgn * (o.p0 - o.p0.shift()) / o.p0 * 1e4)[m]
        spreads.append(sp.values)
        slips.append(o[['notional', 'slip_bps']].values)
        # maker markouts per aggTrade row, notional weighted; maker long when buyer is maker
        sec = ((df.transact_time.values - T0) // 1000).astype(np.int64)
        msign = np.where(df.ibm.values, 1.0, -1.0)
        notl = (df.price * df.quantity).values
        bucket = np.searchsorted(SIZE_B, notl, side='right') - 1
        for h in H_MARK:
            idx = sec + h
            ok = idx < len(mid)
            pnl = msign[ok] * (mid[idx[ok]] / df.price.values[ok] - 1) * 1e4
            w = notl[ok]
            marks[h][0] += (pnl * w).sum(); marks[h][1] += w.sum()
            for b in range(len(SIZE_B) - 1):
                mb = bucket[ok] == b
                mark_size[h][b] += [(pnl[mb] * w[mb]).sum(), w[mb].sum()]
    sp = np.concatenate(spreads); sl = np.vstack(slips)
    lm = np.log(mid)
    res = dict(sym=sym, days=len(files), px=float(np.nanmedian(mid)))
    pos = sp[sp > 0]
    res['spread_med_bps'] = float(np.median(pos)); res['spread_mean_bps'] = float(pos.mean())
    for lo, hi in zip(SIZE_B[:-1], SIZE_B[1:]):
        mm = (sl[:, 0] >= lo) & (sl[:, 0] < hi)
        res[f'slip_{int(lo)}'] = float(sl[mm, 1].mean()) if mm.any() else np.nan
        res[f'nord_{int(lo)}'] = int(mm.sum())
    for h in H_MOVE:
        r = (lm[h::h] - lm[:-h:h][:len(lm[h::h])]) * 1e4
        r = r[np.isfinite(r)]
        res[f'sd_{h}'] = float(r.std()); res[f'mabs_{h}'] = float(np.median(np.abs(r)))
    for h in H_MARK:
        res[f'mk_{h}'] = marks[h][0] / marks[h][1]
        for b in range(len(SIZE_B) - 1):
            s_, w_ = mark_size[h][b]
            res[f'mk_{h}_b{b}'] = s_ / w_ if w_ else np.nan
    return res


if __name__ == '__main__':
    out = [run(s) for s in sys.argv[1:]]
    pd.DataFrame(out).to_pickle(D + 'scalp/cost_baseline_' + '_'.join(s[:4] for s in sys.argv[1:]) + '.pkl')
    pd.set_option('display.width', 250)
    print(pd.DataFrame(out).set_index('sym').T.round(3).to_string())

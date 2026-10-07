# EXPLORATORY (not pre-registered): test A gross catch-up by BTC jump size.
import numpy as np, pandas as pd
exec(open('test_A_leadlag.py').read().split("rows = []")[0])
size = np.abs(r10[ev]); bins = [0, 8, 15, 25, 1e9]
out = []
for a in ALTS:
    A = lmid(a)
    for h in (10, 60, 300):
        g = sgn * (A[ev + 1 + h] - A[ev + 1]); lagdone = sgn * (A[ev] - A[ev - 10])
        for lo, hi in zip(bins[:-1], bins[1:]):
            m = (size >= lo) & (size < hi)
            out.append(dict(alt=a, h=h, bucket=f'{lo}-{hi}', n=int(m.sum()), gross=np.nanmean(g[m]), alt_same_window=np.nanmean(lagdone[m]), btc_move=size[m].mean()))
d = pd.DataFrame(out)
print(d.groupby(['h', 'bucket']).agg(n=('n', 'first'), btc_move=('btc_move', 'first'), alt_same_window=('alt_same_window', 'mean'), gross=('gross', 'mean')).round(2).to_string())

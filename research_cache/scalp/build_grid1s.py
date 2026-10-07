# Build per-day 1s grids from perp aggTrades: mid proxy, taker buy / sell notional per second.
# Output: research_cache/scalp/grid1s/{SYM}/{DATE}.npz (mid float64, buy float32, sell float32), 86400 rows.
import glob, os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cost_baseline import load, grid

D = '/root/bitana/research_cache/'
for sym in sys.argv[1:]:
    os.makedirs(f'{D}scalp/grid1s/{sym}', exist_ok=True)
    for f in sorted(glob.glob(f'{D}aggtrades/{sym}/*.zip')):
        day = f[-14:-4]; out = f'{D}scalp/grid1s/{sym}/{day}.npz'
        if os.path.exists(out):
            continue
        df = load(f); t0 = int(pd.Timestamp(day).value // 10**6)
        s = ((df.transact_time.values - t0) // 1000).astype(np.int64)
        n = (df.price * df.quantity).values
        buy = np.bincount(s[~df.ibm.values], n[~df.ibm.values], minlength=86400)[:86400]
        sell = np.bincount(s[df.ibm.values], n[df.ibm.values], minlength=86400)[:86400]
        np.savez(out, mid=grid(df, t0), buy=buy.astype(np.float32), sell=sell.astype(np.float32))
    print(sym, 'done', flush=True)

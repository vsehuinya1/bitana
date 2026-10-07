# Quoted spread and top-of-book size from the live bookTicker capture (bt_capture.py output).
import sys
import numpy as np, pandas as pd

df = pd.read_csv(sys.argv[1], names=['sym', 't', 'b', 'bq', 'a', 'aq'])
df['mid'] = (df.a + df.b) / 2
df['sp_bps'] = (df.a - df.b) / df.mid * 1e4
df['top_usd'] = np.minimum(df.bq * df.b, df.aq * df.a)
rows = []
for s, g in df.groupby('sym'):
    g = g.set_index(pd.to_datetime(g.t, unit='ms'))
    g1 = g.resample('1s').last().ffill()          # time-weighted, not update-weighted
    tick = np.diff(np.unique(np.round(g.b.values, 8))).min()
    rows.append(dict(sym=s, mins=round((g.t.iloc[-1] - g.t.iloc[0]) / 6e4, 1), px=g.mid.median(),
                     tick_bps=tick / g.mid.median() * 1e4, sp_med=g1.sp_bps.median(), sp_mean=g1.sp_bps.mean(),
                     pct_1tick=(g1.sp_bps <= tick / g.mid.median() * 1e4 * 1.01).mean() * 100,
                     top_usd_med=g1.top_usd.median(), top_usd_p10=g1.top_usd.quantile(.1)))
print(pd.DataFrame(rows).set_index('sym').round(3).to_string())

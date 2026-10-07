# Test A — BTC jump -> alt catch-up (rules: reports/scalping_prereg.md, registered before this run).
import glob
import numpy as np, pandas as pd

G = '/root/bitana/research_cache/scalp/grid1s/'
START, DISC_END, END = '2026-07-24', '2026-08-29', '2026-10-05'
ALTS = {'ETHUSDT': 10.1, 'SOLUSDT': 10.9, 'XRPUSDT': 10.7, 'DOGEUSDT': 11.2, '1000PEPEUSDT': 10.9}
H = [10, 30, 60, 300]
days = pd.date_range(START, END, freq='D')


def lmid(sym):
    parts = []
    for d in days:
        f = f'{G}{sym}/{d.date()}.npz'
        parts.append(np.load(f)['mid'] if glob.glob(f) else np.full(86400, np.nan))
    return np.log(pd.Series(np.concatenate(parts)).ffill(limit=30).values) * 1e4


B = lmid('BTCUSDT'); N = len(B)
r10 = np.full(N, np.nan); r10[10:] = B[10:] - B[:-10]
blk = pd.Series(B[::10]).diff()                                 # non-overlapping 10s returns on a fixed grid
sd_blk = blk.rolling(360, min_periods=300).std().shift(1).values  # previous hour, excluding current block
sig = np.repeat(sd_blk, 10)[:N]
sig = np.r_[np.full(10, np.nan), sig[:-10]]                       # σ from before the event window
cand = np.where(np.abs(r10) >= 5 * sig)[0]
ev, last = [], -10**9
for t in cand:
    if t - last >= 60 and t + 1 + max(H) < N:
        ev.append(t); last = t
ev = np.array(ev); sgn = np.sign(r10[ev])
day = days[ev // 86400]
print(f'events: {len(ev)} on {len(set(day))} days ({len(ev) / len(days):.1f}/day); median |r_B| {np.median(np.abs(r10[ev])):.1f} bp')

rows = []
def add(name, var, h, x, cost):
    df = pd.DataFrame({'day': day, 'g': x}).dropna()
    for per, m in (('disc', df.day <= DISC_END), ('hold', df.day > DISC_END)):
        d = df[m]
        if d.empty:
            continue
        dd = d.groupby('day').g.mean() - cost
        rows.append(dict(trade=name, var=var, h=h, per=per, n=len(d), days=len(dd), gross=d.g.mean(),
                         net=dd.mean(), t=dd.mean() / dd.std() * np.sqrt(len(dd))))

for h in H:
    add('BTC own (control)', 'all', h, sgn * (B[ev + 1 + h] - B[ev + 1]), 10.1)
pooled = {h: {'all': [], 'lag': []} for h in H}
per_alt = []
for a, c in ALTS.items():
    A = lmid(a)
    rA = sgn * (A[ev] - A[ev - 10])
    lag = rA < 0.5 * np.abs(r10[ev])
    for h in H:
        g = sgn * (A[ev + 1 + h] - A[ev + 1])
        pooled[h]['all'].append(g); pooled[h]['lag'].append(np.where(lag, g, np.nan))
        per_alt.append(dict(alt=a, h=h, gross_all=np.nanmean(g), gross_lag=np.nanmean(g[lag]), n_lag=int(lag.sum())))
for h in H:
    for var in ('all', 'lag'):
        x = np.nanmean(np.vstack(pooled[h][var]), axis=0)   # per event: mean gross across alts
        add('alts pooled', var, h, x, np.mean(list(ALTS.values())))
pd.set_option('display.width', 200)
out = pd.DataFrame(rows); out.to_pickle('/root/bitana/research_cache/scalp/test_A.pkl')
print(out.round(2).to_string(index=False))
print(pd.DataFrame(per_alt).round(2).to_string(index=False))

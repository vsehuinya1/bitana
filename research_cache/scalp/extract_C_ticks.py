# Download the event coin-days for C-tick, keep only [s-5m, s+65m] trades per event, delete each zip after use.
# Memory-light (see memory note research-resource-limits): one worker, chunked CSV reads of 4 columns, pauses when the
# box has < 2 GB available.
import os, sys, time, zipfile, urllib.request
import numpy as np, pandas as pd

S = '/root/bitana/research_cache/scalp/'; TMP = '/root/bitana/research_cache/dl_tmp_C/'
EVF = sys.argv[3] if len(sys.argv) > 3 else 'robust_C_events.pkl'
OUT = sys.argv[4] if len(sys.argv) > 4 else 'C_ticks'
ev = pd.read_pickle(S + EVF)[['sym', 'time']]
ev['d'] = ev.time.dt.strftime('%Y-%m-%d')


def mem_avail_mb():
    with open('/proc/meminfo') as f:
        for line in f:
            if line.startswith('MemAvailable'):
                return int(line.split()[1]) // 1024


done = miss = 0
K, NW = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) > 2 else (0, 1)
for n, ((sym, d), g) in enumerate(ev.groupby(['sym', 'd'])):
    if n % NW != K:
        continue
    outs = [f'{S}{OUT}/{sym}_{int(t.value // 10**6)}.npz' for t in g.time]
    if all(os.path.exists(o) for o in outs):
        continue
    while mem_avail_mb() < 2000:
        print('low memory, waiting', mem_avail_mb(), flush=True); time.sleep(60)
    url = f'https://data.binance.vision/data/futures/um/daily/aggTrades/{sym}/{sym}-aggTrades-{d}.zip'
    fn = f'{TMP}{sym}-{d}.zip'
    for a in range(3):
        try:
            urllib.request.urlretrieve(url, fn); break
        except Exception as e:
            err = e; time.sleep(2 + a)
    else:
        print('miss', sym, d, err, flush=True); miss += 1; continue
    ss = np.array([t.value // 10**6 for t in g.time])
    parts = {i: [] for i in range(len(ss))}
    with zipfile.ZipFile(fn) as z:
        for ch in pd.read_csv(z.open(z.namelist()[0]), header=None, usecols=[1, 2, 5, 6], chunksize=500_000, dtype=str):
            t = pd.to_numeric(ch[5], errors='coerce'); ok = t.notna().values
            t = t.values[ok].astype(np.int64)
            for i, s in enumerate(ss):
                m = (t >= s - 300_000) & (t <= s + 3_900_000)
                if m.any():
                    c = ch[ok][m]
                    parts[i].append((t[m], c[1].astype(float).values, c[2].astype(float).values,
                                     c[6].str.lower().eq('true').values))
    for i, o in enumerate(outs):
        p = parts[i]
        if p:
            np.savez(o, t=np.concatenate([x[0] for x in p]), p=np.concatenate([x[1] for x in p]),
                     q=np.concatenate([x[2] for x in p]), ibm=np.concatenate([x[3] for x in p]))
        else:
            np.savez(o, t=np.array([], np.int64), p=np.array([]), q=np.array([]), ibm=np.array([], bool))
    os.remove(fn); done += 1
    if done % 50 == 0:
        print('coin-days', done, 'mem avail', mem_avail_mb(), flush=True)
print('finished', done, 'miss', miss, flush=True)

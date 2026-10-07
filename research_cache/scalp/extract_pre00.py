# Fill the pre-settlement window of 00:00 UTC events. extract_C_ticks.py reads only the settlement day's file, so for a
# settlement at 00:00 the trades in [s-5m, s) are in the PREVIOUS day's file and were missing (found 2026-10-07 by the
# C-pre replay). Merges [s-5m, s) from that file into each event's npz; skips events that already have trades before s.
# Memory-light like extract_C_ticks.py. Usage: extract_pre00.py K NW events.pkl outdir
import os, sys, time, zipfile, urllib.request
import numpy as np, pandas as pd

S = '/root/bitana/research_cache/scalp/'; TMP = '/root/bitana/research_cache/dl_tmp_C/'
K, NW, EVF, OUT = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3], sys.argv[4]
ev = pd.read_pickle(S + EVF)[['sym', 'time']]
ev = ev[(ev.time.dt.hour == 0) & (ev.time.dt.minute == 0)].copy()
ev['prev'] = (ev.time - pd.Timedelta('1D')).dt.strftime('%Y-%m-%d')


def mem_avail_mb():
    with open('/proc/meminfo') as f:
        for line in f:
            if line.startswith('MemAvailable'):
                return int(line.split()[1]) // 1024


done = miss = skip = 0
for n, ((sym, d), g) in enumerate(ev.groupby(['sym', 'prev'])):
    if n % NW != K:
        continue
    todo = []
    for t in g.time:
        s = int(t.value // 10**6); o = f'{S}{OUT}/{sym}_{s}.npz'
        if not os.path.exists(o):
            print('no npz yet', o, flush=True); miss += 1; continue
        z = np.load(o)
        if (z['t'] < s).any():
            skip += 1; continue
        todo.append((s, o, {k: z[k] for k in ('t', 'p', 'q', 'ibm')}))
    if not todo:
        continue
    while mem_avail_mb() < 2000:
        print('low memory, waiting', mem_avail_mb(), flush=True); time.sleep(60)
    url = f'https://data.binance.vision/data/futures/um/daily/aggTrades/{sym}/{sym}-aggTrades-{d}.zip'
    fn = f'{TMP}{sym}-{d}-prev.zip'
    for a in range(3):
        try:
            urllib.request.urlretrieve(url, fn); break
        except Exception as e:
            err = e; time.sleep(2 + a)
    else:
        print('miss', sym, d, err, flush=True); miss += 1; continue
    parts = {s: [] for s, _, _ in todo}
    with zipfile.ZipFile(fn) as zf:
        for ch in pd.read_csv(zf.open(zf.namelist()[0]), header=None, usecols=[1, 2, 5, 6], chunksize=500_000, dtype=str):
            t = pd.to_numeric(ch[5], errors='coerce'); ok = t.notna().values
            t = t.values[ok].astype(np.int64)
            for s in parts:
                m = (t >= s - 300_000) & (t < s)
                if m.any():
                    c = ch[ok][m]
                    parts[s].append((t[m], c[1].astype(float).values, c[2].astype(float).values,
                                     c[6].str.lower().eq('true').values))
    for s, o, z in todo:
        p = parts[s]
        if p:
            np.savez(o, t=np.concatenate([x[0] for x in p] + [z['t']]), p=np.concatenate([x[1] for x in p] + [z['p']]),
                     q=np.concatenate([x[2] for x in p] + [z['q']]), ibm=np.concatenate([x[3] for x in p] + [z['ibm']]))
    os.remove(fn); done += 1
    if done % 50 == 0:
        print('coin-days', done, 'mem avail', mem_avail_mb(), flush=True)
print('finished', done, 'skip', skip, 'miss', miss, flush=True)

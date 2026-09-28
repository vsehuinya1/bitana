"""Incremental 5m kline cache for the paper tracks (wick catcher, perp discount). Public data only, no account calls.
Forward reads fetch only bars newer than the cache (fapi for perps, api.binance.com for spot); --validate windows use
the data.binance.vision monthly archive (no API weight). Cache: logs/paper_cache/ (git-ignored)."""
import io
import json
import os
import ssl
import time
import urllib.error
import urllib.request
import zipfile

import numpy as np
import pandas as pd

CACHE = '/root/bitana/logs/paper_cache/'
CTX = ssl.create_default_context()
BASE = {'perp': 'https://fapi.binance.com/fapi/v1/klines', 'spot': 'https://api.binance.com/api/v3/klines'}
COLS = ['o', 'h', 'l', 'c', 'v', 'tb']   # tb = taker-buy base volume (wick signature lines, 2026-09-28)


def _frame(rows):
    if not rows:
        return pd.DataFrame(columns=COLS, dtype=float)
    a = np.array(rows, dtype=float)
    df = pd.DataFrame(a[:, 1:7], columns=COLS, index=pd.to_datetime(a[:, 0], unit='ms', utc=True))
    return df[~df.index.duplicated(keep='last')].sort_index()


def live(sym, market, start):
    """Closed 5m bars from `start` to now, cached incrementally."""
    os.makedirs(CACHE, exist_ok=True)
    fn = f'{CACHE}{market}_{sym}_5m_v2.pkl'                          # v2: with taker-buy volume
    df = pd.read_pickle(fn) if os.path.exists(fn) else pd.DataFrame(columns=COLS, dtype=float)
    if len(df) and df.index[0] > start:
        df = pd.DataFrame(columns=COLS, dtype=float)                      # cache starts too late: rebuild
    t = int(((df.index[-1] + pd.Timedelta(minutes=5)) if len(df) else start).timestamp() * 1000)
    now = int(time.time() * 1000); rows = []
    limit = 1000 if market == 'perp' else 1000
    while t < now:
        for a in range(3):
            try:
                r = json.load(urllib.request.urlopen(f'{BASE[market]}?symbol={sym}&interval=5m&startTime={t}&limit={limit}',
                                                     context=CTX, timeout=30)); break
            except Exception:
                r = None; time.sleep(1 + a)
        if not r:
            break
        rows += [[float(k[0])] + [float(x) for x in k[1:6]] + [float(k[9])] for k in r if k[6] < now]
        t = r[-1][0] + 300000
        if len(r) < limit:
            break
        time.sleep(0.25)
    if rows:
        df = pd.concat([df, _frame(rows)]); df = df[~df.index.duplicated(keep='last')].sort_index()
        df.to_pickle(fn)
    return df[df.index >= start]


def archive(sym, market, months):
    """Monthly 5m archive frames (validation windows)."""
    root = 'futures/um' if market == 'perp' else 'spot'
    rows = []
    for m in months:
        u = f'https://data.binance.vision/data/{root}/monthly/klines/{sym}/5m/{sym}-5m-{m}.zip'
        b = None
        for a in range(3):
            try:
                b = urllib.request.urlopen(u, context=CTX, timeout=60).read(); break
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    break
                time.sleep(2 + a)
            except Exception:
                time.sleep(2 + a)
        if not b:
            continue
        z = zipfile.ZipFile(io.BytesIO(b))
        for line in z.read(z.namelist()[0]).decode().splitlines():
            if line and line[0].isdigit():
                f = line.split(','); t = float(f[0]); t = t / 1000 if t > 1e14 else t     # 2025+ spot files: microseconds
                rows.append([t] + [float(x) for x in f[1:6]] + [float(f[9])])
    return _frame(rows)

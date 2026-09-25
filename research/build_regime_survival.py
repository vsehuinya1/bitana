#!/usr/bin/env python3
"""Build dashboard/regime_survival.json: empirical odds that the live BTC regime survives the next k 4h closes.

Method (2026-09-25): replay the LIVE classifier on BTCUSDT 4h history since 2019-09, using the same 249
closed-bar window (engines.btc_regime.compute_regime_snapshot) and the same ADXBAND deadband state machine
as main._refresh_btc_regime (enter bull/bear at ADX >= 25.5, revert to neutral below 24.5, flip sign while
ADX >= 24.5). For every bull/bear bar, bucket by ADX band x 3-bar ADX slope and record whether the state held
at each of the next k = 1..6 closes. The dashboard picks the bucket matching "now" and the k closes left
before the session ends.

Read-only: public Binance klines, no account calls. Re-run weekly or after a regime-classifier change.
"""
import json
import os
import ssl
import sys
import time
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, '/root/bitana')
os.environ.pop('API_FOOTBALL_KEY', None)
from core.models import Candle  # noqa: E402
from engines.btc_regime import compute_regime_snapshot  # noqa: E402

OUT = '/root/bitana/dashboard/regime_survival.json'
ADX_BANDS = [(24.5, 28), (28, 32), (32, 40), (40, 999)]
SLOPE = lambda d3: 'falling' if d3 <= -4 else ('rising' if d3 >= 4 else 'flat')  # noqa: E731
KMAX = 6


def fetch():
    ctx, rows = ssl.create_default_context(), []
    t = int(datetime(2019, 9, 8, tzinfo=timezone.utc).timestamp() * 1000)
    while True:
        url = f'https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=4h&startTime={t}&limit=1500'
        r = json.load(urllib.request.urlopen(url, context=ctx, timeout=30))
        if not r:
            break
        rows += r
        t = r[-1][0] + 4 * 3600 * 1000
        time.sleep(0.2)
        if len(r) < 1500:
            break
    now = int(time.time() * 1000)
    return [Candle(symbol='BTCUSDT', timeframe='4h', open_time=datetime.fromtimestamp(k[0] / 1000, timezone.utc),
                   close_time=datetime.fromtimestamp(k[6] / 1000, timezone.utc), open=float(k[1]), high=float(k[2]),
                   low=float(k[3]), close=float(k[4]), volume=float(k[5])) for k in rows if k[6] < now]


def replay(C):
    out, prev = [], None
    for i in range(248, len(C)):
        s = compute_regime_snapshot(C[i - 248:i + 1])
        if prev in ('bull', 'bear'):
            st = 'neutral' if (s.adx is not None and s.adx < 24.5) else (s.state if s.state in ('bull', 'bear') else prev)
        else:
            st = s.state if (s.state in ('bull', 'bear') and (s.adx or 0) >= 25.5) else 'neutral'
        out.append((st, s.adx))
        prev = st
    return out


def main():
    series = replay(fetch())
    cells = {}
    for i in range(3, len(series) - KMAX):
        st, adx = series[i]
        if st not in ('bull', 'bear') or adx is None:
            continue
        band = next((f'{lo}-{hi if hi < 999 else "+"}' for lo, hi in ADX_BANDS if lo <= adx < hi), None)
        if band is None:
            continue
        key = f'{st}|{band}|{SLOPE(adx - series[i - 3][1])}'
        c = cells.setdefault(key, {'n': 0, 'held': [0] * KMAX})
        c['n'] += 1
        for k in range(1, KMAX + 1):
            if all(series[i + j][0] == st for j in range(1, k + 1)):
                c['held'][k - 1] += 1
    table = {k: {'n': v['n'], 'p': [round(h / v['n'], 3) for h in v['held']]} for k, v in cells.items()}
    json.dump({'built': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'bars': len(series),
               'bands': [f'{lo}-{hi if hi < 999 else "+"}' for lo, hi in ADX_BANDS], 'kmax': KMAX, 'table': table},
              open(OUT, 'w'), indent=1)
    print(f'wrote {OUT}: {len(table)} cells from {len(series)} bars')


if __name__ == '__main__':
    main()

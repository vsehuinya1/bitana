#!/usr/bin/env python3
"""ADX-band + regime-age measurement (read-only).

Uses the ENGINE's compute_regime_snapshot (no reimplementation) over a
sliding 4h window -> engine-exact ADX/state/dist series.
Answers: (1) how much boundary flap the ADXBAND kills vs a 25.0 line,
(2) whether old neutral episodes with stretched dist resolve bull/bear.
"""
import json
import os
import sys
import time
import urllib.request

os.environ.pop("API_FOOTBALL_KEY", None)
sys.path.insert(0, "/root/bitana")
from engines.btc_regime import compute_regime_snapshot  # noqa: E402
from core.models import Candle  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

URL = ("https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT"
       "&interval=4h&limit=1000")
raw = json.loads(urllib.request.urlopen(urllib.request.Request(
    URL, headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read())
print(f"bars={len(raw)} "
      f"span={datetime.fromtimestamp(raw[0][0]/1000, tz=timezone.utc):%m-%d}"
      f"→{datetime.fromtimestamp(raw[-1][0]/1000, tz=timezone.utc):%m-%d}")

candles = [
    Candle(symbol="BTCUSDT", timeframe="4h",
           open_time=datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc),
           close_time=datetime.fromtimestamp(k[6] / 1000, tz=timezone.utc),
           open=float(k[1]), high=float(k[2]), low=float(k[3]),
           close=float(k[4]), volume=float(k[5]))
    for k in raw
]

# sliding engine-exact snapshots (min 260 bars so EMA200 is warm)
t0 = time.time()
series = []  # (idx, close_time, raw_state, adx, dist, ret_next_4h)
for i in range(260, len(candles)):
    s = compute_regime_snapshot(candles[: i + 1])
    nxt = (float(candles[i + 1].close) / float(candles[i].close) - 1) * 100 \
        if i + 1 < len(candles) else None
    series.append((i, candles[i].close_time, s.state, s.adx,
                   s.distance_from_ema_pct, nxt))
print(f"snapshots={len(series)} computed in {time.time()-t0:.1f}s")

# ---- (1) flap comparison: raw 25.0 line vs hysteresis bands ----
def sim_band(enter, revert):
    """main.py state machine over the raw series."""
    states, prev = [], "neutral"
    for _, _, st, adx, _, _ in series:
        if prev in ("bull", "bear"):
            if adx is not None and adx < revert:
                prev = "neutral"
            elif st in ("bull", "bear"):
                prev = st
        else:
            prev = st if (st in ("bull", "bear") and (adx or 0) >= enter) \
                else "neutral"
        states.append(prev)
    return states


def flips(states):
    return sum(1 for a, b in zip(states, states[1:]) if a != b)


raw_flips = flips([s[2] for s in series])
days = (series[-1][1] - series[0][1]).total_seconds() / 86400
print(f"\nspan={days:.0f}d  RAW 25.0-line flips: {raw_flips} "
      f"({raw_flips/days*30:.1f}/30d)")
for enter, revert in ((25.5, 24.5), (26.0, 24.5), (25.0, 24.0), (26.0, 24.0)):
    st = sim_band(enter, revert)
    f = flips(st)
    # lag cost: bars where band=neutral but raw=trending, sum |next-bar ret|
    lag_bars = sum(1 for s, b in zip(series, st)
                   if b == "neutral" and s[2] in ("bull", "bear"))
    print(f"BAND {enter}/{revert}: flips={f} ({f/days*30:.1f}/30d) "
          f"neutral-while-raw-trending bars={lag_bars}")

# ---- (2) episode age: does old neutral resolve bull or bear? ----
st_band = sim_band(25.5, 24.5)
eps = []  # (state, start_idx, end_idx, len)
cur, start = st_band[0], 0
for j in range(1, len(st_band)):
    if st_band[j] != cur:
        eps.append((cur, start, j - 1, j - start))
        cur, start = st_band[j], j
eps.append((cur, start, len(st_band) - 1, len(st_band) - start))
import statistics as stat
for name in ("bull", "neutral", "bear"):
    lens = [e[3] for e in eps if e[0] == name]
    if lens:
        print(f"\n{name}: n={len(lens)} median={stat.median(lens):.0f} "
              f"max={max(lens)} bars (4h)")
    # closed episodes: what follows?
    nxt_states = [st_band[e[2] + 1] for e in eps if e[0] == name
                  and e[2] + 1 < len(st_band)]
    from collections import Counter
    print(f"  next-state after close: {dict(Counter(nxt_states))}")

# old-neutral episodes (>=20 bars): next state + fwd 24h (6-bar) return
print("\nneutral episodes >=20 bars (closed):")
for e in eps:
    if e[0] == "neutral" and e[3] >= 20 and e[2] + 7 < len(series):
        nxt = st_band[e[2] + 1]
        seg = series[e[2] + 1: e[2] + 7]
        fwd = 1.0
        for s in seg:
            fwd *= 1 + (s[5] or 0) / 100
        dist_e = [s[4] for s in series[e[1]: e[2] + 1] if s[4] is not None]
        print(f"  ended {series[e[2]][1]:%m-%d %H:%M} len={e[3]} "
              f"dist_end={dist_e[-1]:+.1f}% -> {nxt.upper():7s} "
              f"fwd24h={(fwd-1)*100:+.2f}%")
# current episode
cur_e = eps[-1]
print(f"\nCURRENT: {cur_e[0]} age={cur_e[3]} bars "
      f"(started {series[cur_e[1]][1]:%m-%d %H:%M}), "
      f"dist_now={series[-1][4]:+.2f}%, adx_now={series[-1][3]}")
old_neutral = [e for e in eps if e[0] == 'neutral' and e[3] >= 20]
print(f"neutral episodes >=20 bars: {len(old_neutral)} of "
      f"{sum(1 for e in eps if e[0]=='neutral')} total")

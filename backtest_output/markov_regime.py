"""Markov regime-transition analysis on BTC 4h bull/neutral/bear states.

Mirrors engines/btc_regime.py logic (4h 200EMA + ADX14>25) over ~2 years of
BTCUSDT perp klines, then estimates a first-order Markov chain:
  - transition matrix at 4h-bar granularity
  - dwell times (empirical run lengths vs geometric implied)
  - stationary distribution
  - k-step forecasts: P(regime) at Monday Asia open / NY open from now
  - Markov-assumption check: P(stay) binned by regime age
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import numpy as np
import requests

STATES = ("bear", "neutral", "bull")
IDX = {s: i for i, s in enumerate(STATES)}


def fetch_klines_4h(total_bars: int = 4600) -> list[dict]:
    out = []
    end_ms = int(time.time() * 1000)
    while len(out) < total_bars:
        limit = min(1500, total_bars - len(out) + 10)
        r = requests.get(
            "https://fapi.binance.com/fapi/v1/klines",
            params={"symbol": "BTCUSDT", "interval": "4h", "limit": limit, "endTime": end_ms},
            timeout=20,
        )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        out = batch + out
        end_ms = batch[0][0] - 1
        time.sleep(0.3)
    # dedupe, sort
    seen, dedup = set(), []
    for k in sorted(out, key=lambda x: x[0]):
        if k[0] not in seen:
            seen.add(k[0])
            dedup.append(k)
    return [
        dict(open_time=k[0], high=float(k[2]), low=float(k[3]), close=float(k[4]))
        for k in dedup
    ]


def adx_series(highs, lows, closes, period=14):
    """Wilder ADX matching engines/swing_break_engine._adx_series."""
    n = len(closes)
    if n < period * 2 + 1:
        return [0.0] * n
    pdm, ndm, trs = [0.0], [0.0], [0.0]
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        dn = lows[i - 1] - lows[i]
        pdm.append(up if up > dn and up > 0 else 0.0)
        ndm.append(dn if dn > up and dn > 0 else 0.0)
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))

    def smooth(vals, p):
        s = [0.0] * (p + 1)
        s[p] = sum(vals[1:p + 1])
        for i in range(p + 1, len(vals)):
            s.append(s[-1] - s[-1] / p + vals[i])
        return s

    s_pdm, s_ndm, s_tr = smooth(pdm, period), smooth(ndm, period), smooth(trs, period)
    dx = [0.0] * n
    for i in range(period, n):
        if i >= len(s_tr) or s_tr[i] == 0:
            continue
        pdi = 100 * s_pdm[i] / s_tr[i] if i < len(s_pdm) else 0
        ndi = 100 * s_ndm[i] / s_tr[i] if i < len(s_ndm) else 0
        denom = pdi + ndi
        dx[i] = abs(pdi - ndi) / denom * 100 if denom > 0 else 0
    adx = [0.0] * n
    si = period * 2
    if si < n:
        adx[si] = sum(dx[period:si + 1]) / (period + 1)
        for i in range(si + 1, n):
            adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period
    return adx


def ema_series(closes, span=200):
    alpha = 2.0 / (span + 1)
    out = [closes[0]]
    for c in closes[1:]:
        out.append(alpha * c + (1 - alpha) * out[-1])
    return out


print("Fetching ~2y of BTCUSDT 4h klines...")
kl = fetch_klines_4h(4600)
print(f"bars: {len(kl)}  span: "
      f"{datetime.fromtimestamp(kl[0]['open_time']/1000, tz=timezone.utc):%Y-%m-%d} -> "
      f"{datetime.fromtimestamp(kl[-1]['open_time']/1000, tz=timezone.utc):%Y-%m-%d %H:%M}")

highs = [k["high"] for k in kl]
lows = [k["low"] for k in kl]
closes = [k["close"] for k in kl]
ema200 = ema_series(closes, 200)
adx14 = adx_series(highs, lows, closes)

WARMUP = 200
states = []
for i in range(WARMUP, len(kl)):
    if adx14[i] <= 25:
        s = "neutral"
    elif closes[i] > ema200[i]:
        s = "bull"
    else:
        s = "bear"
    states.append(s)
print(f"regime observations: {len(states)} (current: {states[-1]})")

# ── Transition matrix ──
counts = np.zeros((3, 3))
for a, b in zip(states[:-1], states[1:]):
    counts[IDX[a], IDX[b]] += 1
P = counts / counts.sum(axis=1, keepdims=True)
print("\nTRANSITION MATRIX (4h bar, rows=from):")
print(f"{'':10s}" + "".join(f"{s:>10s}" for s in STATES))
for i, s in enumerate(STATES):
    print(f"{s:10s}" + "".join(f"{P[i, j]:10.4f}" for j in range(3)))

occ = np.array([states.count(s) for s in STATES], dtype=float)
print("\nEmpirical occupancy:", {s: f"{100 * c / len(states):.1f}%" for s, c in zip(STATES, occ)})

# stationary distribution
evals, evecs = np.linalg.eig(P.T)
stat = np.real(evecs[:, np.argmin(np.abs(evals - 1))])
stat = stat / stat.sum()
print("Stationary dist:   ", {s: f"{100 * v:.1f}%" for s, v in zip(STATES, stat)})

# ── Dwell times ──
print("\nDWELL TIMES (runs):")
runs = {s: [] for s in STATES}
cur, ln = states[0], 1
for s in states[1:]:
    if s == cur:
        ln += 1
    else:
        runs[cur].append(ln)
        cur, ln = s, 1
runs[cur].append(ln)
for s in STATES:
    r = runs[s]
    p_stay = P[IDX[s], IDX[s]]
    implied = 1 / (1 - p_stay) if p_stay < 1 else float("inf")
    print(f"  {s:8s} runs={len(r):3d} mean={np.mean(r):5.1f} bars ({np.mean(r)/6:.1f}d) "
          f"median={np.median(r):4.0f} max={max(r):3d} | geometric implied mean={implied:.1f} bars")

# ── Markov check: P(stay) by regime age ──
print("\nMARKOV CHECK — P(stay) by regime age (bars in state so far):")
age_bins = {s: {"1-3": [0, 0], "4-9": [0, 0], "10-24": [0, 0], "25+": [0, 0]} for s in STATES}
cur, age = states[0], 1
for nxt in states[1:]:
    b = "1-3" if age <= 3 else "4-9" if age <= 9 else "10-24" if age <= 24 else "25+"
    age_bins[cur][b][1] += 1
    if nxt == cur:
        age_bins[cur][b][0] += 1
        age += 1
    else:
        cur, age = nxt, 1
for s in STATES:
    parts = []
    for b, (stay, tot) in age_bins[s].items():
        if tot >= 10:
            parts.append(f"{b}: {100 * stay / tot:.0f}% (n={tot})")
    print(f"  {s:8s} " + " | ".join(parts))

# ── Forecasts from current state ──
now = datetime.now(timezone.utc)
mon_asia = datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc)
mon_ny = datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc)
bars_to_asia = max(1, round((mon_asia - now).total_seconds() / 14400))
bars_to_ny = max(1, round((mon_ny - now).total_seconds() / 14400))

v = np.zeros(3)
v[IDX[states[-1]]] = 1.0
Pk_asia = v @ np.linalg.matrix_power(P, bars_to_asia)
Pk_ny = v @ np.linalg.matrix_power(P, bars_to_ny)
print(f"\nFORECAST from current='{states[-1]}' (now {now:%a %H:%M} UTC):")
print(f"  Monday Asia open ({bars_to_asia} bars): " +
      ", ".join(f"{s}={100 * Pk_asia[IDX[s]]:.0f}%" for s in STATES) +
      f" | tradeable(neutral+bear)={100 * (Pk_asia[0] + Pk_asia[1]):.0f}%")
print(f"  Monday NY open  ({bars_to_ny} bars): " +
      ", ".join(f"{s}={100 * Pk_ny[IDX[s]]:.0f}%" for s in STATES) +
      f" | tradeable(neutral+bear)={100 * (Pk_ny[0] + Pk_ny[1]):.0f}%")

# expected regime mix over next week (42 bars)
mix = np.zeros(3)
vk = v.copy()
for _ in range(42):
    vk = vk @ P
    mix += vk
mix /= 42
print(f"  Next-week expected mix: " + ", ".join(f"{s}={100 * mix[IDX[s]]:.0f}%" for s in STATES))

# conditional: where do you GO when leaving each state
print("\nEXIT DESTINATIONS (given a transition happens):")
for i, s in enumerate(STATES):
    row = P[i].copy()
    row[i] = 0
    if row.sum() > 0:
        row = row / row.sum()
        print(f"  from {s:8s}: " + ", ".join(f"-> {STATES[j]} {100 * row[j]:.0f}%" for j in range(3) if j != i))

# recent regime history (last 3 weeks, daily-ish sampling)
print("\nRECENT REGIME PATH (last 21 days, one label per day at 00:00 bar):")
day_states = {}
for i, k in enumerate(kl[WARMUP:]):
    dt = datetime.fromtimestamp(k["open_time"] / 1000, tz=timezone.utc)
    if dt.hour == 0:
        day_states[f"{dt:%m-%d}"] = states[i]
recent = list(day_states.items())[-21:]
print("  " + " ".join(f"{d}:{s[:2]}" for d, s in recent))

"""BTC regime check — same engine path as the live gate (engines/btc_regime.py).

2026-08-30: replaced inline simplified ADX (14-bar rolling mean) with the engine's
Wilder-smoothed _adx_series. The old method diverged from live and misreported
state (printed 'bull' while the live gate was already 'neutral' since 08-29 20:00Z).
"""
import requests
import numpy as np
from datetime import datetime, timezone

from core.models import Candle
from engines.btc_regime import compute_regime_snapshot, compute_regime_age_bars
from engines.swing_break_engine import _adx_series

url = 'https://fapi.binance.com/fapi/v1/klines'
params = {'symbol': 'BTCUSDT', 'interval': '4h', 'limit': 500}
klines = requests.get(url, params=params, timeout=10).json()


def to_candle(k, closed=True):
    return Candle(
        symbol='BTCUSDT', timeframe='4h',
        open_time=datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc),
        close_time=datetime.fromtimestamp(k[6] / 1000, tz=timezone.utc),
        open=float(k[1]), high=float(k[2]), low=float(k[3]),
        close=float(k[4]), volume=float(k[5]), is_closed=closed,
    )


candles = [to_candle(k) for k in klines[:-1]] + [to_candle(klines[-1], closed=False)]

snap = compute_regime_snapshot(candles)
closes = np.array([c.close for c in candles], dtype=float)

# Rolling EMA200 with the same recursion as engines.liq_cluster_engine_v5._ema
alpha = 2.0 / 201
ema_arr = np.empty_like(closes)
ema_arr[0] = closes[0]
for i in range(1, len(closes)):
    ema_arr[i] = alpha * closes[i] + (1 - alpha) * ema_arr[i - 1]
ema200 = ema_arr[-1]

print(f'Total 4h candles: {len(candles)}')
print(f'Current price: {closes[-1]:.2f}')
print(f'Current EMA200: {ema200:.2f}')
print(f'Distance: {snap.distance_from_ema_pct:.4f}%')
print(f'Current ADX: {snap.adx:.2f} (neutral threshold: 25)')
print(f'Current state: {snap.state}')
age = compute_regime_age_bars(candles)
print(f'Regime age (4h bars): {age} = {age * 4 / 24:.1f} days' if age is not None else 'Regime age: n/a')

# Transitions via the same method the live gate uses
adx = np.array(_adx_series(candles, 14), dtype=float)
states = []
for i in range(len(candles)):
    if i < 199:
        states.append(None)
        continue
    if adx[i] <= 25:
        states.append('neutral')
    elif closes[i] > ema_arr[i]:
        states.append('bull')
    else:
        states.append('bear')

print('\nRegime transitions (engine method):')
prev = None
for i, s in enumerate(states):
    if s is not None and s != prev:
        dt = candles[i].open_time.strftime('%Y-%m-%d %H:%M')
        print(f'  {dt}: {prev} -> {s}')
        prev = s

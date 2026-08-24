import requests
import numpy as np
from datetime import datetime, timezone

url = 'https://fapi.binance.com/fapi/v1/klines'
params = {'symbol': 'BTCUSDT', 'interval': '4h', 'limit': 500}
klines = requests.get(url, params=params, timeout=10).json()

closes = np.array([float(k[4]) for k in klines])
highs_all = np.array([float(k[2]) for k in klines])
lows_all = np.array([float(k[3]) for k in klines])

# EMA200
def ema(arr, period):
    alpha = 2.0 / (period + 1)
    result = np.zeros_like(arr)
    result[0] = arr[0]
    for i in range(1, len(arr)):
        result[i] = alpha * arr[i] + (1 - alpha) * result[i-1]
    return result

ema200 = ema(closes, 200)

# Compute regime at each point from bar 200 onwards
states = []
for i in range(199, len(closes)):
    price = closes[i]
    e = ema200[i]
    
    # ADX14 - compute from last 14 bars
    if i >= 13:
        hs = highs_all[i-13:i+1]
        ls = lows_all[i-13:i+1]
        cs = closes[i-13:i+1]
        
        # True Range
        tr1 = hs - ls
        tr2 = np.abs(hs[1:] - cs[:-1])
        tr3 = np.abs(ls[1:] - cs[:-1])
        tr = np.maximum(np.maximum(tr1[1:], tr2), tr3)
        tr = np.concatenate([[hs[0] - ls[0]], tr])
        atr = np.mean(tr)
        
        # DM
        up_move = hs[1:] - hs[:-1]
        down_move = ls[:-1] - ls[1:]
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        plus_di = 100 * np.mean(plus_dm) / atr if atr > 0 else 0
        minus_di = 100 * np.mean(minus_dm) / atr if atr > 0 else 0
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di) if (plus_di + minus_di) > 0 else 0
        adx = dx
    else:
        adx = 0
    
    if adx <= 25:
        state = 'neutral'
    elif price > e:
        state = 'bull'
    else:
        state = 'bear'
    states.append(state)

print(f'Total 4h candles: {len(closes)}')
print(f'Current price: {closes[-1]:.2f}')
print(f'Current EMA200: {ema200[-1]:.2f}')
print(f'Distance: {(closes[-1] - ema200[-1]) / ema200[-1] * 100:.4f}%')
print(f'Current state: {states[-1]}')

# Regime age
current = states[-1]
age = 0
for s in reversed(states):
    if s != current:
        break
    age += 1
age = max(age - 1, 0)
print(f'Regime age (4h bars): {age} = {age * 4 / 24:.1f} days')

# Show transitions
print('\nRegime transitions:')
prev = None
for i, s in enumerate(states):
    if s != prev:
        idx = i + 199
        ts = klines[idx][0]
        dt = datetime.fromtimestamp(ts / 1000, timezone.utc)
        print(f'  {dt.strftime("%Y-%m-%d %H:%M")}: {prev} -> {s}')
        prev = s
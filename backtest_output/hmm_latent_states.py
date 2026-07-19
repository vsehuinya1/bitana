"""Six-state diagonal Gaussian HMM on BTC 4h features.

Train: BTCUSDT 4h history through 2026-06-30.
Test/edge mapping: forward-filtered states Jul 1-17 (no future-state lookahead).
Features: log_ret, vol6, vol6/vol42, range_pct, trend_vs_ema50.
"""
from __future__ import annotations

import bisect
import math
import sqlite3
import time
from datetime import datetime, timezone

import numpy as np
import requests


K = 6
DB = "storage/signal_shadow.db"
KEY_STRATEGIES = (
    "asia_pump_short_4h",
    "asia_pump_short_4h_tsl",
    "ny_flush_buy_4h_open",
    "ny_flush_buy_4h_open_tsl",
    "london_burst_fade",
    "fade_6h_late",
    "burst_follow",
    "setup_follow",
)


def logsumexp(a, axis=None):
    maximum = np.max(a, axis=axis, keepdims=True)
    out = maximum + np.log(np.sum(np.exp(a - maximum), axis=axis, keepdims=True))
    if axis is not None:
        out = np.squeeze(out, axis=axis)
    return out


def fetch_klines(total_bars=4600):
    out = []
    end_ms = int(time.time() * 1000)
    while len(out) < total_bars:
        limit = min(1500, total_bars - len(out) + 10)
        resp = requests.get(
            "https://fapi.binance.com/fapi/v1/klines",
            params={
                "symbol": "BTCUSDT",
                "interval": "4h",
                "limit": limit,
                "endTime": end_ms,
            },
            timeout=20,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        out = batch + out
        end_ms = batch[0][0] - 1
        time.sleep(0.25)
    dedup = {k[0]: k for k in out}
    return [
        {
            "open_ms": k[0],
            "close_ms": k[6],
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
        }
        for k in (dedup[key] for key in sorted(dedup))
    ]


def ema(values, span):
    alpha = 2 / (span + 1)
    out = [values[0]]
    for value in values[1:]:
        out.append(alpha * value + (1 - alpha) * out[-1])
    return np.asarray(out)


def rolling_std(values, window):
    out = np.zeros(len(values))
    for i in range(len(values)):
        start = max(0, i - window + 1)
        out[i] = np.std(values[start : i + 1])
    return out


def adx_series(highs, lows, closes, period=14):
    n = len(closes)
    pdm, ndm, trs = [0.0], [0.0], [0.0]
    for i in range(1, n):
        up, down = highs[i] - highs[i - 1], lows[i - 1] - lows[i]
        pdm.append(up if up > down and up > 0 else 0.0)
        ndm.append(down if down > up and down > 0 else 0.0)
        trs.append(
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        )

    def smooth(values):
        out = [0.0] * (period + 1)
        out[period] = sum(values[1 : period + 1])
        for i in range(period + 1, len(values)):
            out.append(out[-1] - out[-1] / period + values[i])
        return out

    sp, sn, st = smooth(pdm), smooth(ndm), smooth(trs)
    dx = [0.0] * n
    for i in range(period, n):
        if i >= len(st) or st[i] == 0:
            continue
        pdi = 100 * sp[i] / st[i] if i < len(sp) else 0
        ndi = 100 * sn[i] / st[i] if i < len(sn) else 0
        denom = pdi + ndi
        dx[i] = abs(pdi - ndi) / denom * 100 if denom else 0
    adx = [0.0] * n
    start = period * 2
    if start < n:
        adx[start] = sum(dx[period : start + 1]) / (period + 1)
        for i in range(start + 1, n):
            adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period
    return np.asarray(adx)


def kmeans(x, k, seed, iterations=40):
    rng = np.random.default_rng(seed)
    centers = x[rng.choice(len(x), size=k, replace=False)].copy()
    labels = np.zeros(len(x), dtype=int)
    for _ in range(iterations):
        distances = np.sum((x[:, None, :] - centers[None, :, :]) ** 2, axis=2)
        new_labels = np.argmin(distances, axis=1)
        if np.array_equal(labels, new_labels):
            break
        labels = new_labels
        for state in range(k):
            subset = x[labels == state]
            centers[state] = subset.mean(axis=0) if len(subset) else x[rng.integers(len(x))]
    return labels, centers


def emission_logprob(x, means, variances):
    # T x K, diagonal Gaussians.
    diff = x[:, None, :] - means[None, :, :]
    return -0.5 * (
        np.sum(np.log(2 * np.pi * variances), axis=1)[None, :]
        + np.sum(diff * diff / variances[None, :, :], axis=2)
    )


def forward_backward(x, pi, trans, means, variances):
    log_b = emission_logprob(x, means, variances)
    log_pi = np.log(np.maximum(pi, 1e-300))
    log_a = np.log(np.maximum(trans, 1e-300))
    t_len, k = log_b.shape
    alpha = np.empty((t_len, k))
    alpha[0] = log_pi + log_b[0]
    for t in range(1, t_len):
        alpha[t] = log_b[t] + logsumexp(alpha[t - 1][:, None] + log_a, axis=0)
    ll = float(logsumexp(alpha[-1], axis=0))
    beta = np.zeros((t_len, k))
    for t in range(t_len - 2, -1, -1):
        beta[t] = logsumexp(
            log_a + log_b[t + 1][None, :] + beta[t + 1][None, :], axis=1
        )
    gamma = np.exp(alpha + beta - ll)
    gamma /= gamma.sum(axis=1, keepdims=True)
    return ll, log_b, alpha, beta, gamma


def fit_hmm(x, seed, max_iter=60):
    labels, means = kmeans(x, K, seed)
    variances = np.empty((K, x.shape[1]))
    for state in range(K):
        subset = x[labels == state]
        variances[state] = np.var(subset, axis=0) + 0.1 if len(subset) > 1 else 1.0
    pi = np.ones(K) / K
    trans = np.ones((K, K)) * 0.1
    for a, b in zip(labels[:-1], labels[1:]):
        trans[a, b] += 1
    trans += np.eye(K) * 5
    trans /= trans.sum(axis=1, keepdims=True)
    prev_ll = -np.inf
    for iteration in range(max_iter):
        ll, log_b, alpha, beta, gamma = forward_backward(
            x, pi, trans, means, variances
        )
        xi_sum = np.zeros((K, K))
        log_a = np.log(np.maximum(trans, 1e-300))
        for t in range(len(x) - 1):
            log_xi = (
                alpha[t][:, None]
                + log_a
                + log_b[t + 1][None, :]
                + beta[t + 1][None, :]
                - ll
            )
            xi_sum += np.exp(log_xi)
        pi = gamma[0] + 1e-6
        pi /= pi.sum()
        trans = xi_sum + 1e-3
        trans /= trans.sum(axis=1, keepdims=True)
        weights = gamma.sum(axis=0) + 1e-12
        means = gamma.T @ x / weights[:, None]
        for state in range(K):
            diff = x - means[state]
            variances[state] = (
                gamma[:, state][:, None] * diff * diff
            ).sum(axis=0) / weights[state]
        variances = np.maximum(variances, 0.03)
        if ll - prev_ll < 1e-4:
            break
        prev_ll = ll
    return ll, pi, trans, means, variances, iteration + 1


def filtered_states(x, pi, trans, means, variances):
    log_b = emission_logprob(x, means, variances)
    probabilities = np.zeros((len(x), K))
    emission = np.exp(log_b[0] - np.max(log_b[0]))
    probabilities[0] = pi * emission
    probabilities[0] /= probabilities[0].sum()
    for t in range(1, len(x)):
        emission = np.exp(log_b[t] - np.max(log_b[t]))
        probabilities[t] = (probabilities[t - 1] @ trans) * emission
        probabilities[t] /= probabilities[t].sum()
    return np.argmax(probabilities, axis=1), probabilities


def cost_atr(row):
    atr_pct = float(row["entry_atr_pct"] or 0)
    return 0.12 / atr_pct if atr_pct else 0.0


print("Fetching BTC 4h history...")
klines = fetch_klines()
close = np.asarray([k["close"] for k in klines])
high = np.asarray([k["high"] for k in klines])
low = np.asarray([k["low"] for k in klines])
ret = np.zeros(len(close))
ret[1:] = np.diff(np.log(close))
vol6 = rolling_std(ret, 6)
vol42 = rolling_std(ret, 42)
vol_ratio = vol6 / np.maximum(vol42, 1e-8)
range_pct = (high - low) / close
ema50 = ema(close.tolist(), 50)
trend = close / ema50 - 1
raw_x = np.column_stack((ret, vol6, vol_ratio, range_pct, trend))
feature_names = ("log_ret", "vol6", "vol_ratio", "range_pct", "trend50")

valid_start = 100
times = np.asarray([k["close_ms"] for k in klines])
train_end = int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp() * 1000)
train_mask = (np.arange(len(klines)) >= valid_start) & (times < train_end)
train_raw = raw_x[train_mask]
mean_x, std_x = train_raw.mean(axis=0), train_raw.std(axis=0)
std_x = np.maximum(std_x, 1e-8)
x = (raw_x - mean_x) / std_x
x_train = x[train_mask]
print(
    f"train bars={len(x_train)} span="
    f"{datetime.fromtimestamp(times[train_mask][0]/1000, tz=timezone.utc):%Y-%m-%d} -> 2026-06-30"
)

best = None
for seed in (3, 11, 29):
    fitted = fit_hmm(x_train, seed)
    print(f"seed={seed} ll={fitted[0]:.1f} iterations={fitted[-1]}")
    if best is None or fitted[0] > best[0]:
        best = fitted
ll, pi, trans, means, variances, iterations = best
labels, posterior = filtered_states(x[valid_start:], pi, trans, means, variances)
labels_full = np.full(len(klines), -1)
confidence_full = np.zeros(len(klines))
labels_full[valid_start:] = labels
confidence_full[valid_start:] = posterior.max(axis=1)

# Reorder state IDs by model mean trend, then volatility ratio for stable presentation.
raw_means = means * std_x + mean_x
order = sorted(range(K), key=lambda s: (raw_means[s, 4], raw_means[s, 2]))
display_id = {model_state: i for i, model_state in enumerate(order)}

print("\nHMM STATE PROFILES (trained pre-Jul; ordered low->high trend)")
for model_state in order:
    display = display_id[model_state]
    profile = dict(zip(feature_names, raw_means[model_state]))
    occupancy = np.mean(labels == model_state)
    print(
        f"  H{display}: occ={100*occupancy:5.1f}% "
        f"ret={profile['log_ret']*100:+.3f}% vol6={profile['vol6']*100:.3f}% "
        f"vol_ratio={profile['vol_ratio']:.2f} range={profile['range_pct']*100:.2f}% "
        f"trend50={profile['trend50']*100:+.2f}% selfP={trans[model_state,model_state]:.3f}"
    )

# ADX regime for crosstab.
adx = adx_series(high, low, close)
ema200 = ema(close.tolist(), 200)
adx_regime = []
for i in range(len(close)):
    if i < 200 or adx[i] <= 25:
        state = "neutral"
    elif close[i] > ema200[i]:
        state = "bull"
    else:
        state = "bear"
    adx_regime.append(state)

test_mask = (times >= train_end) & (
    times < int(datetime(2026, 7, 18, tzinfo=timezone.utc).timestamp() * 1000)
)
test_indices = np.where(test_mask)[0]
print("\nHMM vs ADX CROSSTAB (Jul 1-17 completed 4h bars)")
print(f"{'HMM':8s}{'bear':>8s}{'neutral':>10s}{'bull':>8s}{'n':>7s}{'dominant':>12s}")
joint = np.zeros((K, 3))
regime_idx = {"bear": 0, "neutral": 1, "bull": 2}
for model_state in order:
    display = display_id[model_state]
    idx = [i for i in test_indices if labels_full[i] == model_state]
    counts = [sum(adx_regime[i] == regime for i in idx) for regime in ("bear", "neutral", "bull")]
    joint[display] = counts
    total = sum(counts)
    dominant = ("bear", "neutral", "bull")[int(np.argmax(counts))] if total else "-"
    print(f"H{display:<7d}{counts[0]:8d}{counts[1]:10d}{counts[2]:8d}{total:7d}{dominant:>12s}")

# NMI.
joint /= max(joint.sum(), 1)
px, py = joint.sum(axis=1), joint.sum(axis=0)
mi = 0.0
for i in range(K):
    for j in range(3):
        if joint[i, j] > 0:
            mi += joint[i, j] * math.log(joint[i, j] / (px[i] * py[j]))
hx = -sum(p * math.log(p) for p in px if p > 0)
hy = -sum(p * math.log(p) for p in py if p > 0)
nmi = mi / math.sqrt(hx * hy) if hx and hy else 0
purity = sum(np.max(joint[i]) for i in range(K))
print(f"Agreement: NMI={nmi:.3f}, HMM-state purity vs ADX={purity:.1%}")
print(
    f"Mean filtered-state confidence Jul1-17: "
    f"{np.mean(confidence_full[test_indices]):.1%}"
)

# Map shadow entries to last completed 4h HMM state.
close_times = [k["close_ms"] for k in klines]


def hmm_at(entry_time):
    ts = int(datetime.fromisoformat(entry_time).timestamp() * 1000)
    idx = bisect.bisect_right(close_times, ts) - 1
    if idx < valid_start:
        return None, None
    return display_id[int(labels_full[idx])], confidence_full[idx]


conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
trade_rows = [
    dict(row)
    for row in conn.execute(
        "SELECT * FROM shadow_trades WHERE entry_time>='2026-07-01' "
        "AND entry_time<'2026-07-18' AND status='closed'"
    )
]
for row in trade_rows:
    row["hmm_state"], row["hmm_conf"] = hmm_at(row["entry_time"])
    row["net_pnl"] = float(row["pnl_atr"]) - cost_atr(row)


def cap_sim(rows, cap=3):
    active, accepted = [], []
    for row in sorted(rows, key=lambda r: (r["entry_time"], r["id"])):
        active = [r for r in active if r["exit_time"] > row["entry_time"]]
        if len(active) >= cap or any(r["symbol"] == row["symbol"] for r in active):
            continue
        active.append(row)
        accepted.append(row)
    return accepted, sum(r["net_pnl"] for r in accepted)


print("\nSTRATEGY EDGE BY HMM STATE (net estimated costs; n>=3)")
for strategy in KEY_STRATEGIES:
    print(f"\n-- {strategy} --")
    for state in range(K):
        subset = [
            row
            for row in trade_rows
            if row["strategy"] == strategy and row["hmm_state"] == state
        ]
        if len(subset) < 3:
            continue
        values = [row["net_pnl"] for row in subset]
        print(
            f"  H{state}: n={len(values):3d} days={len(set(r['entry_time'][:10] for r in subset)):2d} "
            f"net={sum(values):+8.2f} avg={np.mean(values):+6.3f} "
            f"med={np.median(values):+6.3f} wr={100*sum(v>0 for v in values)/len(values):.0f}% "
            f"conf={np.mean([r['hmm_conf'] for r in subset]):.0%}"
        )

print("\nHMM FILTER CAP-3 SIMULATIONS (net estimated costs)")
filters = (
    ("asia all", "asia_pump_short_4h", tuple(range(K))),
    ("asia H5", "asia_pump_short_4h", (5,)),
    ("asia H2+H5", "asia_pump_short_4h", (2, 5)),
    ("NY all", "ny_flush_buy_4h_open", tuple(range(K))),
    ("NY H4+H5", "ny_flush_buy_4h_open", (4, 5)),
    ("late6 all", "fade_6h_late", tuple(range(K))),
    ("late6 H4+H5", "fade_6h_late", (4, 5)),
)
for name, strategy, allowed_states in filters:
    subset = [
        row
        for row in trade_rows
        if row["strategy"] == strategy and row["hmm_state"] in allowed_states
    ]
    accepted, net = cap_sim(subset)
    print(
        f"  {name:16s} rows={len(subset):3d} accepted={len(accepted):3d} "
        f"net={net:+8.2f} avg={net/len(accepted) if accepted else 0:+.3f}"
    )

print("\nHMM TRANSITION MATRIX (display-state order)")
display_trans = np.zeros((K, K))
for model_i in range(K):
    for model_j in range(K):
        display_trans[display_id[model_i], display_id[model_j]] = trans[model_i, model_j]
print(f"{'':6s}" + "".join(f"H{i:>7d}" for i in range(K)))
for i in range(K):
    print(f"H{i:<5d}" + "".join(f"{display_trans[i,j]:8.3f}" for j in range(K)))

current_idx = np.where(times <= int(time.time() * 1000))[0][-1]
print(
    f"\nCURRENT HMM: H{display_id[int(labels_full[current_idx])]} "
    f"confidence={confidence_full[current_idx]:.1%}; "
    f"ADX={adx_regime[current_idx]}"
)
conn.close()

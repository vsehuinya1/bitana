import bisect
import time
from datetime import datetime, timezone
import numpy as np
import requests

# Replicate the script's logic exactly
resp = requests.get('https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=4h&limit=1000', timeout=10)
klines = resp.json()
times = [int(k[0]) for k in klines]
closes = [float(k[4]) for k in klines]
highs = [float(k[2]) for k in klines]
lows = [float(k[3]) for k in klines]

close = np.asarray(closes)
high = np.asarray(highs)
low = np.asarray(lows)
ret = np.zeros(len(close))
ret[1:] = np.diff(np.log(close))

def rolling_std(values, window):
    out = np.zeros(len(values))
    for i in range(len(values)):
        start = max(0, i - window + 1)
        out[i] = np.std(values[start:i+1])
    return out

def ema(values, span):
    alpha = 2 / (span + 1)
    out = [values[0]]
    for value in values[1:]:
        out.append(alpha * value + (1 - alpha) * out[-1])
    return np.asarray(out)

vol6 = rolling_std(ret, 6)
vol42 = rolling_std(ret, 42)
vol_ratio = vol6 / np.maximum(vol42, 1e-8)
range_pct = (high - low) / close
ema50 = ema(close.tolist(), 50)
trend = close / ema50 - 1
raw_x = np.column_stack((ret, vol6, vol_ratio, range_pct, trend))

valid_start = 100
train_end = int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp() * 1000)
train_mask = (np.arange(len(klines)) >= valid_start) & (np.array(times) < train_end)
train_raw = raw_x[train_mask]
mean_x, std_x = train_raw.mean(axis=0), train_raw.std(axis=0)
std_x = np.maximum(std_x, 1e-8)
x = (raw_x - mean_x) / std_x
x_train = x[train_mask]

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
    diff = x[:, None, :] - means[None, :, :]
    return -0.5 * (np.sum(np.log(2 * np.pi * variances), axis=1)[None, :] + np.sum(diff * diff / variances[None, :, :], axis=2))

def logsumexp(a, axis=None):
    maximum = np.max(a, axis=axis, keepdims=True)
    out = maximum + np.log(np.sum(np.exp(a - maximum), axis=axis, keepdims=True))
    if axis is not None:
        out = np.squeeze(out, axis=axis)
    return out

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
        beta[t] = logsumexp(log_a + log_b[t + 1][None, :] + beta[t + 1][None, :], axis=1)
    gamma = np.exp(alpha + beta - ll)
    gamma /= gamma.sum(axis=1, keepdims=True)
    return ll, log_b, alpha, beta, gamma

def fit_hmm(x, seed, max_iter=60):
    labels, means = kmeans(x, 6, seed)
    variances = np.empty((6, x.shape[1]))
    for state in range(6):
        subset = x[labels == state]
        variances[state] = np.var(subset, axis=0) + 0.1 if len(subset) > 1 else 1.0
    pi = np.ones(6) / 6
    trans = np.ones((6, 6)) * 0.1
    for a, b in zip(labels[:-1], labels[1:]):
        trans[a, b] += 1
    trans += np.eye(6) * 5
    trans /= trans.sum(axis=1, keepdims=True)
    prev_ll = -np.inf
    for iteration in range(max_iter):
        ll, log_b, alpha, beta, gamma = forward_backward(x, pi, trans, means, variances)
        xi_sum = np.zeros((6, 6))
        log_a = np.log(np.maximum(trans, 1e-300))
        for t in range(len(x) - 1):
            log_xi = (alpha[t][:, None] + log_a + log_b[t + 1][None, :] + beta[t + 1][None, :] - ll)
            xi_sum += np.exp(log_xi)
        pi = gamma[0] + 1e-6
        pi /= pi.sum()
        trans = xi_sum + 1e-3
        trans /= trans.sum(axis=1, keepdims=True)
        weights = gamma.sum(axis=0) + 1e-12
        means = gamma.T @ x / weights[:, None]
        for state in range(6):
            diff = x - means[state]
            variances[state] = (gamma[:, state][:, None] * diff * diff).sum(axis=0) / weights[state]
        variances = np.maximum(variances, 0.03)
        if ll - prev_ll < 1e-4:
            break
        prev_ll = ll
    return ll, pi, trans, means, variances, iteration + 1

best = None
for seed in (3, 11, 29):
    fitted = fit_hmm(x_train, seed)
    if best is None or fitted[0] > best[0]:
        best = fitted
ll, pi, trans, means, variances, iterations = best

# Use the script's filtered_states logic
log_b = emission_logprob(x[valid_start:], means, variances)
probabilities = np.zeros((len(x[valid_start:]), 6))
emission = np.exp(log_b[0] - np.max(log_b[0]))
probabilities[0] = pi * emission
probabilities[0] /= probabilities[0].sum()
for t in range(1, len(x[valid_start:])):
    emission = np.exp(log_b[t] - np.max(log_b[t]))
    probabilities[t] = (probabilities[t - 1] @ trans) * emission
    probabilities[t] /= probabilities[t].sum()
labels = np.argmax(probabilities, axis=1)

labels_full = np.full(len(klines), -1)
confidence_full = np.zeros(len(klines))
labels_full[valid_start:] = labels
confidence_full[valid_start:] = probabilities.max(axis=1)

raw_means = means * std_x + mean_x
order = sorted(range(6), key=lambda s: (raw_means[s, 4], raw_means[s, 2]))
display_id = {model_state: i for i, model_state in enumerate(order)}

print('Display mapping:', display_id)
print('Raw trend50:', [round(raw_means[s, 4], 4) for s in range(6)])

print('\nRecent bars:')
for i in range(len(klines)-15, len(klines)):
    model_state = labels_full[i]
    if model_state >= 0:
        dt = datetime.fromtimestamp(times[i]/1000, tz=timezone.utc)
        print(f'  {dt.strftime("%m-%d %H:%M")}: model_state={model_state} -> H{display_id[model_state]} conf={confidence_full[i]:.1%}')

current_idx = np.where(np.array(times) <= int(time.time() * 1000))[0][-1]
model_state = labels_full[current_idx]
print(f'\ncurrent_idx={current_idx}, model_state={model_state}, display=H{display_id[model_state]}, conf={confidence_full[current_idx]:.1%}')
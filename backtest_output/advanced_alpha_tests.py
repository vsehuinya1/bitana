"""Advanced alpha tests for Monday go-live.

Tests:
1. Alternate hard stops and 1h winner scaling
2. Semi-Markov edge by BTC regime age
3. Session carryover
4. MDP-style gate optimization (literal + variance-penalized)

BTC regimes are reconstructed from completed 4h bars to avoid look-ahead.
"""
from __future__ import annotations

import bisect
import math
import sqlite3
import statistics
import time
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np
import requests


DB = "storage/signal_shadow.db"
START = "2026-07-01"
END = "2026-07-18"
STATES = ("bear", "neutral", "bull")
AGE_BINS = ("<16h", "16-32h", "32-64h", ">64h")
KEY_STRATEGIES = (
    "asia_pump_short_4h",
    "asia_pump_short_4h_tsl",
    "ny_flush_buy_4h_open",
    "ny_flush_buy_4h_open_tsl",
    "london_burst_fade",
    "fade_6h_late",
)
LIVE_STRATEGIES = ("asia_pump_short_4h", "ny_flush_buy_4h_open")


def fetch_klines(total_bars: int = 4600) -> list[dict]:
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
    dedup = {}
    for k in out:
        dedup[k[0]] = {
            "open_ms": k[0],
            "close_ms": k[6],
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
        }
    return [dedup[k] for k in sorted(dedup)]


def ema_series(values: list[float], span: int) -> list[float]:
    alpha = 2.0 / (span + 1)
    out = [values[0]]
    for value in values[1:]:
        out.append(alpha * value + (1 - alpha) * out[-1])
    return out


def adx_series(highs, lows, closes, period=14):
    n = len(closes)
    pdm, ndm, trs = [0.0], [0.0], [0.0]
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        pdm.append(up if up > down and up > 0 else 0.0)
        ndm.append(down if down > up and down > 0 else 0.0)
        trs.append(
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        )

    def smooth(values, p):
        out = [0.0] * (p + 1)
        out[p] = sum(values[1 : p + 1])
        for i in range(p + 1, len(values)):
            out.append(out[-1] - out[-1] / p + values[i])
        return out

    sp, sn, st = smooth(pdm, period), smooth(ndm, period), smooth(trs, period)
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
    return adx


def age_bin(age_bars: int) -> str:
    if age_bars < 4:
        return "<16h"
    if age_bars < 8:
        return "16-32h"
    if age_bars < 16:
        return "32-64h"
    return ">64h"


def trade_cost_atr(row) -> float:
    atr_pct = float(row["entry_atr_pct"] or 0)
    return 0.12 / atr_pct if atr_pct > 0 else 0.0


def metric(rows, value_fn=lambda row: float(row["pnl_atr"])) -> dict | None:
    values = [value_fn(row) for row in rows]
    if not values:
        return None
    return {
        "n": len(values),
        "total": sum(values),
        "avg": statistics.mean(values),
        "med": statistics.median(values),
        "wr": sum(v > 0 for v in values) / len(values),
        "sd": statistics.pstdev(values) if len(values) > 1 else 0.0,
        "days": len({row["entry_time"][:10] for row in rows}),
    }


def ci_mean(values, seed=7, n_boot=3000):
    if len(values) < 2:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    arr = np.asarray(values)
    means = np.mean(rng.choice(arr, size=(n_boot, len(arr)), replace=True), axis=1)
    return tuple(np.quantile(means, [0.025, 0.975]))


print("Loading BTC 4h history and reconstructing completed-bar regimes...")
klines = fetch_klines()
highs = [k["high"] for k in klines]
lows = [k["low"] for k in klines]
closes = [k["close"] for k in klines]
ema200 = ema_series(closes, 200)
adx14 = adx_series(highs, lows, closes)
regimes, ages = [], []
current, age = None, 0
for i in range(len(klines)):
    if i < 200:
        regime = None
    elif adx14[i] <= 25:
        regime = "neutral"
    elif closes[i] > ema200[i]:
        regime = "bull"
    else:
        regime = "bear"
    if regime is None:
        age = 0
    elif regime == current:
        age += 1
    else:
        current, age = regime, 0
    regimes.append(regime)
    ages.append(age)
close_ms = [k["close_ms"] for k in klines]


def state_at(entry_time: str) -> tuple[str | None, int | None]:
    ts = int(datetime.fromisoformat(entry_time).timestamp() * 1000)
    idx = bisect.bisect_right(close_ms, ts) - 1
    if idx < 200:
        return None, None
    return regimes[idx], ages[idx]


conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
rows = [
    dict(row)
    for row in conn.execute(
        "SELECT * FROM shadow_trades WHERE entry_time>=? AND entry_time<? "
        "AND status='closed'",
        (START, END),
    )
]
for row in rows:
    regime, age = state_at(row["entry_time"])
    row["recon_regime"] = regime
    row["recon_age"] = age
    row["age_bin"] = age_bin(age) if age is not None else None
    row["net_pnl"] = float(row["pnl_atr"]) - trade_cost_atr(row)
print(f"trades: {len(rows)}; span {rows[0]['entry_time']} -> {rows[-1]['entry_time']}")

# ---------------------------------------------------------------------------
# 1. Hard-stop counterfactual
# ---------------------------------------------------------------------------
print("\n" + "=" * 80)
print("HARD STOP COUNTERFACTUAL")
print("If run_mae_atr <= -stop, assume exit at -stop; otherwise keep actual final PnL.")
for strategy in LIVE_STRATEGIES:
    strategy_rows = [r for r in rows if r["strategy"] == strategy]
    print(f"\n-- {strategy} (n={len(strategy_rows)}) --")
    for regime in STATES:
        subset = [r for r in strategy_rows if r["recon_regime"] == regime]
        if not subset:
            continue
        print(f"  {regime} n={len(subset)}")
        for stop in (2, 3, 4, 5, 6, 8, 10):
            values = [
                (-float(stop) if float(r["run_mae_atr"] or 0) <= -stop else float(r["pnl_atr"]))
                - trade_cost_atr(r)
                for r in subset
            ]
            r_values = [value / stop for value in values]
            lo, hi = ci_mean(values)
            stops = sum(float(r["run_mae_atr"] or 0) <= -stop for r in subset)
            print(
                f"    SL={stop:2d}ATR total={sum(values):+8.2f} avg={np.mean(values):+6.3f} "
                f"med={np.median(values):+6.3f} hits={stops:3d} "
                f"totalR={sum(r_values):+7.2f} avgR={np.mean(r_values):+.3f} "
                f"CI95_ATR=[{lo:+.2f},{hi:+.2f}]"
            )

# MAE distribution among winners tells how much room winners need.
print("\nWINNER MAE DISTRIBUTION (plain live books, actual winners)")
for strategy in LIVE_STRATEGIES:
    winners = [r for r in rows if r["strategy"] == strategy and float(r["pnl_atr"]) > 0]
    maes = sorted(abs(min(0.0, float(r["run_mae_atr"] or 0))) for r in winners)
    if maes:
        print(
            f"  {strategy:28s} n={len(maes):3d} "
            f"p50={np.quantile(maes,.5):.2f} p75={np.quantile(maes,.75):.2f} "
            f"p90={np.quantile(maes,.9):.2f} p95={np.quantile(maes,.95):.2f} ATR"
        )

# ---------------------------------------------------------------------------
# 1b. Scaling at one hour
# ---------------------------------------------------------------------------
print("\n" + "=" * 80)
print("SCALE-IN AT 1H (checkpoint sample only)")
print("Incremental scale PnL = final_pnl - pnl_1h - extra round-trip cost.")
for strategy in LIVE_STRATEGIES:
    sample = [
        r
        for r in rows
        if r["strategy"] == strategy and r["pnl_1h"] is not None
    ]
    if not sample:
        continue
    print(f"\n-- {strategy}: checkpoint n={len(sample)} --")
    base = sum(r["net_pnl"] for r in sample)
    print(f"  base net total={base:+.2f}")
    for threshold in (0.0, 0.5, 1.0, 2.0):
        selected = [r for r in sample if float(r["pnl_1h"]) >= threshold]
        if not selected:
            continue
        forward = [float(r["pnl_atr"]) - float(r["pnl_1h"]) for r in selected]
        lo, hi = ci_mean(forward)
        print(
            f"  pnl_1h>={threshold:.1f}: n={len(selected):2d} "
            f"forward_delta avg={np.mean(forward):+.3f} med={np.median(forward):+.3f} "
            f"positive={sum(v>0 for v in forward)}/{len(forward)} CI95=[{lo:+.2f},{hi:+.2f}]"
        )
        partial_half = sum(
            0.5 * (float(r["pnl_1h"]) - float(r["pnl_atr"])) for r in selected
        )
        print(
            f"      alternative: take 50% off at 1h -> change vs base={partial_half:+.2f}, "
            f"portfolio total={base+partial_half:+.2f}"
        )
        for add_fraction in (0.25, 0.50, 1.00):
            incremental = sum(
                add_fraction
                * (
                    float(r["pnl_atr"])
                    - float(r["pnl_1h"])
                    - trade_cost_atr(r)
                )
                for r in selected
            )
            print(
                f"      add {add_fraction:.2f}x -> incremental={incremental:+.2f}, "
                f"portfolio total={base+incremental:+.2f}"
            )

# ---------------------------------------------------------------------------
# 2. Semi-Markov: strategy edge by regime age
# ---------------------------------------------------------------------------
print("\n" + "=" * 80)
print("SEMI-MARKOV EDGE BY REGIME AGE")
for strategy in KEY_STRATEGIES:
    print(f"\n-- {strategy} --")
    for regime in STATES:
        for age_name in AGE_BINS:
            subset = [
                r
                for r in rows
                if r["strategy"] == strategy
                and r["recon_regime"] == regime
                and r["age_bin"] == age_name
            ]
            m = metric(subset, lambda r: r["net_pnl"])
            if m and m["n"] >= 2:
                lo, hi = ci_mean([r["net_pnl"] for r in subset])
                print(
                    f"  {regime:8s} {age_name:7s} n={m['n']:3d} days={m['days']:2d} "
                    f"net={m['total']:+8.2f} avg={m['avg']:+.3f} med={m['med']:+.3f} "
                    f"CI95=[{lo:+.2f},{hi:+.2f}]"
                )

# ---------------------------------------------------------------------------
# 3. Session carryover
# ---------------------------------------------------------------------------
print("\n" + "=" * 80)
print("SESSION CARRYOVER (weekday UTC days; canonical books; net of estimated costs)")
CANON = {
    "asia": "asia_pump_short_4h",
    "london": "london_burst_fade",
    "ny": "ny_flush_buy_4h_open",
}
daily = defaultdict(lambda: defaultdict(list))
for row in rows:
    day = datetime.fromisoformat(row["entry_time"]).date()
    if day.weekday() >= 5:
        continue
    for session, strategy in CANON.items():
        if row["strategy"] == strategy:
            daily[str(day)][session].append(row)


def session_value(session_rows):
    # Cap at three concurrent within the canonical session book.
    active, accepted = [], []
    for row in sorted(session_rows, key=lambda r: (r["entry_time"], r["id"])):
        active = [r for r in active if r["exit_time"] > row["entry_time"]]
        if len(active) >= 3 or any(r["symbol"] == row["symbol"] for r in active):
            continue
        active.append(row)
        accepted.append(row)
    return sum(r["net_pnl"] for r in accepted)


daily_values = {}
for day, sessions in daily.items():
    daily_values[day] = {
        session: session_value(session_rows)
        for session, session_rows in sessions.items()
        if session_rows
    }


def permutation_pvalue(x, y, observed, n=10000, seed=11):
    rng = np.random.default_rng(seed)
    y = np.asarray(y)
    count = 0
    for _ in range(n):
        yp = rng.permutation(y)
        corr = np.corrcoef(x, yp)[0, 1]
        if abs(corr) >= abs(observed):
            count += 1
    return (count + 1) / (n + 1)


for source, target in (("asia", "london"), ("london", "ny"), ("asia", "ny")):
    pairs = [
        (values[source], values[target], day)
        for day, values in sorted(daily_values.items())
        if source in values and target in values
    ]
    if len(pairs) < 3:
        print(f"  {source}->{target}: insufficient (n={len(pairs)})")
        continue
    x = np.array([p[0] for p in pairs])
    y = np.array([p[1] for p in pairs])
    corr = float(np.corrcoef(x, y)[0, 1])
    pval = permutation_pvalue(x, y, corr)
    y_pos = y[x > 0]
    y_neg = y[x <= 0]
    print(
        f"  {source:6s}->{target:6s} n={len(pairs):2d} corr={corr:+.3f} perm_p={pval:.3f} | "
        f"target after source>0: n={len(y_pos)} avg={np.mean(y_pos) if len(y_pos) else float('nan'):+.2f} | "
        f"after source<=0: n={len(y_neg)} avg={np.mean(y_neg) if len(y_neg) else float('nan'):+.2f}"
    )
    print(
        "    "
        + " | ".join(
            f"{day[5:]} {source}={sv:+.1f}->{target}={tv:+.1f}"
            for sv, tv, day in pairs
        )
    )

# ---------------------------------------------------------------------------
# 4. MDP / contextual gate
# ---------------------------------------------------------------------------
print("\n" + "=" * 80)
print("MDP GATE OPTIMIZATION")
print("State=(regime, age_bin), actions={block, reduce 0.5x, allow 1x}, gamma=.95.")
print("Actions do not affect regime transitions; therefore future value cancels from action ranking.")

state_list = [(regime, age_name) for regime in STATES for age_name in AGE_BINS]
state_idx = {state: i for i, state in enumerate(state_list)}

# Transition matrix from reconstructed BTC 4h state/age buckets.
transition_counts = np.ones((len(state_list), len(state_list))) * 0.1
bar_states = []
for regime, age in zip(regimes[200:], ages[200:]):
    bar_states.append((regime, age_bin(age)))
for a, b in zip(bar_states[:-1], bar_states[1:]):
    transition_counts[state_idx[a], state_idx[b]] += 1
transition = transition_counts / transition_counts.sum(axis=1, keepdims=True)

# Live-policy reward sample: Asia plain every day; NY plain, excluding Monday.
policy_rows = []
for row in rows:
    if row["strategy"] == "asia_pump_short_4h":
        policy_rows.append(row)
    elif row["strategy"] == "ny_flush_buy_4h_open":
        if datetime.fromisoformat(row["entry_time"]).weekday() != 0:
            policy_rows.append(row)

rewards = defaultdict(list)
for row in policy_rows:
    state = (row["recon_regime"], row["age_bin"])
    if state in state_idx:
        rewards[state].append(row["net_pnl"])


def solve_mdp(lambda_var: float):
    actions = {"block": 0.0, "reduce": 0.5, "allow": 1.0}
    reward_matrix = np.zeros((len(state_list), len(actions)))
    action_names = list(actions)
    estimates = {}
    for i, state in enumerate(state_list):
        vals = rewards[state]
        n = len(vals)
        raw_mu = float(np.mean(vals)) if vals else 0.0
        raw_var = float(np.var(vals)) if len(vals) > 1 else 0.0
        # Shrink thin state means/variances toward zero / pooled variance.
        weight = n / (n + 20)
        pooled_var = float(np.var([r["net_pnl"] for r in policy_rows]))
        mu = weight * raw_mu
        var = weight * raw_var + (1 - weight) * pooled_var
        estimates[state] = (n, raw_mu, mu, var)
        for j, name in enumerate(action_names):
            fraction = actions[name]
            reward_matrix[i, j] = fraction * mu - lambda_var * fraction**2 * var

    value = np.zeros(len(state_list))
    gamma = 0.95
    for _ in range(1000):
        future = gamma * transition @ value
        q = reward_matrix + future[:, None]
        new_value = q.max(axis=1)
        if np.max(np.abs(new_value - value)) < 1e-10:
            break
        value = new_value
    q = reward_matrix + (gamma * transition @ value)[:, None]
    choice = np.argmax(q, axis=1)
    return action_names, choice, estimates


for lambda_var in (0.0, 0.05, 0.10):
    names, choices, estimates = solve_mdp(lambda_var)
    print(f"\n  lambda_variance={lambda_var:.2f}")
    for i, state in enumerate(state_list):
        n, raw_mu, shrunk_mu, var = estimates[state]
        if n:
            print(
                f"    {state[0]:8s} {state[1]:7s} n={n:3d} raw_net={raw_mu:+.3f} "
                f"shrunk={shrunk_mu:+.3f} sd={math.sqrt(var):.2f} -> {names[choices[i]].upper()}"
            )

conn.close()

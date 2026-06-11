"""
Full Universe Liq-Cluster Sweep.

Phase 1: Discover qualifying USDT-M perps from Binance
Phase 2: Collect 12mo 5m/1h OHLCV + daily liq for each
Phase 3: Run frozen V3 walk-forward on each
Phase 4: Score and rank with composite model

Usage (on VPS):
    cd /root/bitana
    python3 -u research/scripts/full_universe_sweep.py 2>&1 | tee /tmp/universe_sweep.log
"""
import sys
import time
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import requests
import pandas as pd
import numpy as np
from loguru import logger
logger.remove()
logger.add(sys.stderr, level="WARNING")

from research.config.settings import OHLCV_DIR, LIQUIDATION_DIR, COINALYZE_SYMBOL_MAP
from research.data.storage.parquet_store import load_parquet
from research.data.collectors.binance_ohlcv import collect_ohlcv
from research.data.collectors.coinalyze_liq import collect_liquidations
from research.engine.backtest import BacktestEngine
from research.engine.costs import CostModel
from research.engine.signals_liq_v2 import (
    LiqClusterExpansionSignal, LiqClusterConfig, classify_cascade_context,
)
from research.config.secrets import get_coinalyze_key

# ═══════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════

CFG = LiqClusterConfig(
    min_confirmations=4,
    require_short_squeeze=True,
    vol_z_threshold=3.0,
    initial_stop_atr=2.5,
    ret5d_min=-5.0,
    partial_r=2.5,
)

WINDOWS = [
    ("May-Jun 25", "2025-05-01", "2025-07-01", "OOS"),
    ("Jul-Aug 25", "2025-07-01", "2025-09-01", "OOS"),
    ("Sep-Oct 25", "2025-09-01", "2025-11-01", "OOS"),
    ("Nov-Dec 25", "2025-11-01", "2026-01-01", "IS"),
    ("Jan-Feb 26", "2026-01-01", "2026-03-01", "IS"),
    ("Mar-Apr 26", "2026-03-01", "2026-05-01", "IS"),
    ("May 26",     "2026-05-01", "2026-06-01", "IS"),
]

MIN_VOLUME_USD = 50_000_000
MIN_LISTING_MONTHS = 18

# Coinalyze symbol format: {SYMBOL}_PERP.A for Binance
def coinalyze_sym(binance_sym):
    """Generate Coinalyze symbol from Binance symbol."""
    if binance_sym in COINALYZE_SYMBOL_MAP:
        return COINALYZE_SYMBOL_MAP[binance_sym]
    return f"{binance_sym}_PERP.A"


# ═══════════════════════════════════════════════════
# Phase 1: Symbol Discovery
# ═══════════════════════════════════════════════════

def discover_symbols():
    """Find all qualifying USDT-M perps."""
    print("PHASE 1: SYMBOL DISCOVERY", flush=True)
    print("-" * 50, flush=True)

    # Get exchange info
    info = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=30).json()
    usdt_perps = [
        s for s in info["symbols"]
        if s["quoteAsset"] == "USDT"
        and s["contractType"] == "PERPETUAL"
        and s["status"] == "TRADING"
    ]
    print(f"  Total USDT-M perps: {len(usdt_perps)}", flush=True)

    # Filter by listing date
    cutoff_ms = int(time.time() * 1000) - (MIN_LISTING_MONTHS * 30 * 86400000)
    old_enough = [s for s in usdt_perps if s.get("onboardDate", 0) < cutoff_ms]
    print(f"  Listed {MIN_LISTING_MONTHS}mo+: {len(old_enough)}", flush=True)

    # Get 24h volumes
    tickers = requests.get("https://fapi.binance.com/fapi/v1/ticker/24hr", timeout=30).json()
    vol_map = {t["symbol"]: float(t["quoteVolume"]) for t in tickers}

    # Filter by volume
    qualifying = []
    for s in old_enough:
        sym = s["symbol"]
        vol = vol_map.get(sym, 0)
        if vol >= MIN_VOLUME_USD:
            qualifying.append({
                "symbol": sym,
                "volume_m": vol / 1e6,
            })

    qualifying.sort(key=lambda x: x["volume_m"], reverse=True)
    print(f"  Volume >= ${MIN_VOLUME_USD/1e6:.0f}M: {len(qualifying)}", flush=True)

    # Check Coinalyze liq data availability via API probe
    api_key = get_coinalyze_key()
    available = []
    skipped = []

    print(f"  Checking Coinalyze liq data...", flush=True)
    for i, q in enumerate(qualifying):
        sym = q["symbol"]
        ca_sym = coinalyze_sym(sym)

        # Quick probe: fetch last 7 days of liq data
        try:
            now = int(time.time())
            resp = requests.get(
                "https://api.coinalyze.net/v1/liquidation-history",
                params={
                    "symbols": ca_sym,
                    "interval": "daily",
                    "from": now - 7 * 86400,
                    "to": now,
                    "api_key": api_key,
                },
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list) and len(data) > 0:
                    history = data[0].get("history", [])
                    if len(history) >= 3:
                        q["liq_available"] = True
                        available.append(q)
                    else:
                        q["liq_available"] = False
                        skipped.append((sym, "no liq history"))
                else:
                    skipped.append((sym, "empty response"))
            elif resp.status_code == 429:
                print(f"    Rate limited, sleeping 60s...", flush=True)
                time.sleep(60)
                available.append(q)  # assume available, will retry later
            else:
                skipped.append((sym, f"HTTP {resp.status_code}"))
        except Exception as e:
            skipped.append((sym, str(e)[:50]))

        # Rate limit: ~35 req/min
        if (i + 1) % 30 == 0:
            print(f"    Checked {i+1}/{len(qualifying)}...", flush=True)
            time.sleep(5)
        else:
            time.sleep(1.5)

    print(f"  With Coinalyze liq data: {len(available)}", flush=True)
    if skipped:
        print(f"  Skipped ({len(skipped)}):", flush=True)
        for sym, reason in skipped[:10]:
            print(f"    {sym}: {reason}", flush=True)
        if len(skipped) > 10:
            print(f"    ... and {len(skipped)-10} more", flush=True)

    return available


# ═══════════════════════════════════════════════════
# Phase 2: Data Collection
# ═══════════════════════════════════════════════════

def collect_symbol_data(symbol):
    """Collect 12mo 5m, 1h OHLCV + daily liq."""
    start_ms = int(time.time() * 1000) - (365 * 86400000)

    # 5m
    f5m = OHLCV_DIR / f"{symbol}_5m.parquet"
    if not f5m.exists():
        try:
            collect_ohlcv(symbol, "5m", start_ms=start_ms)
        except Exception as e:
            return False, f"5m: {e}"

    # 1h
    f1h = OHLCV_DIR / f"{symbol}_1h.parquet"
    if not f1h.exists():
        try:
            collect_ohlcv(symbol, "1h", start_ms=start_ms)
        except Exception as e:
            return False, f"1h: {e}"

    # Liq
    fliq = LIQUIDATION_DIR / f"{symbol}_liq_daily.parquet"
    if not fliq.exists():
        try:
            collect_liquidations(symbol, "daily")
        except Exception as e:
            return False, f"liq: {e}"

    return True, "OK"


# ═══════════════════════════════════════════════════
# Phase 3: Walk-Forward
# ═══════════════════════════════════════════════════

def run_symbol(symbol):
    """Run walk-forward and return stats dict."""
    # Load 5m
    df_5m = load_parquet(OHLCV_DIR / f"{symbol}_5m.parquet")
    if df_5m.empty or len(df_5m) < 10000:
        return None

    df_5m["dt"] = pd.to_datetime(df_5m["timestamp"], unit="ms")

    # Build daily from 1h
    ohlcv_1h = load_parquet(OHLCV_DIR / f"{symbol}_1h.parquet")
    if ohlcv_1h.empty:
        return None
    ohlcv_1h["dt"] = pd.to_datetime(ohlcv_1h["timestamp"], unit="ms")
    daily = ohlcv_1h.set_index("dt").resample("1D").agg({
        "timestamp": "first", "open": "first", "high": "max",
        "low": "min", "close": "last", "volume": "sum",
    }).dropna().reset_index()

    # Load liq
    liq_file = LIQUIDATION_DIR / f"{symbol}_liq_daily.parquet"
    if not liq_file.exists():
        return None
    liq = load_parquet(liq_file)
    if liq.empty:
        return None
    liq["dt"] = pd.to_datetime(liq["timestamp"], unit="ms")
    daily = pd.merge_asof(
        daily.sort_values("dt"),
        liq[["dt", "long_liquidations", "short_liquidations"]].sort_values("dt"),
        on="dt", direction="nearest",
    )
    daily["total_liq"] = daily["long_liquidations"].fillna(0) + daily["short_liquidations"].fillna(0)

    # Classify + prepare
    daily_c = classify_cascade_context(daily.copy(), CFG)
    sig_prep = LiqClusterExpansionSignal(CFG)
    df_5m_p = sig_prep.prepare(df_5m.copy(), daily_c)

    # Walk-forward
    oos_r = []
    is_r = []

    for _, start, end, sample in WINDOWS:
        s = pd.Timestamp(start)
        e = pd.Timestamp(end)
        mask = (df_5m_p["dt"] >= s) & (df_5m_p["dt"] < e)
        wdf = df_5m_p[mask].copy()
        if len(wdf) < 100:
            continue

        signal = LiqClusterExpansionSignal(CFG)
        engine = BacktestEngine(cost_model=CostModel(), initial_capital=10000)
        engine.run(wdf, signal.evaluate, context={"capital": 10000})
        t = engine.get_trades()
        if t.empty:
            continue

        sr = (t["risk_per_unit"] * t["size"]).replace(0, np.nan)
        t["r"] = t["pnl_net"] / sr
        r = t["r"].dropna()

        if sample == "OOS":
            oos_r.extend(r.tolist())
        else:
            is_r.extend(r.tolist())

    all_r = np.array(oos_r + is_r)
    if len(all_r) < 5:
        return None

    oos_arr = np.array(oos_r) if oos_r else np.array([])
    is_arr = np.array(is_r) if is_r else np.array([])

    ws = all_r[all_r > 0].sum()
    ls = abs(all_r[all_r <= 0].sum())
    pf = ws / ls if ls > 0 else 99

    wr = (all_r > 0).mean() * 100
    skew = float(pd.Series(all_r).skew()) if len(all_r) > 2 else 0

    cum_r = np.cumsum(all_r)
    peak = np.maximum.accumulate(cum_r)
    max_dd = float((cum_r - peak).min())

    if len(oos_arr) > 0:
        oos_ws = oos_arr[oos_arr > 0].sum()
        oos_ls = abs(oos_arr[oos_arr <= 0].sum())
        oos_pf = oos_ws / oos_ls if oos_ls > 0 else 99
    else:
        oos_pf = 0

    return {
        "symbol": symbol,
        "n": len(all_r),
        "wr": wr,
        "pf": pf,
        "total_r": float(all_r.sum()),
        "oos_pf": oos_pf,
        "oos_n": len(oos_arr),
        "max_dd": max_dd,
        "skew": skew,
        "avg_win": float(all_r[all_r > 0].mean()) if (all_r > 0).any() else 0,
        "avg_loss": float(all_r[all_r <= 0].mean()) if (all_r <= 0).any() else 0,
    }


# ═══════════════════════════════════════════════════
# Phase 4: Scoring
# ═══════════════════════════════════════════════════

def normalize(x, floor, cap):
    """Clip x to [floor, cap] then scale to [0, 1]."""
    x = max(floor, min(cap, x))
    return (x - floor) / (cap - floor) if cap > floor else 0


def compute_score(r):
    """Compute composite score for a result dict."""
    # Hard cutoffs
    if r["oos_pf"] < 0.7 and r["oos_n"] > 5:
        return -1  # disqualified
    if r["n"] < 15:
        return -1
    if r["pf"] < 0.9:
        return -1
    if r["max_dd"] < -25:
        return -1

    winning_trades = r["n"] * r["wr"] / 100
    risk_adj = r["total_r"] / abs(r["max_dd"]) if r["max_dd"] != 0 else 0

    score = (
        0.35 * normalize(r["oos_pf"], 0.5, 3.0)
        + 0.25 * normalize(winning_trades, 5, 60)
        + 0.20 * normalize(risk_adj, 0, 5.0)
        + 0.12 * normalize(r["skew"], -1.0, 2.0)
        + 0.08 * normalize(-r["max_dd"], -25, 0)
    )
    return score


def classify_tier(r, score):
    """Classify into deployment tier."""
    if score < 0:
        return "D — Skip"
    if score >= 0.65 and r["oos_pf"] >= 1.3 and r["n"] >= 30:
        return "A — Deploy"
    if score >= 0.50 and r["oos_pf"] >= 1.0 and r["n"] >= 20:
        return "B — Strong"
    if score >= 0.35:
        return "C — Watch"
    return "D — Skip"


# ═══════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════

def main():
    sep = "=" * 90
    print(sep, flush=True)
    print("  FULL UNIVERSE LIQ-CLUSTER SWEEP", flush=True)
    print("  Frozen V3 params | Composite scoring | 12-month walk-forward", flush=True)
    print(sep, flush=True)

    # Phase 1
    qualifying = discover_symbols()
    symbols = [q["symbol"] for q in qualifying]
    vol_map = {q["symbol"]: q["volume_m"] for q in qualifying}
    print(f"\n  {len(symbols)} symbols qualify\n", flush=True)

    # Phase 2
    print("PHASE 2: DATA COLLECTION", flush=True)
    print("-" * 50, flush=True)
    ready = []
    for i, sym in enumerate(symbols, 1):
        ok, msg = collect_symbol_data(sym)
        status = "✓" if ok else "✗"
        if i % 10 == 0 or not ok:
            print(f"  [{i}/{len(symbols)}] {status} {sym} {'' if ok else '— '+msg}", flush=True)
        if ok:
            ready.append(sym)
    print(f"\n  Data ready: {len(ready)}/{len(symbols)}\n", flush=True)

    # Phase 3
    print("PHASE 3: WALK-FORWARD", flush=True)
    print("-" * 50, flush=True)
    results = []
    for i, sym in enumerate(ready, 1):
        if i % 5 == 0:
            print(f"  [{i}/{len(ready)}] Running {sym}...", flush=True)

        stats = run_symbol(sym)
        if stats is None:
            continue

        stats["volume_m"] = vol_map.get(sym, 0)
        results.append(stats)

    print(f"\n  Results: {len(results)}/{len(ready)} symbols produced trades\n", flush=True)

    # Phase 4
    print("PHASE 4: SCORING & RANKING", flush=True)
    print("-" * 50, flush=True)

    for r in results:
        r["score"] = compute_score(r)
        r["tier"] = classify_tier(r, r["score"])

    # Sort by score descending
    scored = [r for r in results if r["score"] >= 0]
    disqualified = [r for r in results if r["score"] < 0]
    scored.sort(key=lambda x: x["score"], reverse=True)

    # Print ranked table
    print(f"\n{sep}", flush=True)
    print(f"  RANKED RESULTS ({len(scored)} scored + {len(disqualified)} disqualified)", flush=True)
    print(sep, flush=True)

    header = (f"  {'#':>3} {'Symbol':<12} {'Score':>6} {'Tier':<12} "
              f"{'N':>4} {'WR':>5} {'PF':>5} {'TotalR':>7} {'MaxDD':>6} "
              f"{'OOS_PF':>6} {'Skew':>5} {'Vol$M':>7}")
    print(header, flush=True)
    print("  " + "-" * 87, flush=True)

    for rank, r in enumerate(scored, 1):
        print(
            f"  {rank:>3} {r['symbol']:<12} {r['score']:>6.3f} {r['tier']:<12} "
            f"{r['n']:>4} {r['wr']:>4.0f}% {r['pf']:>5.2f} {r['total_r']:>+7.1f} "
            f"{r['max_dd']:>6.1f} {r['oos_pf']:>6.2f} {r['skew']:>+5.2f} "
            f"{r['volume_m']:>7.0f}",
            flush=True,
        )

    # Tier summary
    print(f"\n  TIER SUMMARY:", flush=True)
    for tier_name in ["A — Deploy", "B — Strong", "C — Watch"]:
        tier_syms = [r for r in scored if r["tier"] == tier_name]
        if tier_syms:
            names = ", ".join(r["symbol"] for r in tier_syms)
            total_r = sum(r["total_r"] for r in tier_syms)
            print(f"    {tier_name}: {len(tier_syms)} symbols — {names}", flush=True)
            print(f"      Combined R: {total_r:+.1f}", flush=True)

    if disqualified:
        print(f"\n  DISQUALIFIED ({len(disqualified)}):", flush=True)
        for r in disqualified:
            reason = []
            if r["oos_pf"] < 0.7 and r.get("oos_n", 0) > 5:
                reason.append(f"OOS_PF={r['oos_pf']:.2f}")
            if r["n"] < 15:
                reason.append(f"N={r['n']}")
            if r["pf"] < 0.9:
                reason.append(f"PF={r['pf']:.2f}")
            if r["max_dd"] < -25:
                reason.append(f"DD={r['max_dd']:.1f}")
            print(f"    {r['symbol']}: {', '.join(reason)}", flush=True)

    # Save results
    results_df = pd.DataFrame(scored + disqualified)
    out_path = Path("/tmp/universe_sweep_results.csv")
    results_df.to_csv(out_path, index=False)
    print(f"\n  Results saved to {out_path}", flush=True)

    print(f"\nDONE", flush=True)


if __name__ == "__main__":
    main()

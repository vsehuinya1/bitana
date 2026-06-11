"""
Cross-Asset Liq-Cluster Validation.

Tests the FROZEN SOL-tuned V3 signal on BTCUSDT and ETHUSDT
to determine if the edge generalizes.

Usage (on VPS):
    cd /root/bitana
    python3 -u research/scripts/cross_asset_validation.py
"""
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

print("=" * 70, flush=True)
print("  CROSS-ASSET LIQ-CLUSTER VALIDATION", flush=True)
print("  Testing frozen SOL parameters on BTC and ETH", flush=True)
print("=" * 70, flush=True)

import pandas as pd
import numpy as np
from loguru import logger
logger.remove()
logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level:<7} | {message}")

from research.config.settings import OHLCV_DIR, LIQUIDATION_DIR
from research.data.storage.parquet_store import load_parquet
from research.data.collectors.binance_ohlcv import collect_ohlcv
from research.data.collectors.coinalyze_liq import collect_liquidations
from research.engine.backtest import BacktestEngine
from research.engine.costs import CostModel
from research.engine.signals_liq_v2 import (
    LiqClusterExpansionSignal, LiqClusterConfig, classify_cascade_context,
)


# ═══════════════════════════════════════════════════
# Phase 1: Data Collection
# ═══════════════════════════════════════════════════

def collect_data(symbol):
    """Collect 12 months of 5m, 1h, and daily liq data for a symbol."""
    start_ms = int(time.time() * 1000) - (365 * 86400000)

    print(f"\n--- Collecting {symbol} ---", flush=True)

    # 5m OHLCV
    f5m = OHLCV_DIR / f"{symbol}_5m.parquet"
    if f5m.exists():
        existing = load_parquet(f5m)
        print(f"  5m: already have {len(existing)} bars", flush=True)
    else:
        print(f"  Collecting 5m OHLCV...", flush=True)
        collect_ohlcv(symbol, "5m", start_ms=start_ms)

    # 1h OHLCV
    f1h = OHLCV_DIR / f"{symbol}_1h.parquet"
    if f1h.exists():
        existing = load_parquet(f1h)
        print(f"  1h: already have {len(existing)} bars", flush=True)
    else:
        print(f"  Collecting 1h OHLCV...", flush=True)
        collect_ohlcv(symbol, "1h", start_ms=start_ms)

    # Daily liquidations
    fliq = LIQUIDATION_DIR / f"{symbol}_liq_daily.parquet"
    if fliq.exists():
        existing = load_parquet(fliq)
        print(f"  Liq: already have {len(existing)} bars", flush=True)
    else:
        print(f"  Collecting daily liquidations...", flush=True)
        try:
            collect_liquidations(symbol, "daily", start_ms=start_ms)
        except Exception as e:
            print(f"  !! Liq collection failed: {e}", flush=True)
            print(f"  Will proceed without liq data (generate synthetic from price vol)", flush=True)


def load_and_prepare(symbol):
    """Load data and prepare for backtest."""
    print(f"\n--- Loading {symbol} ---", flush=True)

    # 5m
    df_5m = load_parquet(OHLCV_DIR / f"{symbol}_5m.parquet")
    if df_5m.empty:
        print(f"  !! No 5m data for {symbol}", flush=True)
        return None, None
    df_5m['dt'] = pd.to_datetime(df_5m['timestamp'], unit='ms')
    print(f"  5m: {len(df_5m)} bars, {df_5m['dt'].min().date()} -> {df_5m['dt'].max().date()}", flush=True)

    # Daily from 1h
    ohlcv_1h = load_parquet(OHLCV_DIR / f"{symbol}_1h.parquet")
    if ohlcv_1h.empty:
        print(f"  !! No 1h data for {symbol}", flush=True)
        return None, None
    ohlcv_1h['dt'] = pd.to_datetime(ohlcv_1h['timestamp'], unit='ms')
    daily = ohlcv_1h.set_index('dt').resample('1D').agg({
        'timestamp': 'first', 'open': 'first', 'high': 'max',
        'low': 'min', 'close': 'last', 'volume': 'sum',
    }).dropna().reset_index()

    # Liquidations
    liq_file = LIQUIDATION_DIR / f"{symbol}_liq_daily.parquet"
    if liq_file.exists():
        liq = load_parquet(liq_file)
        if not liq.empty:
            liq['dt'] = pd.to_datetime(liq['timestamp'], unit='ms')
            daily = pd.merge_asof(
                daily.sort_values('dt'),
                liq[['dt', 'long_liquidations', 'short_liquidations']].sort_values('dt'),
                on='dt', direction='nearest',
            )
            daily['total_liq'] = daily['long_liquidations'].fillna(0) + daily['short_liquidations'].fillna(0)
            print(f"  Daily: {len(daily)} bars with liq data", flush=True)
        else:
            # Generate synthetic liq proxy from volume * volatility
            daily['total_liq'] = daily['volume'] * ((daily['high'] - daily['low']) / daily['close'])
            daily['long_liquidations'] = daily['total_liq'] * 0.5
            daily['short_liquidations'] = daily['total_liq'] * 0.5
            print(f"  Daily: {len(daily)} bars with SYNTHETIC liq proxy", flush=True)
    else:
        daily['total_liq'] = daily['volume'] * ((daily['high'] - daily['low']) / daily['close'])
        daily['long_liquidations'] = daily['total_liq'] * 0.5
        daily['short_liquidations'] = daily['total_liq'] * 0.5
        print(f"  Daily: {len(daily)} bars with SYNTHETIC liq proxy", flush=True)

    return df_5m, daily


# ═══════════════════════════════════════════════════
# Phase 2: Walk-Forward
# ═══════════════════════════════════════════════════

def run_walk_forward(symbol, df_5m, daily, cfg):
    """Run walk-forward on 2-month windows."""
    # Classify cascade context
    daily_c = classify_cascade_context(daily.copy(), cfg)

    # Prepare full 5m
    sig_prep = LiqClusterExpansionSignal(cfg)
    df_5m_p = sig_prep.prepare(df_5m.copy(), daily_c)

    windows = [
        ("May-Jun 25", "2025-05-01", "2025-07-01", "OOS"),
        ("Jul-Aug 25", "2025-07-01", "2025-09-01", "OOS"),
        ("Sep-Oct 25", "2025-09-01", "2025-11-01", "OOS"),
        ("Nov-Dec 25", "2025-11-01", "2026-01-01", "IS"),
        ("Jan-Feb 26", "2026-01-01", "2026-03-01", "IS"),
        ("Mar-Apr 26", "2026-03-01", "2026-05-01", "IS"),
        ("May 26",     "2026-05-01", "2026-06-01", "IS"),
    ]

    oos_r = []
    is_r = []
    per_window = []

    for wlabel, start, end, sample in windows:
        s = pd.Timestamp(start)
        e = pd.Timestamp(end)
        mask = (df_5m_p['dt'] >= s) & (df_5m_p['dt'] < e)
        wdf = df_5m_p[mask].copy()

        if len(wdf) < 100:
            per_window.append((wlabel, 0, 0, 0, 0, 0, 0, sample))
            continue

        signal = LiqClusterExpansionSignal(cfg)
        engine = BacktestEngine(cost_model=CostModel(), initial_capital=10000)
        engine.run(wdf, signal.evaluate, context={'capital': 10000})
        t = engine.get_trades()

        if t.empty:
            per_window.append((wlabel, 0, 0, 0, 0, 0, 0, sample))
            continue

        sr = (t['risk_per_unit'] * t['size']).replace(0, np.nan)
        t['r'] = t['pnl_net'] / sr
        r = t['r'].dropna()
        n = len(t)
        ws = r[r > 0].sum()
        ls = abs(r[r <= 0].sum())
        pf = ws / ls if ls > 0 else 99
        stops = (t.get('exit_reason', pd.Series()) == 'stop_loss').sum()
        wr = (r > 0).mean() * 100
        total_r = r.sum()
        sk = float(pd.Series(r).skew()) if len(r) > 2 else 0

        if sample == "OOS":
            oos_r.extend(r.tolist())
        else:
            is_r.extend(r.tolist())

        per_window.append((wlabel, n, wr, pf, total_r, stops, sk, sample))

    return per_window, oos_r, is_r


def print_results(symbol, per_window, oos_r, is_r):
    """Print formatted results."""
    print(f"\n{'=' * 70}", flush=True)
    print(f"  {symbol} — V3 WALK-FORWARD (frozen SOL params)", flush=True)
    print(f"{'=' * 70}", flush=True)
    header = f"  {'Period':<22} {'Trades':>6} {'WR':>5} {'PF':>6} {'TotalR':>8} {'Stops':>8} {'Skew':>6}"
    print(header, flush=True)
    print("  " + "-" * 67, flush=True)

    for wlabel, n, wr, pf, tr, stops, sk, sample in per_window:
        tag = "*" if sample == "OOS" else " "
        if n == 0:
            print(f"  {tag}{wlabel:<21} {0:>6}    --     --       --       --     --", flush=True)
        else:
            stop_pct = stops / n * 100
            print(f"  {tag}{wlabel:<21} {n:>6} {wr:>4.0f}% {pf:>6.2f} {tr:>+8.1f} {stops:>4}({stop_pct:.0f}%) {sk:>+6.2f}", flush=True)

    print("  " + "-" * 67, flush=True)

    all_r = np.array(oos_r + is_r)
    if len(all_r) > 0:
        ws = all_r[all_r > 0].sum()
        ls = abs(all_r[all_r <= 0].sum())
        pf = ws / ls if ls > 0 else 99
        wr = (all_r > 0).mean() * 100
        tr = all_r.sum()
    else:
        pf = 0; wr = 0; tr = 0

    oos_arr = np.array(oos_r) if oos_r else np.array([0])
    if len(oos_r) > 0:
        oos_ws = oos_arr[oos_arr > 0].sum()
        oos_ls = abs(oos_arr[oos_arr <= 0].sum())
        oos_pf = oos_ws / oos_ls if oos_ls > 0 else 99
    else:
        oos_pf = 0

    is_arr = np.array(is_r) if is_r else np.array([0])
    if len(is_r) > 0:
        is_ws = is_arr[is_arr > 0].sum()
        is_ls = abs(is_arr[is_arr <= 0].sum())
        is_pf = is_ws / is_ls if is_ls > 0 else 99
    else:
        is_pf = 0

    if len(oos_r) > 0:
        print(f"  OOS: {len(oos_r)}t WR={((oos_arr>0).mean()*100):.0f}% PF={oos_pf:.2f} R={oos_arr.sum():+.1f}", flush=True)
    if len(is_r) > 0:
        print(f"  IS:  {len(is_r)}t WR={((is_arr>0).mean()*100):.0f}% PF={is_pf:.2f} R={is_arr.sum():+.1f}", flush=True)
    print(f"  FULL: {len(all_r)}t WR={wr:.0f}% PF={pf:.2f} R={tr:+.1f}", flush=True)

    return {
        'symbol': symbol,
        'n': len(all_r),
        'wr': wr,
        'pf': pf,
        'total_r': tr,
        'oos_pf': oos_pf,
        'oos_r': oos_arr.sum() if oos_r else 0,
    }


# ═══════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════

def main():
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

    # Frozen SOL-tuned V3 config
    cfg = LiqClusterConfig(
        min_confirmations=4,
        require_short_squeeze=True,
        vol_z_threshold=3.0,
        initial_stop_atr=2.5,
        ret5d_min=-5.0,
        partial_r=2.5,
    )

    # Phase 1: Collect data
    print("\n" + "=" * 70, flush=True)
    print("  PHASE 1: DATA COLLECTION", flush=True)
    print("=" * 70, flush=True)
    for symbol in symbols:
        collect_data(symbol)

    # Phase 2: Run walk-forward for each
    print("\n" + "=" * 70, flush=True)
    print("  PHASE 2: WALK-FORWARD VALIDATION", flush=True)
    print("=" * 70, flush=True)

    all_results = []
    for symbol in symbols:
        df_5m, daily = load_and_prepare(symbol)
        if df_5m is None:
            continue
        per_window, oos_r, is_r = run_walk_forward(symbol, df_5m, daily, cfg)
        result = print_results(symbol, per_window, oos_r, is_r)
        all_results.append(result)

    # Phase 3: Summary comparison
    print("\n" + "=" * 70, flush=True)
    print("  CROSS-ASSET SUMMARY", flush=True)
    print("=" * 70, flush=True)
    print(f"  {'Symbol':<12} {'Trades':>7} {'WR':>5} {'PF':>6} {'TotalR':>8} {'OOS_PF':>7}", flush=True)
    print("  " + "-" * 50, flush=True)
    for r in all_results:
        print(f"  {r['symbol']:<12} {r['n']:>7} {r['wr']:>4.0f}% {r['pf']:>6.2f} {r['total_r']:>+8.1f} {r['oos_pf']:>7.2f}", flush=True)

    print("", flush=True)

    # Verdict
    profitable = [r for r in all_results if r['pf'] > 1.0]
    if len(profitable) == len(all_results):
        print("  >>> GENERAL PHENOMENON — edge works across all assets", flush=True)
    elif len(profitable) > 1:
        names = ", ".join(r['symbol'] for r in profitable)
        print(f"  >>> PARTIAL GENERALIZATION — works on: {names}", flush=True)
    elif len(profitable) == 1:
        print(f"  >>> ASSET-SPECIFIC — only works on {profitable[0]['symbol']}", flush=True)
    else:
        print(f"  >>> NO EDGE DETECTED on any asset", flush=True)

    print("\nDONE", flush=True)


if __name__ == "__main__":
    main()

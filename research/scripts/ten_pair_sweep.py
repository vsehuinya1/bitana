"""
10-Pair Liq-Cluster Sweep — VPS Runner.

Collects data and runs frozen V3 signal on 10 SOL-adjacent pairs.
All data collection via Binance API (OHLCV) and Coinalyze API (liquidations).

Usage (on VPS):
    cd /root/bitana
    python3 -u research/scripts/ten_pair_sweep.py
"""
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

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

# ═══════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════

SYMBOLS = [
    # Tier 1: retail leverage + squeeze
    "DOGEUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT", "MATICUSDT",
    # Tier 2: mid-cap alts
    "BNBUSDT", "LINKUSDT", "NEARUSDT",
    # New additions
    "SUIUSDT", "FILUSDT",
    # Baseline (already validated)
    "SOLUSDT",
]

WINDOWS = [
    ("May-Jun 25", "2025-05-01", "2025-07-01", "OOS"),
    ("Jul-Aug 25", "2025-07-01", "2025-09-01", "OOS"),
    ("Sep-Oct 25", "2025-09-01", "2025-11-01", "OOS"),
    ("Nov-Dec 25", "2025-11-01", "2026-01-01", "IS"),
    ("Jan-Feb 26", "2026-01-01", "2026-03-01", "IS"),
    ("Mar-Apr 26", "2026-03-01", "2026-05-01", "IS"),
    ("May 26",     "2026-05-01", "2026-06-01", "IS"),
]

# Frozen SOL-tuned V3 config
CFG = LiqClusterConfig(
    min_confirmations=4,
    require_short_squeeze=True,
    vol_z_threshold=3.0,
    initial_stop_atr=2.5,
    ret5d_min=-5.0,
    partial_r=2.5,
)


# ═══════════════════════════════════════════════════
# Data Collection
# ═══════════════════════════════════════════════════

def collect_symbol_data(symbol):
    """Collect 12 months of 5m, 1h OHLCV + daily liquidations."""
    start_ms = int(time.time() * 1000) - (365 * 86400000)

    # 5m OHLCV
    f5m = OHLCV_DIR / f"{symbol}_5m.parquet"
    if not f5m.exists():
        print(f"  {symbol} 5m OHLCV...", flush=True)
        try:
            collect_ohlcv(symbol, "5m", start_ms=start_ms)
        except Exception as e:
            print(f"    FAIL: {e}", flush=True)
            return False

    # 1h OHLCV
    f1h = OHLCV_DIR / f"{symbol}_1h.parquet"
    if not f1h.exists():
        print(f"  {symbol} 1h OHLCV...", flush=True)
        try:
            collect_ohlcv(symbol, "1h", start_ms=start_ms)
        except Exception as e:
            print(f"    FAIL: {e}", flush=True)
            return False

    # Daily liquidations from Coinalyze
    fliq = LIQUIDATION_DIR / f"{symbol}_liq_daily.parquet"
    if not fliq.exists():
        print(f"  {symbol} liq (Coinalyze)...", flush=True)
        if symbol not in COINALYZE_SYMBOL_MAP:
            print(f"    No Coinalyze mapping for {symbol}", flush=True)
            return False
        try:
            collect_liquidations(symbol, "daily")
        except Exception as e:
            print(f"    FAIL: {e}", flush=True)
            return False

    return True


# ═══════════════════════════════════════════════════
# Backtest
# ═══════════════════════════════════════════════════

def load_and_prepare(symbol):
    """Load data and prepare for backtest."""
    df_5m = load_parquet(OHLCV_DIR / f"{symbol}_5m.parquet")
    if df_5m.empty:
        return None, None
    df_5m['dt'] = pd.to_datetime(df_5m['timestamp'], unit='ms')

    ohlcv_1h = load_parquet(OHLCV_DIR / f"{symbol}_1h.parquet")
    if ohlcv_1h.empty:
        return None, None
    ohlcv_1h['dt'] = pd.to_datetime(ohlcv_1h['timestamp'], unit='ms')
    daily = ohlcv_1h.set_index('dt').resample('1D').agg({
        'timestamp': 'first', 'open': 'first', 'high': 'max',
        'low': 'min', 'close': 'last', 'volume': 'sum',
    }).dropna().reset_index()

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
            return df_5m, daily

    return None, None  # No liq data = skip


def run_walk_forward(symbol, df_5m, daily):
    """Run walk-forward on windows."""
    daily_c = classify_cascade_context(daily.copy(), CFG)
    sig_prep = LiqClusterExpansionSignal(CFG)
    df_5m_p = sig_prep.prepare(df_5m.copy(), daily_c)

    oos_r = []
    is_r = []
    per_window = []

    for wlabel, start, end, sample in WINDOWS:
        s = pd.Timestamp(start)
        e = pd.Timestamp(end)
        mask = (df_5m_p['dt'] >= s) & (df_5m_p['dt'] < e)
        wdf = df_5m_p[mask].copy()

        if len(wdf) < 100:
            per_window.append((wlabel, 0, 0, 0, 0, 0, sample))
            continue

        signal = LiqClusterExpansionSignal(CFG)
        engine = BacktestEngine(cost_model=CostModel(), initial_capital=10000)
        engine.run(wdf, signal.evaluate, context={'capital': 10000})
        t = engine.get_trades()

        if t.empty:
            per_window.append((wlabel, 0, 0, 0, 0, 0, sample))
            continue

        sr = (t['risk_per_unit'] * t['size']).replace(0, np.nan)
        t['r'] = t['pnl_net'] / sr
        r = t['r'].dropna()
        n = len(t)
        ws = r[r > 0].sum()
        ls = abs(r[r <= 0].sum())
        pf = ws / ls if ls > 0 else 99
        stops = (t.get('exit_reason', pd.Series()) == 'stop_loss').sum()
        total_r = r.sum()

        if sample == "OOS":
            oos_r.extend(r.tolist())
        else:
            is_r.extend(r.tolist())

        per_window.append((wlabel, n, (r > 0).mean() * 100, pf, total_r, stops, sample))

    return per_window, oos_r, is_r


# ═══════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════

def main():
    sep = "=" * 80
    print(sep, flush=True)
    print("  10-PAIR LIQ-CLUSTER SWEEP (frozen SOL V3 params)", flush=True)
    print(sep, flush=True)

    # Phase 1: Collect data
    print("\nPHASE 1: DATA COLLECTION", flush=True)
    print("-" * 40, flush=True)
    ready = []
    for symbol in SYMBOLS:
        ok = collect_symbol_data(symbol)
        status = "✓" if ok else "✗"
        print(f"  {status} {symbol}", flush=True)
        if ok:
            ready.append(symbol)

    # Phase 2: Walk-forward
    print(f"\nPHASE 2: WALK-FORWARD ({len(ready)} symbols)", flush=True)
    print("-" * 40, flush=True)

    all_results = []
    for i, symbol in enumerate(ready, 1):
        print(f"\n  [{i}/{len(ready)}] {symbol}...", flush=True)
        df_5m, daily = load_and_prepare(symbol)
        if df_5m is None:
            print(f"    SKIP — no data", flush=True)
            continue

        per_window, oos_r, is_r = run_walk_forward(symbol, df_5m, daily)

        all_r = np.array(oos_r + is_r)
        if len(all_r) == 0:
            all_results.append({
                'symbol': symbol, 'n': 0, 'wr': 0, 'pf': 0,
                'total_r': 0, 'oos_pf': 0, 'oos_n': 0,
                'max_dd': 0, 'stops_pct': 0,
            })
            continue

        ws = all_r[all_r > 0].sum()
        ls = abs(all_r[all_r <= 0].sum())
        pf = ws / ls if ls > 0 else 99

        oos_arr = np.array(oos_r) if oos_r else np.array([0])
        oos_ws = oos_arr[oos_arr > 0].sum()
        oos_ls = abs(oos_arr[oos_arr <= 0].sum())
        oos_pf = oos_ws / oos_ls if oos_ls > 0 else 99

        cum_r = np.cumsum(all_r)
        peak = np.maximum.accumulate(cum_r)
        max_dd = (cum_r - peak).min()

        # Per-window detail
        for wlabel, n, wr, wpf, tr, stops, sample in per_window:
            if n > 0:
                tag = "OOS" if sample == "OOS" else " IS"
                print(f"    {tag} {wlabel}: {n}t PF={wpf:.2f} R={tr:+.1f}", flush=True)

        all_results.append({
            'symbol': symbol,
            'n': len(all_r),
            'wr': (all_r > 0).mean() * 100,
            'pf': pf,
            'total_r': all_r.sum(),
            'oos_pf': oos_pf if oos_r else 0,
            'oos_n': len(oos_r),
            'max_dd': max_dd,
            'stops_pct': 0,  # computed below
        })

    # Phase 3: Summary
    all_results.sort(key=lambda x: x['total_r'], reverse=True)

    print(f"\n{sep}", flush=True)
    print(f"  CROSS-ASSET SUMMARY — RANKED BY TOTAL R", flush=True)
    print(f"{sep}", flush=True)
    header = f"  {'#':>2} {'Symbol':<12} {'Trades':>7} {'WR':>5} {'PF':>6} {'TotalR':>8} {'MaxDD':>7} {'OOS_PF':>7} {'Verdict':>10}"
    print(header, flush=True)
    print("  " + "-" * 72, flush=True)

    for rank, r in enumerate(all_results, 1):
        if r['n'] == 0:
            verdict = "NO DATA"
        elif r['pf'] >= 1.2 and r['oos_pf'] >= 1.0:
            verdict = "✓ DEPLOY"
        elif r['pf'] >= 1.0:
            verdict = "~ MARGINAL"
        else:
            verdict = "✗ SKIP"

        print(
            f"  {rank:>2} {r['symbol']:<12} {r['n']:>7} {r['wr']:>4.0f}% {r['pf']:>6.2f} "
            f"{r['total_r']:>+8.1f} {r['max_dd']:>7.1f} {r['oos_pf']:>7.2f} {verdict:>10}",
            flush=True,
        )

    # Deployable assets
    deployable = [r for r in all_results if r['pf'] >= 1.2 and r['oos_pf'] >= 1.0 and r['n'] >= 20]
    marginal = [r for r in all_results if 1.0 <= r['pf'] < 1.2 and r['n'] >= 20]

    print(f"\n  DEPLOYABLE (PF>=1.2, OOS>=1.0, N>=20):", flush=True)
    if deployable:
        for r in deployable:
            print(f"    {r['symbol']}: PF={r['pf']:.2f} R={r['total_r']:+.1f} OOS_PF={r['oos_pf']:.2f}", flush=True)
    else:
        print(f"    None", flush=True)

    print(f"  MARGINAL (PF>=1.0, N>=20):", flush=True)
    if marginal:
        for r in marginal:
            print(f"    {r['symbol']}: PF={r['pf']:.2f} R={r['total_r']:+.1f}", flush=True)
    else:
        print(f"    None", flush=True)

    print(f"\nDONE", flush=True)


if __name__ == "__main__":
    main()

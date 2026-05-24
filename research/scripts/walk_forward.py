"""
Walk-Forward Validation — V3 Liq-Cluster Signal.

Tests the forensic-refined V3 signal across rolling 2-month windows.
May-Oct 2025 is true out-of-sample (parameters tuned on Nov 2025 - May 2026).

Usage:
    python -u research/scripts/walk_forward.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

print("Loading...", flush=True)

import pandas as pd
import numpy as np
from loguru import logger
logger.remove()
logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level:<7} | {message}")

from research.config.settings import OHLCV_DIR, LIQUIDATION_DIR, REPORTS_DIR
from research.data.storage.parquet_store import load_parquet
from research.engine.backtest import BacktestEngine
from research.engine.costs import CostModel
from research.engine.signals_liq_v2 import (
    LiqClusterExpansionSignal, LiqClusterConfig, classify_cascade_context,
)


def load_data():
    """Load 12-month 5m + full daily."""
    print("Loading 5m...", flush=True)
    df_5m = load_parquet(OHLCV_DIR / "SOLUSDT_5m.parquet")
    df_5m['dt'] = pd.to_datetime(df_5m['timestamp'], unit='ms')
    print(f"  5m: {len(df_5m)} bars, {df_5m['dt'].min().date()} -> {df_5m['dt'].max().date()}", flush=True)

    print("Loading daily...", flush=True)
    ohlcv_1h = load_parquet(OHLCV_DIR / "SOLUSDT_1h.parquet")
    ohlcv_1h['dt'] = pd.to_datetime(ohlcv_1h['timestamp'], unit='ms')
    daily = ohlcv_1h.set_index('dt').resample('1D').agg({
        'timestamp': 'first', 'open': 'first', 'high': 'max',
        'low': 'min', 'close': 'last', 'volume': 'sum',
    }).dropna().reset_index()

    liq = load_parquet(LIQUIDATION_DIR / "SOLUSDT_liq_daily.parquet")
    liq['dt'] = pd.to_datetime(liq['timestamp'], unit='ms')
    daily = pd.merge_asof(
        daily.sort_values('dt'),
        liq[['dt', 'long_liquidations', 'short_liquidations']].sort_values('dt'),
        on='dt', direction='nearest',
    )
    daily['total_liq'] = daily['long_liquidations'].fillna(0) + daily['short_liquidations'].fillna(0)
    print(f"  Daily: {len(daily)} bars", flush=True)

    return df_5m, daily


def run_window(df_5m_prep, start_dt, end_dt, cfg):
    """Run backtest on a specific time window."""
    mask = (df_5m_prep['dt'] >= start_dt) & (df_5m_prep['dt'] < end_dt)
    window = df_5m_prep[mask].copy()

    if len(window) < 100:
        return None

    signal = LiqClusterExpansionSignal(cfg)
    engine = BacktestEngine(cost_model=CostModel(), initial_capital=10000)
    engine.run(window, signal.evaluate, context={'capital': 10000})

    trades = engine.get_trades()
    if trades.empty:
        return {'n': 0, 'r_values': [], 'stops': 0}

    sr = (trades['risk_per_unit'] * trades['size']).replace(0, np.nan)
    trades['r'] = trades['pnl_net'] / sr
    r = trades['r'].dropna()
    stops = (trades.get('exit_reason', pd.Series()) == 'stop_loss').sum()

    return {
        'n': len(trades),
        'r_values': r.tolist(),
        'stops': stops,
    }


def main():
    df_5m, daily = load_data()

    # V3 forensic-refined config
    cfg = LiqClusterConfig(
        min_confirmations=4,
        require_short_squeeze=True,
        vol_z_threshold=3.0,
        initial_stop_atr=2.5,
    )

    # Classify regimes on full daily
    print("Classifying regimes...", flush=True)
    daily_classified = classify_cascade_context(daily.copy(), cfg)

    # Prepare full 5m dataset once (features + regime merge)
    print("Preparing 5m features...", flush=True)
    signal_prep = LiqClusterExpansionSignal(cfg)
    df_5m_prep = signal_prep.prepare(df_5m.copy(), daily_classified)

    # Define windows
    windows = [
        ("May-Jun 25 (OOS)", "2025-05-01", "2025-07-01", "OOS"),
        ("Jul-Aug 25 (OOS)", "2025-07-01", "2025-09-01", "OOS"),
        ("Sep-Oct 25 (OOS)", "2025-09-01", "2025-11-01", "OOS"),
        ("Nov-Dec 25 (IS)",  "2025-11-01", "2026-01-01", "IS"),
        ("Jan-Feb 26 (IS)",  "2026-01-01", "2026-03-01", "IS"),
        ("Mar-Apr 26 (IS)",  "2026-03-01", "2026-05-01", "IS"),
        ("May 26 (IS)",      "2026-05-01", "2026-06-01", "IS"),
    ]

    sep = "=" * 72
    print("", flush=True)
    print(sep, flush=True)
    print("  V3 4-of-6 WALK-FORWARD (squeeze=ON, vol_z>3, stop=2.5x)", flush=True)
    print(sep, flush=True)
    header = f"  {'Period':<22} {'Trades':>6} {'/wk':>5} {'WR':>5} {'PF':>6} {'TotalR':>8} {'Stops':>8} {'Skew':>6}"
    print(header, flush=True)
    print("  " + "-" * 67, flush=True)

    oos_r = []
    is_r = []

    for label, start, end, sample in windows:
        start_dt = pd.Timestamp(start)
        end_dt = pd.Timestamp(end)
        result = run_window(df_5m_prep, start_dt, end_dt, cfg)

        if result is None or result['n'] == 0:
            print(f"  {label:<22} {0:>6}   --    --     --      --       --     --", flush=True)
            continue

        n = result['n']
        r = np.array(result['r_values'])
        stops = result['stops']
        days = (end_dt - start_dt).days
        tpw = n / (days / 7) if days > 0 else 0
        wr = (r > 0).mean() * 100
        w_sum = r[r > 0].sum()
        l_sum = abs(r[r <= 0].sum())
        pf = w_sum / l_sum if l_sum > 0 else float('inf')
        total_r = r.sum()
        sk = float(pd.Series(r).skew()) if len(r) > 2 else 0

        stop_pct = stops / n * 100

        if sample == "OOS":
            oos_r.extend(r.tolist())
        else:
            is_r.extend(r.tolist())

        print(f"  {label:<22} {n:>6} {tpw:>5.1f} {wr:>4.0f}% {pf:>6.2f} {total_r:>+8.1f} {stops:>4}({stop_pct:.0f}%) {sk:>+6.2f}", flush=True)

    print("  " + "-" * 67, flush=True)

    # OOS summary
    if oos_r:
        r_oos = np.array(oos_r)
        ws = r_oos[r_oos > 0].sum()
        ls = abs(r_oos[r_oos <= 0].sum())
        pf = ws / ls if ls > 0 else float('inf')
        wr = (r_oos > 0).mean() * 100
        print(f"  {'OOS TOTAL':<22} {len(r_oos):>6}       {wr:>4.0f}% {pf:>6.2f} {r_oos.sum():>+8.1f}", flush=True)

    # IS summary
    if is_r:
        r_is = np.array(is_r)
        ws = r_is[r_is > 0].sum()
        ls = abs(r_is[r_is <= 0].sum())
        pf = ws / ls if ls > 0 else float('inf')
        wr = (r_is > 0).mean() * 100
        print(f"  {'IS TOTAL':<22} {len(r_is):>6}       {wr:>4.0f}% {pf:>6.2f} {r_is.sum():>+8.1f}", flush=True)

    # Full
    all_r = oos_r + is_r
    if all_r:
        r_all = np.array(all_r)
        ws = r_all[r_all > 0].sum()
        ls = abs(r_all[r_all <= 0].sum())
        pf = ws / ls if ls > 0 else float('inf')
        wr = (r_all > 0).mean() * 100
        print(f"  {'FULL 12M':<22} {len(r_all):>6}       {wr:>4.0f}% {pf:>6.2f} {r_all.sum():>+8.1f}", flush=True)

    # OOS vs IS comparison
    print("", flush=True)
    if oos_r and is_r:
        r_oos = np.array(oos_r)
        r_is = np.array(is_r)
        oos_pf = r_oos[r_oos>0].sum() / abs(r_oos[r_oos<=0].sum()) if (r_oos<=0).any() else float('inf')
        is_pf = r_is[r_is>0].sum() / abs(r_is[r_is<=0].sum()) if (r_is<=0).any() else float('inf')
        print(f"  OOS PF: {oos_pf:.2f} | IS PF: {is_pf:.2f}", flush=True)
        if oos_pf >= 0.8:
            print(f"  >>> OOS HOLDS — signal generalises", flush=True)
        elif oos_pf >= 0.6:
            print(f"  >>> OOS PARTIAL — some regime dependence", flush=True)
        else:
            print(f"  >>> OOS FAILS — likely overfit", flush=True)

    print("", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()

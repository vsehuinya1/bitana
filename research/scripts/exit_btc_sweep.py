"""
Runner Exit Optimization + BTC Alignment Sweep.

Tests variations of:
1. Partial take-profit level (2R, 2.5R, 3R)
2. Partial fraction (25%, 33%, 50%)
3. BTC trend alignment (on/off)

Runs each combo on full 12-month dataset via walk-forward windows.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
print("Loading...", flush=True)

import pandas as pd
import numpy as np
from itertools import product
from loguru import logger
logger.remove()
logger.add(sys.stderr, level="WARNING")

from research.config.settings import OHLCV_DIR, LIQUIDATION_DIR
from research.data.storage.parquet_store import load_parquet
from research.engine.backtest import BacktestEngine
from research.engine.costs import CostModel
from research.engine.signals_liq_v2 import (
    LiqClusterExpansionSignal, LiqClusterConfig, classify_cascade_context,
)


def load_data():
    """Load SOL 5m, SOL daily, BTC daily."""
    df_5m = load_parquet(OHLCV_DIR / "SOLUSDT_5m.parquet")
    df_5m['dt'] = pd.to_datetime(df_5m['timestamp'], unit='ms')
    print(f"  SOL 5m: {len(df_5m)} bars", flush=True)

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

    # BTC daily
    btc_1h = load_parquet(OHLCV_DIR / "BTCUSDT_1h.parquet")
    btc_1h['dt'] = pd.to_datetime(btc_1h['timestamp'], unit='ms')
    btc_daily = btc_1h.set_index('dt').resample('1D').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last',
    }).dropna().reset_index()
    btc_daily['btc_ema20'] = btc_daily['close'].ewm(span=20).mean()
    btc_daily['btc_ema50'] = btc_daily['close'].ewm(span=50).mean()
    btc_daily['btc_trend'] = btc_daily['btc_ema20'] > btc_daily['btc_ema50']
    btc_daily['btc_ret_5d'] = btc_daily['close'].pct_change(5) * 100
    print(f"  BTC daily: {len(btc_daily)} bars", flush=True)

    return df_5m, daily, btc_daily


def run_variant(df_5m_prep, cfg, windows):
    """Run backtest on walk-forward windows, return aggregate stats."""
    oos_r = []
    is_r = []

    for _, start, end, sample in windows:
        s = pd.Timestamp(start)
        e = pd.Timestamp(end)
        mask = (df_5m_prep['dt'] >= s) & (df_5m_prep['dt'] < e)
        wdf = df_5m_prep[mask].copy()
        if len(wdf) < 100:
            continue

        signal = LiqClusterExpansionSignal(cfg)
        engine = BacktestEngine(cost_model=CostModel(), initial_capital=10000)
        engine.run(wdf, signal.evaluate, context={'capital': 10000})
        t = engine.get_trades()
        if t.empty:
            continue

        sr = (t['risk_per_unit'] * t['size']).replace(0, np.nan)
        t['r'] = t['pnl_net'] / sr
        r = t['r'].dropna()
        if sample == "OOS":
            oos_r.extend(r.tolist())
        else:
            is_r.extend(r.tolist())

    all_r = np.array(oos_r + is_r)
    if len(all_r) == 0:
        return None

    oos_arr = np.array(oos_r) if oos_r else np.array([0])
    is_arr = np.array(is_r) if is_r else np.array([0])

    ws = all_r[all_r > 0].sum()
    ls = abs(all_r[all_r <= 0].sum())

    oos_ws = oos_arr[oos_arr > 0].sum()
    oos_ls = abs(oos_arr[oos_arr <= 0].sum())

    # Max consecutive loss streak
    losing = (all_r <= 0).astype(int)
    if len(losing) > 0:
        streaks = []
        current = 0
        for x in losing:
            if x:
                current += 1
            else:
                if current > 0:
                    streaks.append(current)
                current = 0
        if current > 0:
            streaks.append(current)
        max_streak = max(streaks) if streaks else 0
    else:
        max_streak = 0

    # Cumulative R drawdown
    cum_r = np.cumsum(all_r)
    peak = np.maximum.accumulate(cum_r)
    dd = cum_r - peak
    max_dd = dd.min()

    return {
        'n': len(all_r),
        'wr': (all_r > 0).mean() * 100,
        'pf': ws / ls if ls > 0 else 99,
        'total_r': all_r.sum(),
        'avg_r': all_r.mean(),
        'skew': float(pd.Series(all_r).skew()) if len(all_r) > 2 else 0,
        'max_r': all_r.max(),
        'oos_pf': oos_ws / oos_ls if oos_ls > 0 else 99,
        'oos_r': oos_arr.sum() if oos_r else 0,
        'max_dd_r': max_dd,
        'max_streak': max_streak,
        'avg_win': all_r[all_r > 0].mean() if (all_r > 0).any() else 0,
        'avg_loss': all_r[all_r <= 0].mean() if (all_r <= 0).any() else 0,
    }


def main():
    df_5m, daily, btc_daily = load_data()

    windows = [
        ("May-Jun 25", "2025-05-01", "2025-07-01", "OOS"),
        ("Jul-Aug 25", "2025-07-01", "2025-09-01", "OOS"),
        ("Sep-Oct 25", "2025-09-01", "2025-11-01", "OOS"),
        ("Nov-Dec 25", "2025-11-01", "2026-01-01", "IS"),
        ("Jan-Feb 26", "2026-01-01", "2026-03-01", "IS"),
        ("Mar-Apr 26", "2026-03-01", "2026-05-01", "IS"),
        ("May 26",     "2026-05-01", "2026-06-01", "IS"),
    ]

    # Sweep parameters
    partial_rs = [2.0, 2.5, 3.0]
    partial_fracs = [0.25, 0.33, 0.50]
    btc_options = [False, True]  # Whether to require BTC uptrend
    decay_thresholds = [0.30, 0.40]  # expansion decay sensitivity

    results = []
    total = len(partial_rs) * len(partial_fracs) * len(btc_options) * len(decay_thresholds)
    count = 0

    for partial_r, partial_frac, btc_align, decay_th in product(
        partial_rs, partial_fracs, btc_options, decay_thresholds
    ):
        count += 1
        cfg = LiqClusterConfig(
            min_confirmations=4,
            require_short_squeeze=True,
            vol_z_threshold=3.0,
            initial_stop_atr=2.5,
            ret5d_min=-5.0,
            partial_r=partial_r,
            partial_fraction=partial_frac,
            decay_threshold=decay_th,
        )

        # Prepare daily context
        daily_c = classify_cascade_context(daily.copy(), cfg)

        # Add BTC trend if needed
        if btc_align:
            daily_c = pd.merge_asof(
                daily_c.sort_values('dt'),
                btc_daily[['dt', 'btc_trend']].sort_values('dt'),
                on='dt', direction='backward',
            )
        
        sig_prep = LiqClusterExpansionSignal(cfg)
        df_5m_p = sig_prep.prepare(df_5m.copy(), daily_c)

        # Apply BTC filter after prepare
        if btc_align:
            df_5m_p = pd.merge_asof(
                df_5m_p.sort_values('dt'),
                btc_daily[['dt', 'btc_trend']].sort_values('dt'),
                on='dt', direction='backward',
            )
            df_5m_p.loc[df_5m_p['btc_trend'] == False, 'entry_signal'] = False

        stats = run_variant(df_5m_p, cfg, windows)
        if stats is None:
            continue

        stats['partial_r'] = partial_r
        stats['partial_frac'] = partial_frac
        stats['btc_align'] = btc_align
        stats['decay_th'] = decay_th
        results.append(stats)

        if count % 6 == 0:
            print(f"  {count}/{total} variants tested...", flush=True)

    # Sort by total R
    results.sort(key=lambda x: x['total_r'], reverse=True)

    # Print results
    sep = "=" * 95
    print("", flush=True)
    print(sep, flush=True)
    print("  RUNNER EXIT + BTC ALIGNMENT SWEEP", flush=True)
    print(sep, flush=True)
    header = (f"  {'#':>2} {'Part@':>5} {'Frac':>5} {'BTC':>4} {'Decay':>5} | "
              f"{'Trades':>6} {'WR':>4} {'PF':>5} {'TotalR':>7} {'AvgWin':>6} {'AvgLoss':>7} "
              f"{'MaxR':>5} {'Skew':>5} {'MaxDD':>6} {'OOS_PF':>6}")
    print(header, flush=True)
    print("  " + "-" * 92, flush=True)

    for rank, r in enumerate(results[:20], 1):
        btc_label = "Y" if r['btc_align'] else "N"
        print(
            f"  {rank:>2} {r['partial_r']:>5.1f} {r['partial_frac']:>5.0%} {btc_label:>4} "
            f"{r['decay_th']:>5.2f} | "
            f"{r['n']:>6} {r['wr']:>3.0f}% {r['pf']:>5.2f} {r['total_r']:>+7.1f} "
            f"{r['avg_win']:>6.2f} {r['avg_loss']:>7.2f} {r['max_r']:>5.1f} "
            f"{r['skew']:>+5.2f} {r['max_dd_r']:>6.1f} {r['oos_pf']:>6.2f}",
            flush=True,
        )

    # Highlight best
    if results:
        best = results[0]
        print("", flush=True)
        print(f"  BEST: partial@{best['partial_r']:.1f}R "
              f"frac={best['partial_frac']:.0%} BTC={'ON' if best['btc_align'] else 'OFF'} "
              f"decay={best['decay_th']:.2f}", flush=True)
        print(f"  → {best['n']}t PF={best['pf']:.2f} R={best['total_r']:+.1f} "
              f"WR={best['wr']:.0f}% MaxDD={best['max_dd_r']:.1f}R "
              f"OOS_PF={best['oos_pf']:.2f}", flush=True)

        # Current baseline for comparison
        baseline = [r for r in results
                    if r['partial_r'] == 2.0 and r['partial_frac'] == 0.50
                    and not r['btc_align'] and r['decay_th'] == 0.30]
        if baseline:
            b = baseline[0]
            print(f"  BASE: partial@2.0R frac=50% BTC=OFF decay=0.30", flush=True)
            print(f"  → {b['n']}t PF={b['pf']:.2f} R={b['total_r']:+.1f} "
                  f"WR={b['wr']:.0f}% MaxDD={b['max_dd_r']:.1f}R "
                  f"OOS_PF={b['oos_pf']:.2f}", flush=True)

    print("", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()

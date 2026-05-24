"""
Multi-Timeframe Backtest Runner.

Wires together:
1. Daily data + RegimeClassifier → regime context
2. 5m data + MultiTFExecutionSignal → trade execution
3. BacktestEngine → simulation
4. Analytics → R-based results

Usage:
    python -u research/scripts/run_multi_tf.py
    python -u research/scripts/run_multi_tf.py --min-triggers 1 --hold 288
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

print("Loading modules...", flush=True)

import argparse
import pandas as pd
import numpy as np
from loguru import logger

logger.remove()
logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level:<7} | {message}")

from research.config.settings import OHLCV_DIR, LIQUIDATION_DIR, OI_DIR, REPORTS_DIR
from research.data.storage.parquet_store import load_parquet
from research.engine.backtest import BacktestEngine
from research.engine.costs import CostModel
from research.engine.regime_classifier import RegimeClassifier, RegimeConfig
from research.engine.signals_5m import (
    MultiTFExecutionSignal, ExecutionConfig, EntryConfig, ExitConfig,
)
from research.analytics.metrics import compute_metrics, print_metrics


def load_daily_context() -> pd.DataFrame:
    """Load and merge daily data for regime classification."""
    print("Loading daily OHLCV...", flush=True)

    # Build daily from 1h
    ohlcv_1h = load_parquet(OHLCV_DIR / "SOLUSDT_1h.parquet")
    ohlcv_1h['dt'] = pd.to_datetime(ohlcv_1h['timestamp'], unit='ms')

    daily = ohlcv_1h.set_index('dt').resample('1D').agg({
        'timestamp': 'first',
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last',
        'volume': 'sum',
        'taker_buy_volume': 'sum', 'taker_sell_volume': 'sum',
    }).dropna().reset_index()

    # Merge liquidation data
    liq = load_parquet(LIQUIDATION_DIR / "SOLUSDT_liq_daily.parquet")
    if not liq.empty:
        liq['dt'] = pd.to_datetime(liq['timestamp'], unit='ms')
        daily = pd.merge_asof(
            daily.sort_values('dt'),
            liq[['dt', 'long_liquidations', 'short_liquidations']].sort_values('dt'),
            on='dt', direction='nearest',
        )
        daily['total_liq'] = daily['long_liquidations'].fillna(0) + daily['short_liquidations'].fillna(0)
    else:
        daily['total_liq'] = 0

    # Merge OI data
    oi = load_parquet(OI_DIR / "SOLUSDT_oi_daily.parquet")
    if not oi.empty:
        oi['dt'] = pd.to_datetime(oi['timestamp'], unit='ms')
        daily = pd.merge_asof(
            daily.sort_values('dt'),
            oi[['dt', 'open_interest']].sort_values('dt'),
            on='dt', direction='nearest',
        )

    logger.info(f"Daily context: {len(daily)} bars, "
                f"{daily['dt'].min().date()} → {daily['dt'].max().date()}")
    return daily


def load_5m_data() -> pd.DataFrame:
    """Load 5m OHLCV data."""
    print("Loading 5m OHLCV...", flush=True)
    df = load_parquet(OHLCV_DIR / "SOLUSDT_5m.parquet")
    if df.empty:
        logger.error("No 5m data found! Run: python -c \"from research.data.collectors.binance_ohlcv import collect_ohlcv; collect_ohlcv('SOLUSDT', '5m')\"")
        return df
    df['dt'] = pd.to_datetime(df['timestamp'], unit='ms')
    logger.info(f"5m data: {len(df)} bars, {df['dt'].min()} → {df['dt'].max()}")
    return df


def run_backtest(
    df_5m: pd.DataFrame,
    df_daily: pd.DataFrame,
    exec_config: ExecutionConfig,
    regime_config: RegimeConfig | None = None,
    initial_capital: float = 10_000.0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run the multi-TF backtest."""

    # 1. Classify regimes on daily
    print("Classifying regimes...", flush=True)
    classifier = RegimeClassifier(regime_config or RegimeConfig())
    df_daily = classifier.classify(df_daily)

    # 2. Prepare 5m with triggers + regime context
    print("Preparing 5m triggers + regime merge...", flush=True)
    signal = MultiTFExecutionSignal(exec_config)
    df_5m = signal.prepare(df_5m, df_daily)

    # 3. Run engine on 5m
    print("Running backtest on 5m bars...", flush=True)
    cost_model = CostModel()
    engine = BacktestEngine(
        cost_model=cost_model,
        initial_capital=initial_capital,
    )
    engine.run(df_5m, signal.evaluate, context={'capital': initial_capital})

    trades = engine.get_trades()
    equity = engine.get_equity_curve()

    return trades, equity


def analyze_results(trades: pd.DataFrame, equity: pd.DataFrame):
    """Full results analysis."""
    if trades.empty:
        print("\n!! NO TRADES GENERATED !!", flush=True)
        print("Check: is 5m data collected? Are regime windows active?", flush=True)
        return

    # Standard metrics
    metrics = compute_metrics(trades, equity)
    print_metrics(metrics, "MULTI-TF REGIME EXECUTION (5m)")

    # R-based analysis
    if 'risk_per_unit' in trades.columns and 'size' in trades.columns:
        stop_risk = trades['risk_per_unit'] * trades['size']
        stop_risk = stop_risk.replace(0, np.nan)
        trades['r'] = trades['pnl_net'] / stop_risk
    else:
        avg_loss = abs(trades[trades['pnl_net'] < 0]['pnl_net'].mean()) if (trades['pnl_net'] < 0).any() else 1
        trades['r'] = trades['pnl_net'] / avg_loss

    r = trades['r'].dropna()
    if len(r) == 0:
        return

    print(f"\n{'='*60}", flush=True)
    print(f"  R-BASED ANALYSIS", flush=True)
    print(f"{'='*60}", flush=True)
    print(f"  Avg R: {r.mean():.2f} | Median: {r.median():.2f}", flush=True)
    print(f"  Avg Winner: {r[r>0].mean():.2f}R | Avg Loser: {r[r<=0].mean():.2f}R", flush=True)
    print(f"  Max R: {r.max():.2f} | Stop-outs: {(r<=-0.9).sum()}", flush=True)
    w = r[r>0].sum(); l_abs = abs(r[r<=0].sum())
    print(f"  PF (R): {w/l_abs:.2f} | Total R: {r.sum():.1f}", flush=True)

    print(f"\n  R Distribution:", flush=True)
    for t in [-1, 0, 0.5, 1, 2, 3, 5]:
        c = (r >= t).sum()
        print(f"    >= {t:+.0f}R: {c} ({c/len(r)*100:.1f}%)", flush=True)

    print(f"\n  Skew: {r.skew():.3f} | Kurt: {r.kurtosis():.3f}", flush=True)

    # By regime
    if 'regime' in trades.columns:
        print(f"\n  BY REGIME:", flush=True)
        for regime, grp in trades.groupby('regime'):
            rr = grp['r'].dropna()
            if len(rr) == 0:
                continue
            wr = (rr > 0).mean() * 100
            print(f"    {regime}: {len(grp)}t WR={wr:.0f}% Avg={rr.mean():.2f}R "
                  f"Sum={rr.sum():.1f}R", flush=True)

    # By trigger
    if 'triggers' in trades.columns:
        print(f"\n  BY TRIGGER COMBINATION:", flush=True)
        trigger_str = trades['triggers'].apply(lambda x: '+'.join(sorted(x)) if isinstance(x, list) else str(x))
        for combo, grp in trades.groupby(trigger_str):
            rr = grp['r'].dropna()
            if len(rr) >= 3:
                print(f"    {combo}: {len(grp)}t Avg={rr.mean():.2f}R", flush=True)

    # By exit reason
    if 'exit_reason' in trades.columns:
        print(f"\n  BY EXIT TYPE:", flush=True)
        for reason, grp in trades.groupby('exit_reason'):
            rr = grp['r'].dropna()
            print(f"    {reason}: {len(grp)}t Avg={rr.mean():.2f}R", flush=True)

    # Recent performance
    if 'entry_time' in trades.columns:
        trades['entry_dt'] = pd.to_datetime(trades['entry_time'], unit='ms')
        now = pd.Timestamp.now()
        for days, label in [(30, "30D"), (60, "60D"), (90, "90D")]:
            recent = trades[trades['entry_dt'] >= now - pd.Timedelta(days=days)]
            if len(recent) > 0:
                rr = recent['r'].dropna()
                print(f"\n  LAST {label}: {len(recent)}t WR={((rr>0).mean()*100):.0f}% "
                      f"Avg={rr.mean():.2f}R Sum={rr.sum():.1f}R", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Multi-TF Regime Backtest")
    parser.add_argument("--min-triggers", type=int, default=2, help="Min simultaneous triggers")
    parser.add_argument("--hold", type=int, default=288, help="Max hold bars (5m)")
    parser.add_argument("--stop-atr", type=float, default=2.0, help="Initial stop ATR mult")
    parser.add_argument("--partial-r", type=float, default=2.0, help="Take partial at N R")
    parser.add_argument("--risk", type=float, default=0.01, help="Risk fraction")
    parser.add_argument("--capital", type=float, default=10000, help="Initial capital")
    args = parser.parse_args()

    exec_config = ExecutionConfig(
        entry=EntryConfig(min_triggers=args.min_triggers),
        exit=ExitConfig(
            max_hold_bars=args.hold,
            initial_stop_atr=args.stop_atr,
            partial_r=args.partial_r,
        ),
        risk_fraction=args.risk,
    )

    print(f"\n{'='*60}", flush=True)
    print(f"  MULTI-TF REGIME BACKTEST", flush=True)
    print(f"  Min triggers: {args.min_triggers} | Stop: {args.stop_atr}x ATR | "
          f"Hold: {args.hold} bars | Partial: {args.partial_r}R", flush=True)
    print(f"{'='*60}\n", flush=True)

    # Load data
    df_daily = load_daily_context()
    df_5m = load_5m_data()

    if df_5m.empty:
        print("ABORTED: No 5m data available.", flush=True)
        return

    # Run
    trades, equity = run_backtest(df_5m, df_daily, exec_config, initial_capital=args.capital)
    analyze_results(trades, equity)

    # Save
    if not trades.empty:
        out = REPORTS_DIR / "multi_tf_trades.parquet"
        trades.to_parquet(out)
        logger.info(f"Trades saved to {out}")

    print("\nDONE", flush=True)


if __name__ == "__main__":
    main()

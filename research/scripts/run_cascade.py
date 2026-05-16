"""
Liquidation Cascade Signal — Backtest Runner.

Wires together:
1. Data loading (OHLCV 1h → daily, liquidation daily, OI daily)
2. Signal preparation (cascade detection)
3. Backtest engine execution
4. Results analysis and reporting

Usage:
    python -u research/scripts/run_cascade.py
    python -u research/scripts/run_cascade.py --hold 3 --atr-mult 1.5
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
from research.engine.signals_cascade import LiquidationCascadeSignal, CascadeConfig
from research.analytics.metrics import compute_metrics, print_metrics


def load_and_merge_daily() -> pd.DataFrame:
    """Load OHLCV, liquidation, and OI data. Merge into daily bars."""
    print("Loading OHLCV...", flush=True)
    ohlcv = load_parquet(OHLCV_DIR / "SOLUSDT_1h.parquet")
    ohlcv['dt'] = pd.to_datetime(ohlcv['timestamp'], unit='ms')
    logger.info(f"OHLCV 1h: {len(ohlcv)} rows")

    # Resample to daily
    daily = ohlcv.set_index('dt').resample('1D').agg({
        'timestamp': 'first',
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum',
        'taker_buy_volume': 'sum',
        'taker_sell_volume': 'sum',
    }).dropna().reset_index()
    logger.info(f"Daily bars: {len(daily)}")

    # Merge liquidation data
    print("Loading liquidations...", flush=True)
    liq = load_parquet(LIQUIDATION_DIR / "SOLUSDT_liq_daily.parquet")
    liq['dt'] = pd.to_datetime(liq['timestamp'], unit='ms')
    logger.info(f"Liquidation daily: {len(liq)} rows")

    daily = pd.merge_asof(
        daily.sort_values('dt'),
        liq[['dt', 'long_liquidations', 'short_liquidations']].sort_values('dt'),
        on='dt', direction='nearest'
    )

    # Merge OI data
    print("Loading OI...", flush=True)
    oi = load_parquet(OI_DIR / "SOLUSDT_oi_daily.parquet")
    if not oi.empty:
        oi['dt'] = pd.to_datetime(oi['timestamp'], unit='ms')
        daily = pd.merge_asof(
            daily.sort_values('dt'),
            oi[['dt', 'open_interest']].sort_values('dt'),
            on='dt', direction='nearest'
        )
        logger.info(f"OI merged: {len(daily)} rows")

    # Compute total liquidation
    daily['total_liq'] = daily['long_liquidations'].fillna(0) + daily['short_liquidations'].fillna(0)

    logger.info(f"Final daily dataset: {len(daily)} rows, "
                f"{daily['dt'].min().date()} → {daily['dt'].max().date()}")

    return daily


def run_backtest(
    daily: pd.DataFrame,
    config: CascadeConfig,
    initial_capital: float = 10_000.0,
) -> tuple:
    """Run the cascade backtest and return (trades_df, equity_df, metrics)."""

    # Initialize signal
    signal = LiquidationCascadeSignal(config)
    daily = signal.prepare(daily)

    # Initialize engine
    cost_model = CostModel()  # Default: 4bps taker + 2bps slippage
    engine = BacktestEngine(
        cost_model=cost_model,
        initial_capital=initial_capital,
    )

    # Run
    print("Running backtest...", flush=True)
    engine.run(daily, signal.evaluate, context={'capital': initial_capital})

    trades = engine.get_trades()
    equity = engine.get_equity_curve()

    return trades, equity


def analyze_results(trades: pd.DataFrame, equity: pd.DataFrame, daily: pd.DataFrame):
    """Comprehensive results analysis."""

    if trades.empty:
        print("\n!! NO TRADES GENERATED !!", flush=True)
        return

    # Basic metrics
    metrics = compute_metrics(trades, equity)
    print_metrics(metrics, "LIQUIDATION CASCADE → LONG")

    # ── R-based analysis ──
    print("\n" + "="*60, flush=True)
    print("  R-BASED ANALYSIS", flush=True)
    print("="*60, flush=True)

    if 'risk_per_unit' in trades.columns:
        trades['r_multiple'] = trades['pnl_gross'] / (trades['risk_per_unit'] * trades['size'])
    else:
        # Approximate R from entry/exit/stop
        trades['r_multiple'] = trades['pnl_net'] / (trades['cost'] + abs(trades['pnl_net'])) if 'cost' in trades.columns else 0

    r = trades['pnl_net'] / trades['pnl_net'].abs().mean() if trades['pnl_net'].abs().mean() > 0 else trades['pnl_net']

    # Convert to proper R using the stop-based risk
    if 'atr_at_entry' in trades.columns:
        stop_risk = trades['atr_at_entry'] * 2 * trades['size']  # 2x ATR * size
        stop_risk = stop_risk.replace(0, np.nan)
        trades['r'] = trades['pnl_net'] / stop_risk
    else:
        # Fallback: use avg trade as 1R
        avg_loss = abs(trades[trades['pnl_net'] < 0]['pnl_net'].mean()) if (trades['pnl_net'] < 0).any() else 1
        trades['r'] = trades['pnl_net'] / avg_loss

    r = trades['r']

    print(f"\n  Avg R: {r.mean():.2f}", flush=True)
    print(f"  Median R: {r.median():.2f}", flush=True)
    print(f"  Avg Winner R: {r[r>0].mean():.2f}" if (r>0).any() else "  No winners", flush=True)
    print(f"  Avg Loser R: {r[r<=0].mean():.2f}" if (r<=0).any() else "  No losers", flush=True)
    print(f"  Max R: {r.max():.2f}", flush=True)

    print(f"\n  R Distribution:", flush=True)
    for thresh in [-1, 0, 0.5, 1, 2, 3, 5]:
        count = (r >= thresh).sum()
        print(f"    >= {thresh:+.0f}R: {count} ({count/len(r)*100:.1f}%)", flush=True)

    # Fat tail check
    print(f"\n  Skew: {r.skew():.3f}", flush=True)
    print(f"  Kurtosis: {r.kurtosis():.3f}", flush=True)

    if len(r) >= 10:
        top10 = r.nlargest(10)
        total_r = r.sum()
        if total_r > 0:
            print(f"  Top 10 contribute: {top10.sum():.1f}R / {total_r:.1f}R ({top10.sum()/total_r*100:.0f}%)", flush=True)

    # ── Recent performance ──
    if 'entry_time' in trades.columns:
        trades['entry_dt'] = pd.to_datetime(trades['entry_time'], unit='ms')
        now = pd.Timestamp.now()

        for days_back, label in [(60, "LAST 60 DAYS"), (90, "LAST 90 DAYS")]:
            cutoff = now - pd.Timedelta(days=days_back)
            recent = trades[trades['entry_dt'] >= cutoff]

            print(f"\n  {label}:", flush=True)
            print(f"  Events: {len(recent)}", flush=True)
            if len(recent) > 0:
                rr = recent['r']
                print(f"  WR: {(rr>0).mean()*100:.1f}% | Avg R: {rr.mean():.2f} | Sum R: {rr.sum():.1f}", flush=True)
                for _, t in recent.iterrows():
                    date_str = t['entry_dt'].strftime('%Y-%m-%d')
                    pnl_pct = t['pnl_net'] / (t['entry_price'] * t['size']) * 100 if t['size'] > 0 else 0
                    print(f"    {date_str}: {t['r']:+.2f}R ({pnl_pct:+.2f}%) "
                          f"entry={t['entry_price']:.2f} exit={t['exit_price']:.2f} "
                          f"reason={t['exit_reason']}", flush=True)

    # ── By year ──
    if 'entry_dt' in trades.columns:
        trades['year'] = trades['entry_dt'].dt.year
        print(f"\n  BY YEAR:", flush=True)
        for yr, grp in trades.groupby('year'):
            rr = grp['r']
            w = rr[rr>0].sum()
            l_abs = abs(rr[rr<=0].sum())
            pf = w / l_abs if l_abs > 0 else float('inf')
            print(f"    {yr}: {len(grp)}t WR={((rr>0).mean()*100):.0f}% "
                  f"Avg={rr.mean():.2f}R Sum={rr.sum():.1f}R PF={pf:.2f}", flush=True)

    # ── Exit reason breakdown ──
    if 'exit_reason' in trades.columns:
        print(f"\n  EXIT REASONS:", flush=True)
        for reason, count in trades['exit_reason'].value_counts().items():
            subset = trades[trades['exit_reason'] == reason]
            print(f"    {reason}: {count} trades, avg R={subset['r'].mean():.2f}", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Run Liquidation Cascade Backtest")
    parser.add_argument("--hold", type=int, default=5, help="Max hold days")
    parser.add_argument("--atr-mult", type=float, default=2.0, help="ATR stop multiplier")
    parser.add_argument("--percentile", type=float, default=0.90, help="Liquidation threshold percentile")
    parser.add_argument("--risk", type=float, default=0.01, help="Risk fraction per trade")
    parser.add_argument("--trail", action="store_true", default=True, help="Enable trailing stop")
    parser.add_argument("--no-trail", action="store_true", help="Disable trailing stop")
    parser.add_argument("--capital", type=float, default=10000, help="Initial capital")
    args = parser.parse_args()

    config = CascadeConfig(
        hold_bars=args.hold,
        atr_stop_mult=args.atr_mult,
        percentile=args.percentile,
        risk_fraction=args.risk,
        use_trailing=not args.no_trail,
    )

    print(f"\n{'='*60}", flush=True)
    print(f"  LIQUIDATION CASCADE BACKTEST", flush=True)
    print(f"  Stop: {config.atr_stop_mult}x ATR | Hold: {config.hold_bars}d | "
          f"P{config.percentile*100:.0f} threshold | Trail: {config.use_trailing}", flush=True)
    print(f"{'='*60}\n", flush=True)

    # Load data
    daily = load_and_merge_daily()

    # Run backtest
    trades, equity = run_backtest(daily, config, args.capital)

    # Analyze
    analyze_results(trades, equity, daily)

    # Save trades
    if not trades.empty:
        out_path = REPORTS_DIR / "cascade_trades.parquet"
        trades.to_parquet(out_path)
        logger.info(f"Trades saved to {out_path}")

    print("\nDONE", flush=True)


if __name__ == "__main__":
    main()

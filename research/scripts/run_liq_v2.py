"""
V2 Liq-Cluster Backtest Runner + Deep-Dive Analysis.

Runs the high-selectivity signal and performs winner vs loser analysis.

Usage:
    python -u research/scripts/run_liq_v2.py
    python -u research/scripts/run_liq_v2.py --imb-z 3.0 --impulse 0.5
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

print("Loading...", flush=True)

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
from research.engine.signals_liq_v2 import (
    LiqClusterExpansionSignal, LiqClusterConfig, classify_cascade_context,
)


def load_daily():
    print("Loading daily...", flush=True)
    ohlcv_1h = load_parquet(OHLCV_DIR / "SOLUSDT_1h.parquet")
    ohlcv_1h['dt'] = pd.to_datetime(ohlcv_1h['timestamp'], unit='ms')
    daily = ohlcv_1h.set_index('dt').resample('1D').agg({
        'timestamp': 'first', 'open': 'first', 'high': 'max',
        'low': 'min', 'close': 'last', 'volume': 'sum',
    }).dropna().reset_index()

    liq = load_parquet(LIQUIDATION_DIR / "SOLUSDT_liq_daily.parquet")
    if not liq.empty:
        liq['dt'] = pd.to_datetime(liq['timestamp'], unit='ms')
        daily = pd.merge_asof(daily.sort_values('dt'),
            liq[['dt', 'long_liquidations', 'short_liquidations']].sort_values('dt'),
            on='dt', direction='nearest')
        daily['total_liq'] = daily['long_liquidations'].fillna(0) + daily['short_liquidations'].fillna(0)

    logger.info(f"Daily: {len(daily)} bars, {daily['dt'].min().date()} → {daily['dt'].max().date()}")
    return daily


def load_5m():
    print("Loading 5m...", flush=True)
    df = load_parquet(OHLCV_DIR / "SOLUSDT_5m.parquet")
    if df.empty:
        logger.error("No 5m data!")
        return df
    df['dt'] = pd.to_datetime(df['timestamp'], unit='ms')
    logger.info(f"5m: {len(df)} bars, {df['dt'].min()} → {df['dt'].max()}")
    return df


def run(df_5m, df_daily, cfg, capital=10_000):
    # Context
    df_daily = classify_cascade_context(df_daily, cfg)

    # Signal
    signal = LiqClusterExpansionSignal(cfg)
    df_5m = signal.prepare(df_5m, df_daily)

    # Engine
    print("Running backtest...", flush=True)
    engine = BacktestEngine(cost_model=CostModel(), initial_capital=capital)
    engine.run(df_5m, signal.evaluate, context={'capital': capital})

    return engine.get_trades(), engine.get_equity_curve()


def analyze(trades, equity):
    if trades.empty:
        print("\n!! NO TRADES !!", flush=True)
        print("The entry filters are too strict or no cascade windows occurred.", flush=True)
        print("Consider relaxing: imb_z, vol_z, impulse_min_pct, body_strength", flush=True)
        return

    n = len(trades)
    days = (trades['exit_time'].max() - trades['entry_time'].min()) / 86400000 if n > 1 else 1
    trades_per_week = n / (days / 7) if days > 0 else 0

    # R calc
    if 'risk_per_unit' in trades.columns and 'size' in trades.columns:
        stop_risk = (trades['risk_per_unit'] * trades['size']).replace(0, np.nan)
        trades['r'] = trades['pnl_net'] / stop_risk
    else:
        avg_loss = abs(trades[trades['pnl_net'] < 0]['pnl_net'].mean()) if (trades['pnl_net'] < 0).any() else 1
        trades['r'] = trades['pnl_net'] / avg_loss

    r = trades['r'].dropna()
    winners = r[r > 0]
    losers = r[r <= 0]

    print(f"\n{'='*60}", flush=True)
    print(f"  LIQ-CLUSTER V2 RESULTS", flush=True)
    print(f"{'='*60}", flush=True)
    print(f"  Trades: {n} | Span: {days:.0f}d | {trades_per_week:.1f}/week", flush=True)
    print(f"  WR: {(r>0).mean()*100:.1f}%", flush=True)
    print(f"  Avg R: {r.mean():.2f} | Median: {r.median():.2f}", flush=True)
    print(f"  Avg Win: {winners.mean():.2f}R | Avg Loss: {losers.mean():.2f}R", flush=True)
    print(f"  Max R: {r.max():.2f} | Total R: {r.sum():.1f}R", flush=True)
    w, l = winners.sum(), abs(losers.sum())
    print(f"  PF: {w/l:.2f}" if l > 0 else "  PF: inf", flush=True)
    print(f"  Stop-outs: {trades.get('exit_reason', pd.Series()).eq('stop_loss').sum()}", flush=True)

    print(f"\n  R Distribution:", flush=True)
    for t in [-1, 0, 1, 2, 3, 5]:
        c = (r >= t).sum()
        print(f"    >= {t:+.0f}R: {c} ({c/n*100:.1f}%)", flush=True)

    print(f"\n  Skew: {r.skew():.3f} | Kurt: {r.kurtosis():.3f}", flush=True)

    # Exit breakdown
    if 'exit_reason' in trades.columns:
        print(f"\n  EXIT BREAKDOWN:", flush=True)
        for reason, grp in trades.groupby('exit_reason'):
            rr = grp['r'].dropna()
            print(f"    {reason}: {len(grp)}t | WR {(rr>0).mean()*100:.0f}% | "
                  f"Avg {rr.mean():.2f}R", flush=True)

    # ── DEEP DIVE: Winners vs Losers ──
    print(f"\n{'='*60}", flush=True)
    print(f"  WINNER vs LOSER DEEP DIVE", flush=True)
    print(f"{'='*60}", flush=True)

    big_winners = trades[trades['r'] >= 2]
    stopouts = trades[trades.get('exit_reason', '') == 'stop_loss'] if 'exit_reason' in trades.columns else trades[trades['r'] <= -0.9]

    print(f"\n  Big winners (>=2R): {len(big_winners)}", flush=True)
    print(f"  Stop-outs: {len(stopouts)}", flush=True)

    # Compare feature distributions
    feature_cols = ['imb_z', 'vol_z', 'body_strength', 'bar_return_pct',
                    'cascade_strength', 'atr']
    available = [c for c in feature_cols if c in trades.columns]

    if available and len(big_winners) > 0 and len(stopouts) > 0:
        print(f"\n  Feature comparison (at entry):", flush=True)
        print(f"  {'Feature':<20} {'Winners':>10} {'Stopouts':>10} {'Delta':>10}", flush=True)
        print(f"  {'─'*50}", flush=True)
        for col in available:
            w_mean = big_winners[col].mean()
            l_mean = stopouts[col].mean()
            delta = w_mean - l_mean
            print(f"  {col:<20} {w_mean:>10.3f} {l_mean:>10.3f} {delta:>+10.3f}", flush=True)

    # Recent trades
    if 'entry_time' in trades.columns:
        trades['entry_dt'] = pd.to_datetime(trades['entry_time'], unit='ms')
        print(f"\n  TRADE LOG:", flush=True)
        for _, t in trades.iterrows():
            date = t['entry_dt'].strftime('%Y-%m-%d %H:%M')
            reason = t.get('exit_reason', '?')
            print(f"    {date} | {t['r']:+.2f}R | exit={reason} | "
                  f"imb_z={t.get('imb_z', 0):.1f} vol_z={t.get('vol_z', 0):.1f} "
                  f"body={t.get('body_strength', 0):.2f}", flush=True)

    # Save
    out = REPORTS_DIR / "liq_v2_trades.parquet"
    trades.to_parquet(out)
    logger.info(f"Saved to {out}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--imb-z", type=float, default=2.0)
    parser.add_argument("--vol-z", type=float, default=3.0)
    parser.add_argument("--impulse", type=float, default=0.30)
    parser.add_argument("--body", type=float, default=0.60)
    parser.add_argument("--stop-atr", type=float, default=2.5)
    parser.add_argument("--cooldown", type=int, default=36)
    parser.add_argument("--capital", type=float, default=10000)
    parser.add_argument("--min-confirms", type=int, default=4)
    parser.add_argument("--no-squeeze", action="store_true", help="Disable short-squeeze filter")
    args = parser.parse_args()

    cfg = LiqClusterConfig(
        imb_z_threshold=args.imb_z,
        vol_z_threshold=args.vol_z,
        impulse_min_pct=args.impulse,
        body_strength_min=args.body,
        initial_stop_atr=args.stop_atr,
        cooldown_bars=args.cooldown,
        min_confirmations=args.min_confirms,
        require_short_squeeze=not args.no_squeeze,
    )

    squeeze_label = "ON" if cfg.require_short_squeeze else "OFF"
    print(f"\n{'='*60}", flush=True)
    print(f"  LIQ-CLUSTER V3 BACKTEST (forensic-refined)", flush=True)
    print(f"  {cfg.min_confirmations}-of-6 confirms | squeeze={squeeze_label}", flush=True)
    print(f"  vol_z>{cfg.vol_z_threshold} | Stop: {cfg.initial_stop_atr}x ATR", flush=True)
    print(f"{'='*60}\n", flush=True)

    daily = load_daily()
    df_5m = load_5m()
    if df_5m.empty:
        return

    trades, equity = run(df_5m, daily, cfg, args.capital)
    analyze(trades, equity)
    print("\nDONE", flush=True)


if __name__ == "__main__":
    main()

"""
Cascade Signal — Parameter Sweep.

Sweeps across key parameters to find profitable variants:
- hold_bars: how long to hold (letting winners run)
- atr_stop_mult: stop width (survival vs. R)
- percentile: event quality filter
- trail_atr_mult: trailing stop width
- trail_activation_r: when to activate trailing
- min_cascade_ratio: cascade strength filter
- require_oi_drop: genuine liquidation filter

Usage:
    python -u research/scripts/sweep_cascade.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

print("Loading modules...", flush=True)

import itertools
import pandas as pd
import numpy as np
from loguru import logger

logger.remove()
logger.add(sys.stderr, level="WARNING", format="{time:HH:mm:ss} | {level:<7} | {message}")

from research.config.settings import OHLCV_DIR, LIQUIDATION_DIR, OI_DIR, REPORTS_DIR
from research.data.storage.parquet_store import load_parquet
from research.engine.backtest import BacktestEngine
from research.engine.costs import CostModel
from research.engine.signals_cascade import LiquidationCascadeSignal, CascadeConfig
from research.analytics.metrics import compute_metrics


def load_daily() -> pd.DataFrame:
    """Load and merge daily data (cached for sweep)."""
    print("Loading data...", flush=True)
    ohlcv = load_parquet(OHLCV_DIR / "SOLUSDT_1h.parquet")
    ohlcv['dt'] = pd.to_datetime(ohlcv['timestamp'], unit='ms')

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

    liq = load_parquet(LIQUIDATION_DIR / "SOLUSDT_liq_daily.parquet")
    liq['dt'] = pd.to_datetime(liq['timestamp'], unit='ms')
    daily = pd.merge_asof(
        daily.sort_values('dt'),
        liq[['dt', 'long_liquidations', 'short_liquidations']].sort_values('dt'),
        on='dt', direction='nearest'
    )

    oi = load_parquet(OI_DIR / "SOLUSDT_oi_daily.parquet")
    if not oi.empty:
        oi['dt'] = pd.to_datetime(oi['timestamp'], unit='ms')
        daily = pd.merge_asof(
            daily.sort_values('dt'),
            oi[['dt', 'open_interest']].sort_values('dt'),
            on='dt', direction='nearest'
        )

    daily['total_liq'] = daily['long_liquidations'].fillna(0) + daily['short_liquidations'].fillna(0)
    print(f"Data ready: {len(daily)} daily bars, {daily['dt'].min().date()} → {daily['dt'].max().date()}", flush=True)
    return daily


def run_single(daily_raw: pd.DataFrame, config: CascadeConfig, capital: float = 10_000) -> dict:
    """Run one backtest variant. Returns metrics dict or None on failure."""
    try:
        daily = daily_raw.copy()
        signal = LiquidationCascadeSignal(config)
        daily = signal.prepare(daily)

        engine = BacktestEngine(
            cost_model=CostModel(),
            initial_capital=capital,
        )
        engine.run(daily, signal.evaluate, context={'capital': capital})

        trades = engine.get_trades()
        equity = engine.get_equity_curve()

        if trades.empty or len(trades) < 5:
            return None

        metrics = compute_metrics(trades, equity)

        # Compute R stats
        if 'atr_at_entry' in trades.columns:
            stop_risk = trades['atr_at_entry'] * config.atr_stop_mult * trades['size']
            stop_risk = stop_risk.replace(0, np.nan)
            trades['r'] = trades['pnl_net'] / stop_risk
        else:
            avg_loss = abs(trades[trades['pnl_net'] < 0]['pnl_net'].mean()) if (trades['pnl_net'] < 0).any() else 1
            trades['r'] = trades['pnl_net'] / avg_loss

        r = trades['r']

        # Recent performance (last 180 days)
        if 'entry_time' in trades.columns:
            trades['entry_dt'] = pd.to_datetime(trades['entry_time'], unit='ms')
            cutoff_180 = pd.Timestamp.now() - pd.Timedelta(days=180)
            recent = trades[trades['entry_dt'] >= cutoff_180]
            recent_r_sum = recent['r'].sum() if len(recent) > 0 else 0
            recent_count = len(recent)
        else:
            recent_r_sum = 0
            recent_count = 0

        # Exit breakdown
        exit_counts = trades['exit_reason'].value_counts().to_dict() if 'exit_reason' in trades.columns else {}

        return {
            'hold': config.hold_bars,
            'atr_mult': config.atr_stop_mult,
            'pctl': config.percentile,
            'trail_mult': config.trail_atr_mult,
            'trail_act': config.trail_activation_r,
            'min_ratio': config.min_cascade_ratio,
            'oi_drop': config.require_oi_drop,
            'trades': len(trades),
            'wr': (r > 0).mean(),
            'pf_net': metrics.get('pf_net', 0),
            'avg_r': r.mean(),
            'median_r': r.median(),
            'sum_r': r.sum(),
            'max_r': r.max(),
            'pct_ge_2r': (r >= 2).mean(),
            'pct_ge_3r': (r >= 3).mean(),
            'skew': r.skew(),
            'max_dd_pct': metrics.get('max_dd_pct', 0),
            'cagr': metrics.get('cagr', 0),
            'expect_bps': metrics.get('expectancy_bps_net', 0),
            'recent_180d_r': recent_r_sum,
            'recent_180d_n': recent_count,
            'time_exits': exit_counts.get('time_exit_5d', exit_counts.get(f'time_exit_{config.hold_bars}d', 0)),
            'stop_exits': exit_counts.get('stop_loss', 0),
            'trail_exits': exit_counts.get('trailing_stop', 0),
        }
    except Exception as e:
        return None


def main():
    daily = load_daily()

    # ── Sweep grid ──
    # Focus on the parameters that address the two key problems:
    # 1) No fat tail capture → longer hold, wider trail, later trail activation
    # 2) Threshold quality → higher percentile, min cascade ratio

    grid = {
        'hold_bars':          [3, 5, 7, 10, 15],
        'atr_stop_mult':      [1.5, 2.0, 2.5, 3.0],
        'percentile':         [0.85, 0.90, 0.95],
        'trail_atr_mult':     [2.0, 3.0, 4.0],
        'trail_activation_r': [0.5, 1.0, 2.0],
        'min_cascade_ratio':  [1.0, 1.5],
        'require_oi_drop':    [False],
    }

    combos = list(itertools.product(*grid.values()))
    total = len(combos)
    print(f"\nSweeping {total} parameter combinations...\n", flush=True)

    results = []
    for i, combo in enumerate(combos):
        params = dict(zip(grid.keys(), combo))

        config = CascadeConfig(
            hold_bars=params['hold_bars'],
            atr_stop_mult=params['atr_stop_mult'],
            percentile=params['percentile'],
            trail_atr_mult=params['trail_atr_mult'],
            trail_activation_r=params['trail_activation_r'],
            min_cascade_ratio=params['min_cascade_ratio'],
            require_oi_drop=params['require_oi_drop'],
        )

        result = run_single(daily, config)
        if result is not None:
            results.append(result)

        if (i + 1) % 50 == 0 or (i + 1) == total:
            valid = len(results)
            print(f"  [{i+1}/{total}] {valid} valid variants so far", flush=True)

    if not results:
        print("\n!! NO VALID VARIANTS FOUND !!", flush=True)
        return

    df = pd.DataFrame(results)

    # ── Rank by composite score ──
    # We want: high PF, high avg R, trades > 15, positive recent performance, some fat tail
    df['score'] = (
        df['avg_r'] * 30 +           # R is king
        df['pf_net'] * 10 +          # Profitability
        df['pct_ge_2r'] * 50 +       # Fat tail bonus
        df['recent_180d_r'] * 5 +    # Recency matters
        df['skew'] * 5 -             # Positive skew preferred
        (df['trades'] < 15) * 100    # Penalty for too few trades
    )

    df = df.sort_values('score', ascending=False)

    # ── Print top 20 ──
    print(f"\n{'='*120}", flush=True)
    print(f"  TOP 20 CASCADE VARIANTS (of {len(df)} valid)", flush=True)
    print(f"{'='*120}", flush=True)

    cols = ['hold', 'atr_mult', 'pctl', 'trail_mult', 'trail_act', 'min_ratio',
            'trades', 'wr', 'pf_net', 'avg_r', 'sum_r', 'max_r',
            'pct_ge_2r', 'skew', 'max_dd_pct', 'expect_bps',
            'recent_180d_r', 'recent_180d_n', 'trail_exits', 'score']

    top = df.head(20)
    for rank, (_, row) in enumerate(top.iterrows(), 1):
        print(f"\n  #{rank}", flush=True)
        print(f"    Config: hold={row['hold']}d ATR={row['atr_mult']}x P{row['pctl']*100:.0f} "
              f"trail={row['trail_mult']}x@{row['trail_act']}R ratio≥{row['min_ratio']}", flush=True)
        print(f"    Trades={row['trades']:.0f} WR={row['wr']*100:.1f}% PF={row['pf_net']:.2f} "
              f"AvgR={row['avg_r']:.3f} SumR={row['sum_r']:.1f} MaxR={row['max_r']:.2f}", flush=True)
        print(f"    ≥2R={row['pct_ge_2r']*100:.1f}% Skew={row['skew']:.2f} "
              f"DD={row['max_dd_pct']:.1f}% Exp={row['expect_bps']:.1f}bps", flush=True)
        print(f"    Recent180d: {row['recent_180d_n']:.0f}t sumR={row['recent_180d_r']:.1f} "
              f"TrailExits={row['trail_exits']:.0f} Score={row['score']:.1f}", flush=True)

    # ── Save full results ──
    out = REPORTS_DIR / "cascade_sweep.parquet"
    df.to_parquet(out)
    print(f"\nFull results saved to {out}", flush=True)

    # ── Also save as CSV for easy reading ──
    csv_out = REPORTS_DIR / "cascade_sweep_top50.csv"
    df.head(50).to_csv(csv_out, index=False, float_format='%.4f')
    print(f"Top 50 CSV saved to {csv_out}", flush=True)

    print("\nDONE", flush=True)


if __name__ == "__main__":
    main()

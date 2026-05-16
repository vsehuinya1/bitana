"""
Drawdown filter analysis — tests various daily context filters
to reduce the Nov-Dec 2025 drawdown while preserving winners.
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

from research.config.settings import OHLCV_DIR, LIQUIDATION_DIR
from research.data.storage.parquet_store import load_parquet
from research.engine.backtest import BacktestEngine
from research.engine.costs import CostModel
from research.engine.signals_liq_v2 import (
    LiqClusterExpansionSignal, LiqClusterConfig, classify_cascade_context,
)


def main():
    # Load data
    df_5m = load_parquet(OHLCV_DIR / "SOLUSDT_5m.parquet")
    df_5m['dt'] = pd.to_datetime(df_5m['timestamp'], unit='ms')
    print(f"5m: {len(df_5m)} bars", flush=True)

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

    # Add daily context filters
    daily['ret_5d'] = daily['close'].pct_change(5) * 100
    daily['daily_atr'] = (daily['high'] - daily['low']).rolling(14).mean()
    daily['daily_atr_pctl'] = daily['daily_atr'].rolling(90, min_periods=20).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1] * 100, raw=False)
    daily['ema20d'] = daily['close'].ewm(span=20).mean()
    daily['ema50d'] = daily['close'].ewm(span=50).mean()
    daily['daily_trend'] = daily['ema20d'] > daily['ema50d']

    # Classify cascade context
    cfg = LiqClusterConfig(
        min_confirmations=4, require_short_squeeze=True,
        vol_z_threshold=3.0, initial_stop_atr=2.5,
    )
    daily_c = classify_cascade_context(daily.copy(), cfg)

    # Prepare 5m features
    sig_prep = LiqClusterExpansionSignal(cfg)
    df_5m_p = sig_prep.prepare(df_5m.copy(), daily_c)

    # Merge daily filter features into 5m
    df_5m_p = pd.merge_asof(
        df_5m_p.sort_values('dt'),
        daily_c[['dt', 'ret_5d', 'daily_atr_pctl', 'daily_trend']].sort_values('dt'),
        on='dt', direction='backward',
    )

    # Windows
    windows = [
        ("May-Jun 25", "2025-05-01", "2025-07-01", "OOS"),
        ("Jul-Aug 25", "2025-07-01", "2025-09-01", "OOS"),
        ("Sep-Oct 25", "2025-09-01", "2025-11-01", "OOS"),
        ("Nov-Dec 25", "2025-11-01", "2026-01-01", "IS"),
        ("Jan-Feb 26", "2026-01-01", "2026-03-01", "IS"),
        ("Mar-Apr 26", "2026-03-01", "2026-05-01", "IS"),
        ("May 26",     "2026-05-01", "2026-06-01", "IS"),
    ]

    # Filters to test
    filters = {
        "No filter": None,
        "ret5d > -5%": lambda df: df[df['ret_5d'] > -5],
        "ret5d > -3%": lambda df: df[df['ret_5d'] > -3],
        "atr_pctl > 20": lambda df: df[df['daily_atr_pctl'] > 20],
        "ret5d>-5 + atr>20": lambda df: df[(df['ret_5d'] > -5) & (df['daily_atr_pctl'] > 20)],
        "ret5d>-3 + atr>25": lambda df: df[(df['ret_5d'] > -3) & (df['daily_atr_pctl'] > 25)],
    }

    for filt_name, filt_fn in filters.items():
        if filt_fn is not None:
            df_work = filt_fn(df_5m_p).copy()
        else:
            df_work = df_5m_p.copy()

        oos_r = []
        is_r = []
        per_window = []

        for wlabel, start, end, sample in windows:
            s = pd.Timestamp(start)
            e = pd.Timestamp(end)
            mask = (df_work['dt'] >= s) & (df_work['dt'] < e)
            wdf = df_work[mask].copy()

            if len(wdf) < 100:
                per_window.append((wlabel, 0, 0, 0, 0, 0, sample))
                continue

            signal = LiqClusterExpansionSignal(cfg)
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

        # Summary
        all_r = np.array(oos_r + is_r)
        n_all = len(all_r)
        if n_all > 0:
            ws = all_r[all_r > 0].sum()
            ls = abs(all_r[all_r <= 0].sum())
            pf_all = ws / ls if ls > 0 else 99
            tr_all = all_r.sum()
        else:
            pf_all = 0
            tr_all = 0

        oos_arr = np.array(oos_r)
        if len(oos_arr) > 0:
            oos_ws = oos_arr[oos_arr > 0].sum()
            oos_ls = abs(oos_arr[oos_arr <= 0].sum())
            oos_pf = oos_ws / oos_ls if oos_ls > 0 else 99
        else:
            oos_pf = 0

        print("", flush=True)
        print(f"=== {filt_name} === "
              f"Full: {n_all}t PF={pf_all:.2f} R={tr_all:+.1f} | "
              f"OOS_PF={oos_pf:.2f}", flush=True)
        for wlabel, n, wr, pf, tr, stops, sample in per_window:
            tag = "*" if sample == "OOS" else " "
            if n == 0:
                print(f"  {tag}{wlabel}: 0t", flush=True)
            else:
                print(f"  {tag}{wlabel}: {n}t WR={wr:.0f}% PF={pf:.2f} "
                      f"R={tr:+.1f} Stp={stops}", flush=True)

    print("", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()

"""
SOLUSDT Exploratory Analysis — First Deliverable.

Generates comprehensive analysis covering:
1. Data quality audit
2. OI behavior analysis
3. Funding behavior analysis
4. Taker flow behavior analysis
5. Volatility clustering analysis
6. Session differences analysis
7. Long vs short asymmetry
8. Regime structure identification

Outputs: HTML report + plots to output/reports/
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from loguru import logger

from research.config.settings import (
    OHLCV_DIR, FUNDING_DIR, OI_DIR, LIQUIDATION_DIR,
    REPORTS_DIR, PLOTS_DIR, SESSIONS, TF_TO_MS,
)
from research.data.storage.parquet_store import load_parquet
from research.data.sessions import tag_sessions
from research.data.quality import full_audit, print_audit
from research.features.volatility import add_all_volatility, true_range
from research.features.orderflow import taker_imbalance, delta_persistence, volume_acceleration
from research.features.oi import oi_delta, oi_roc, price_oi_state

logger.remove()
logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level:<7} | {message}")

PLOTS = PLOTS_DIR / "sol_exploration"
PLOTS.mkdir(parents=True, exist_ok=True)

# ─── Plotting config ─────────────────────────────────
plt.rcParams.update({
    'figure.figsize': (14, 6),
    'figure.dpi': 120,
    'font.size': 10,
    'axes.grid': True,
    'grid.alpha': 0.3,
})
COLORS = {
    'bull': '#00c853',
    'bear': '#ff1744',
    'neutral': '#42a5f5',
    'accent': '#ff9100',
    'muted': '#78909c',
}


def load_all_data():
    """Load all available SOLUSDT data."""
    data = {}

    # OHLCV 1h
    ohlcv = load_parquet(OHLCV_DIR / "SOLUSDT_1h.parquet")
    if not ohlcv.empty:
        ohlcv['dt'] = pd.to_datetime(ohlcv['timestamp'], unit='ms')
        data['ohlcv_1h'] = ohlcv
        logger.info(f"OHLCV 1h: {len(ohlcv)} rows, {ohlcv['dt'].min()} → {ohlcv['dt'].max()}")

    # OI daily
    oi_daily = load_parquet(OI_DIR / "SOLUSDT_oi_daily.parquet")
    if not oi_daily.empty:
        oi_daily['dt'] = pd.to_datetime(oi_daily['timestamp'], unit='ms')
        data['oi_daily'] = oi_daily
        logger.info(f"OI daily: {len(oi_daily)} rows, {oi_daily['dt'].min()} → {oi_daily['dt'].max()}")

    # OI hourly
    oi_hourly = load_parquet(OI_DIR / "SOLUSDT_oi_1h.parquet")
    if not oi_hourly.empty:
        oi_hourly['dt'] = pd.to_datetime(oi_hourly['timestamp'], unit='ms')
        data['oi_hourly'] = oi_hourly
        logger.info(f"OI hourly: {len(oi_hourly)} rows")

    # Funding
    funding = load_parquet(FUNDING_DIR / "SOLUSDT_funding.parquet")
    if not funding.empty:
        funding['dt'] = pd.to_datetime(funding['timestamp'], unit='ms')
        data['funding'] = funding
        logger.info(f"Funding: {len(funding)} rows, {funding['dt'].min()} → {funding['dt'].max()}")

    # Liquidations daily
    liq = load_parquet(LIQUIDATION_DIR / "SOLUSDT_liq_daily.parquet")
    if not liq.empty:
        liq['dt'] = pd.to_datetime(liq['timestamp'], unit='ms')
        data['liq_daily'] = liq
        logger.info(f"Liquidations daily: {len(liq)} rows")

    return data


def section_data_audit(data: dict, report: list):
    """Section 1: Data Quality Audit."""
    report.append("\n# 1. DATA QUALITY AUDIT\n")

    for name, df in data.items():
        tf_ms = TF_TO_MS.get('1h', None) if '1h' in name else TF_TO_MS.get('1d', 86400000)
        audit = full_audit(df, name=name, tf_ms=tf_ms)
        print_audit(audit)
        report.append(f"**{name}**: {audit['rows']} rows | {audit.get('ts_min','?')} → {audit.get('ts_max','?')} | Status: {audit['status']}")
        if audit.get('gaps', 0) > 0:
            report.append(f"  Gaps: {audit['gaps']} ({audit.get('total_missing_bars','?')} missing bars)")


def section_oi_behavior(data: dict, report: list):
    """Section 2: OI Behavior Analysis."""
    report.append("\n# 2. OI BEHAVIOR ANALYSIS\n")

    oi = data.get('oi_daily')
    if oi is None or oi.empty:
        report.append("No OI data available.")
        return

    oi = oi.copy().sort_values('dt')

    # Basic stats
    report.append(f"**OI Range**: {oi['open_interest'].min():,.0f} → {oi['open_interest'].max():,.0f}")
    report.append(f"**Current OI**: {oi['open_interest'].iloc[-1]:,.0f}")
    report.append(f"**Mean OI**: {oi['open_interest'].mean():,.0f}")

    # OI change distribution
    oi['oi_chg'] = oi['open_interest'].pct_change() * 100
    report.append(f"\n**Daily OI Change Distribution:**")
    report.append(f"  Mean: {oi['oi_chg'].mean():.2f}%")
    report.append(f"  Std: {oi['oi_chg'].std():.2f}%")
    report.append(f"  Skew: {oi['oi_chg'].skew():.3f}")
    report.append(f"  Kurtosis: {oi['oi_chg'].kurtosis():.3f}")

    # Plot 1: OI over time
    fig, axes = plt.subplots(2, 1, figsize=(14, 10), gridspec_kw={'height_ratios': [2, 1]})
    axes[0].plot(oi['dt'], oi['open_interest'], color=COLORS['neutral'], linewidth=0.8)
    axes[0].set_title('SOLUSDT Daily Open Interest (Close)', fontweight='bold')
    axes[0].set_ylabel('OI (contracts)')
    axes[0].ticklabel_format(axis='y', style='scientific', scilimits=(6,6))

    axes[1].bar(oi['dt'], oi['oi_chg'], color=[COLORS['bull'] if x > 0 else COLORS['bear'] for x in oi['oi_chg'].fillna(0)], width=1, alpha=0.7)
    axes[1].set_title('Daily OI Change (%)', fontweight='bold')
    axes[1].set_ylabel('Change %')
    axes[1].axhline(0, color='white', linewidth=0.5)

    plt.tight_layout()
    plt.savefig(PLOTS / "01_oi_timeseries.png", bbox_inches='tight')
    plt.close()
    report.append(f"\n![OI Timeseries]({PLOTS / '01_oi_timeseries.png'})")

    # OI extremes analysis
    report.append(f"\n**Top 10 largest daily OI increases:**")
    top_up = oi.nlargest(10, 'oi_chg')[['dt', 'open_interest', 'oi_chg']]
    for _, r in top_up.iterrows():
        report.append(f"  {r['dt'].strftime('%Y-%m-%d')}: +{r['oi_chg']:.1f}% (OI: {r['open_interest']:,.0f})")

    report.append(f"\n**Top 10 largest daily OI drops:**")
    top_down = oi.nsmallest(10, 'oi_chg')[['dt', 'open_interest', 'oi_chg']]
    for _, r in top_down.iterrows():
        report.append(f"  {r['dt'].strftime('%Y-%m-%d')}: {r['oi_chg']:.1f}% (OI: {r['open_interest']:,.0f})")


def section_funding_behavior(data: dict, report: list):
    """Section 3: Funding Behavior Analysis."""
    report.append("\n# 3. FUNDING BEHAVIOR ANALYSIS\n")

    f = data.get('funding')
    if f is None or f.empty:
        report.append("No funding data available.")
        return

    f = f.copy().sort_values('dt')
    fr = f['funding_rate']

    report.append(f"**Funding Rate Stats:**")
    report.append(f"  Mean: {fr.mean()*100:.4f}%")
    report.append(f"  Median: {fr.median()*100:.4f}%")
    report.append(f"  Std: {fr.std()*100:.4f}%")
    report.append(f"  Skew: {fr.skew():.3f}")
    report.append(f"  Min: {fr.min()*100:.4f}% | Max: {fr.max()*100:.4f}%")

    # Positive vs negative funding
    pos_pct = (fr > 0).mean() * 100
    neg_pct = (fr < 0).mean() * 100
    report.append(f"\n**Positive funding**: {pos_pct:.1f}% of periods (market net long)")
    report.append(f"**Negative funding**: {neg_pct:.1f}% of periods (market net short)")

    # Extreme funding
    p95 = fr.quantile(0.95)
    p05 = fr.quantile(0.05)
    report.append(f"\n**Extreme thresholds**: P5={p05*100:.4f}% | P95={p95*100:.4f}%")
    extreme_long = (fr > p95).sum()
    extreme_short = (fr < p05).sum()
    report.append(f"**Extreme long periods**: {extreme_long}")
    report.append(f"**Extreme short periods**: {extreme_short}")

    # Plot funding
    fig, axes = plt.subplots(2, 1, figsize=(14, 8))

    axes[0].bar(f['dt'], fr * 100, color=[COLORS['bull'] if x > 0 else COLORS['bear'] for x in fr], width=0.01, alpha=0.6)
    axes[0].axhline(0, color='white', linewidth=0.5)
    axes[0].set_title('SOLUSDT Funding Rate History', fontweight='bold')
    axes[0].set_ylabel('Funding Rate (%)')

    # Rolling mean
    if len(fr) > 21:
        rolling_mean = fr.rolling(21).mean() * 100
        axes[1].plot(f['dt'], rolling_mean, color=COLORS['accent'], linewidth=1)
        axes[1].axhline(0, color='white', linewidth=0.5)
        axes[1].set_title('21-Period Rolling Mean Funding', fontweight='bold')
        axes[1].set_ylabel('Funding Rate (%)')

    plt.tight_layout()
    plt.savefig(PLOTS / "02_funding_history.png", bbox_inches='tight')
    plt.close()
    report.append(f"\n![Funding History]({PLOTS / '02_funding_history.png'})")


def section_taker_flow(data: dict, report: list):
    """Section 4: Taker Flow Behavior."""
    report.append("\n# 4. TAKER FLOW BEHAVIOR\n")

    ohlcv = data.get('ohlcv_1h')
    if ohlcv is None or ohlcv.empty:
        report.append("No OHLCV data available for taker flow analysis.")
        return

    df = ohlcv.copy()
    df = taker_imbalance(df)

    ti = df['taker_imbalance']
    report.append(f"**Taker Imbalance Stats (1h):**")
    report.append(f"  Mean: {ti.mean():.4f}")
    report.append(f"  Median: {ti.median():.4f}")
    report.append(f"  Std: {ti.std():.4f}")
    report.append(f"  Skew: {ti.skew():.3f}")

    bias = "BUYING" if ti.mean() > 0 else "SELLING"
    report.append(f"\n**Aggregate bias**: {bias} (mean imbalance = {ti.mean():.4f})")

    buy_dom = (ti > 0).mean() * 100
    report.append(f"**Buy-dominant bars**: {buy_dom:.1f}%")
    report.append(f"**Sell-dominant bars**: {100 - buy_dom:.1f}%")

    # Volume stats
    total_buy = df['taker_buy_volume'].sum()
    total_sell = df['taker_sell_volume'].sum()
    report.append(f"\n**Total taker buy volume**: {total_buy:,.0f}")
    report.append(f"**Total taker sell volume**: {total_sell:,.0f}")
    report.append(f"**Net imbalance**: {(total_buy - total_sell):,.0f} ({(total_buy/total_sell - 1)*100:.2f}% excess buying)")

    # Aggressive volume spikes
    vol_total = df['taker_buy_volume'] + df['taker_sell_volume']
    vol_z = (vol_total - vol_total.rolling(100).mean()) / vol_total.rolling(100).std()
    spike_count = (vol_z > 2).sum()
    report.append(f"\n**Aggressive volume spikes (z>2)**: {spike_count} ({spike_count/len(df)*100:.1f}% of bars)")

    # Plot taker imbalance
    fig, axes = plt.subplots(2, 1, figsize=(14, 8))

    axes[0].bar(df['dt'], ti, color=[COLORS['bull'] if x > 0 else COLORS['bear'] for x in ti.fillna(0)], width=0.04, alpha=0.5)
    axes[0].set_title('SOLUSDT Taker Imbalance (1h)', fontweight='bold')
    axes[0].set_ylabel('Imbalance')
    axes[0].axhline(0, color='white', linewidth=0.5)

    # Rolling delta
    delta = df['taker_buy_volume'] - df['taker_sell_volume']
    rolling_delta = delta.rolling(24).sum()
    axes[1].plot(df['dt'], rolling_delta, color=COLORS['neutral'], linewidth=0.8)
    axes[1].axhline(0, color='white', linewidth=0.5)
    axes[1].set_title('24h Rolling Taker Delta', fontweight='bold')
    axes[1].set_ylabel('Net Delta')

    plt.tight_layout()
    plt.savefig(PLOTS / "03_taker_flow.png", bbox_inches='tight')
    plt.close()
    report.append(f"\n![Taker Flow]({PLOTS / '03_taker_flow.png'})")


def section_volatility_clustering(data: dict, report: list):
    """Section 5: Volatility Clustering."""
    report.append("\n# 5. VOLATILITY CLUSTERING\n")

    ohlcv = data.get('ohlcv_1h')
    if ohlcv is None or ohlcv.empty:
        report.append("No OHLCV data available.")
        return

    df = ohlcv.copy()
    df = add_all_volatility(df)

    atr = df['atr_14']
    rvol = df['rvol_20']
    bbw = df['bbw_20']

    report.append(f"**ATR(14) Stats:**")
    report.append(f"  Mean: {atr.mean():.4f} | Median: {atr.median():.4f}")
    report.append(f"  P10: {atr.quantile(0.1):.4f} | P90: {atr.quantile(0.9):.4f}")

    report.append(f"\n**Realized Vol(20) Stats:**")
    report.append(f"  Mean: {rvol.mean():.6f} | Median: {rvol.median():.6f}")

    # Compression analysis
    comp = df['compression_pctl_100'].dropna()
    ultra_compressed = (comp < 10).sum()
    expanded = (comp > 90).sum()
    report.append(f"\n**Compression Analysis:**")
    report.append(f"  Ultra-compressed (pctl < 10): {ultra_compressed} bars ({ultra_compressed/len(comp)*100:.1f}%)")
    report.append(f"  Expanded (pctl > 90): {expanded} bars ({expanded/len(comp)*100:.1f}%)")

    # What happens after compression?
    if 'compression_pctl_100' in df.columns:
        df['fwd_ret_12h'] = df['close'].shift(-12) / df['close'] - 1
        df['fwd_ret_24h'] = df['close'].shift(-24) / df['close'] - 1

        compressed = df[df['compression_pctl_100'] < 10].copy()
        normal = df[(df['compression_pctl_100'] >= 25) & (df['compression_pctl_100'] <= 75)].copy()

        if len(compressed) > 10 and len(normal) > 10:
            abs_ret_comp_12 = compressed['fwd_ret_12h'].abs().mean() * 100
            abs_ret_norm_12 = normal['fwd_ret_12h'].abs().mean() * 100
            abs_ret_comp_24 = compressed['fwd_ret_24h'].abs().mean() * 100
            abs_ret_norm_24 = normal['fwd_ret_24h'].abs().mean() * 100

            report.append(f"\n**Post-Compression Move Magnitude:**")
            report.append(f"  Avg |12h move| after compression: {abs_ret_comp_12:.2f}%")
            report.append(f"  Avg |12h move| normal: {abs_ret_norm_12:.2f}%")
            report.append(f"  **Expansion ratio (12h): {abs_ret_comp_12/abs_ret_norm_12:.2f}x**")
            report.append(f"  Avg |24h move| after compression: {abs_ret_comp_24:.2f}%")
            report.append(f"  Avg |24h move| normal: {abs_ret_norm_24:.2f}%")
            report.append(f"  **Expansion ratio (24h): {abs_ret_comp_24/abs_ret_norm_24:.2f}x**")

    # Plot volatility
    fig, axes = plt.subplots(3, 1, figsize=(14, 12))

    axes[0].plot(df['dt'], atr, color=COLORS['accent'], linewidth=0.7)
    axes[0].set_title('ATR(14) — 1h', fontweight='bold')

    if 'compression_pctl_100' in df.columns:
        cp = df['compression_pctl_100']
        axes[1].fill_between(df['dt'], 0, cp, alpha=0.3, color=COLORS['neutral'])
        axes[1].axhline(10, color=COLORS['bear'], linestyle='--', alpha=0.5, label='Compression zone')
        axes[1].axhline(90, color=COLORS['bull'], linestyle='--', alpha=0.5, label='Expansion zone')
        axes[1].set_title('Compression Percentile (ATR rank)', fontweight='bold')
        axes[1].legend()

    axes[2].plot(df['dt'], bbw, color=COLORS['muted'], linewidth=0.7)
    axes[2].set_title('Bollinger Band Width(20)', fontweight='bold')

    plt.tight_layout()
    plt.savefig(PLOTS / "04_volatility.png", bbox_inches='tight')
    plt.close()
    report.append(f"\n![Volatility]({PLOTS / '04_volatility.png'})")


def section_session_analysis(data: dict, report: list):
    """Section 6: Session Differences."""
    report.append("\n# 6. SESSION DIFFERENCES\n")

    ohlcv = data.get('ohlcv_1h')
    if ohlcv is None or ohlcv.empty:
        report.append("No OHLCV data.")
        return

    df = ohlcv.copy()
    df = tag_sessions(df)
    df['returns'] = df['close'].pct_change() * 100
    df['abs_returns'] = df['returns'].abs()

    report.append("| Session | Bars | Mean Ret% | Abs Ret% | Vol | Taker Imb | Buy Dom% |")
    report.append("|---------|------|-----------|----------|-----|-----------|----------|")

    for session in ['asia', 'london', 'ny', 'off']:
        s = df[df['session'] == session]
        if len(s) < 10:
            continue

        mean_ret = s['returns'].mean()
        abs_ret = s['abs_returns'].mean()
        mean_vol = s['volume'].mean()
        ti = ((s['taker_buy_volume'] - s['taker_sell_volume']) /
              (s['taker_buy_volume'] + s['taker_sell_volume'])).mean()
        buy_dom = (s['taker_buy_volume'] > s['taker_sell_volume']).mean() * 100

        report.append(f"| {session:7} | {len(s):4} | {mean_ret:+.4f} | {abs_ret:.4f} | {mean_vol:,.0f} | {ti:+.4f} | {buy_dom:.1f}% |")

    # Weekend analysis
    wknd = df[df['is_weekend'] == True]
    wkdy = df[df['is_weekend'] == False]
    if len(wknd) > 10:
        report.append(f"\n**Weekend vs Weekday:**")
        report.append(f"  Weekend avg |return|: {wknd['abs_returns'].mean():.4f}%")
        report.append(f"  Weekday avg |return|: {wkdy['abs_returns'].mean():.4f}%")
        report.append(f"  Weekend avg volume: {wknd['volume'].mean():,.0f}")
        report.append(f"  Weekday avg volume: {wkdy['volume'].mean():,.0f}")

    # Plot session returns distribution
    fig, axes = plt.subplots(1, 3, figsize=(14, 5))
    for i, session in enumerate(['asia', 'london', 'ny']):
        s = df[df['session'] == session]['returns'].dropna()
        if len(s) > 0:
            axes[i].hist(s, bins=50, color=COLORS['neutral'], alpha=0.7, edgecolor='none')
            axes[i].axvline(s.mean(), color=COLORS['accent'], linestyle='--', label=f'mean={s.mean():.3f}%')
            axes[i].set_title(f'{session.upper()} Returns Dist', fontweight='bold')
            axes[i].legend()
            axes[i].set_xlabel('Return %')

    plt.tight_layout()
    plt.savefig(PLOTS / "05_session_returns.png", bbox_inches='tight')
    plt.close()
    report.append(f"\n![Session Returns]({PLOTS / '05_session_returns.png'})")


def section_long_short_asymmetry(data: dict, report: list):
    """Section 7: Long vs Short Asymmetry — CRITICAL RESEARCH QUESTION."""
    report.append("\n# 7. LONG vs SHORT ASYMMETRY (CRITICAL)\n")
    report.append("*Investigating whether SOLUSDT has structurally stronger LONG momentum than SHORT momentum.*\n")

    ohlcv = data.get('ohlcv_1h')
    if ohlcv is None or ohlcv.empty:
        report.append("No OHLCV data.")
        return

    df = ohlcv.copy()
    df['returns'] = df['close'].pct_change() * 100

    # Separate positive and negative returns
    up = df[df['returns'] > 0]['returns']
    down = df[df['returns'] < 0]['returns']

    report.append(f"**Return Asymmetry:**")
    report.append(f"  Up bars: {len(up)} ({len(up)/len(df)*100:.1f}%)")
    report.append(f"  Down bars: {len(down)} ({len(down)/len(df)*100:.1f}%)")
    report.append(f"  Mean up: +{up.mean():.4f}%")
    report.append(f"  Mean down: {down.mean():.4f}%")
    report.append(f"  **Avg up / |Avg down|: {up.mean() / abs(down.mean()):.3f}**")
    report.append(f"  Skew (full): {df['returns'].skew():.3f}")

    # Large move asymmetry
    for threshold in [1, 2, 3, 5]:
        big_up = (df['returns'] > threshold).sum()
        big_down = (df['returns'] < -threshold).sum()
        report.append(f"  Moves > {threshold}%: Up={big_up} vs Down={big_down} (ratio: {big_up/(big_down+1):.2f})")

    # Consecutive move analysis
    df['direction'] = np.sign(df['returns'])
    df['streak_change'] = (df['direction'] != df['direction'].shift()).cumsum()
    streaks = df.groupby('streak_change').agg(
        direction=('direction', 'first'),
        length=('direction', 'count'),
        total_move=('returns', 'sum'),
    )

    up_streaks = streaks[streaks['direction'] > 0]
    dn_streaks = streaks[streaks['direction'] < 0]

    report.append(f"\n**Consecutive Move Streaks:**")
    report.append(f"  Avg up streak length: {up_streaks['length'].mean():.2f} bars")
    report.append(f"  Avg down streak length: {dn_streaks['length'].mean():.2f} bars")
    report.append(f"  Max up streak: {up_streaks['length'].max()} bars")
    report.append(f"  Max down streak: {dn_streaks['length'].max()} bars")
    report.append(f"  Avg up streak total move: +{up_streaks['total_move'].mean():.2f}%")
    report.append(f"  Avg down streak total move: {dn_streaks['total_move'].mean():.2f}%")

    # Momentum persistence: autocorrelation
    report.append(f"\n**Return Autocorrelation (momentum persistence):**")
    for lag in [1, 2, 4, 8, 12, 24]:
        ac = df['returns'].autocorr(lag=lag)
        report.append(f"  Lag {lag:2d}h: {ac:.4f}")

    # Taker flow asymmetry during moves
    df = taker_imbalance(df)
    up_mask = df['returns'] > 0.5
    dn_mask = df['returns'] < -0.5

    if up_mask.sum() > 10 and dn_mask.sum() > 10:
        up_ti = df.loc[up_mask, 'taker_imbalance'].mean()
        dn_ti = df.loc[dn_mask, 'taker_imbalance'].mean()
        report.append(f"\n**Taker imbalance during moves (>0.5%):**")
        report.append(f"  During up moves: {up_ti:.4f}")
        report.append(f"  During down moves: {dn_ti:.4f}")
        report.append(f"  **Aggression asymmetry: {abs(up_ti)/abs(dn_ti):.3f}x** (>1 = longs more aggressive)")

    # OI behavior during up vs down moves
    oi = data.get('oi_daily')
    if oi is not None and not oi.empty:
        # Merge daily OI with daily returns
        ohlcv_daily = df.set_index('dt').resample('1D').agg({
            'open': 'first', 'close': 'last', 'high': 'max',
            'low': 'min', 'volume': 'sum'
        }).dropna().reset_index()
        ohlcv_daily['returns'] = ohlcv_daily['close'].pct_change() * 100

        merged = pd.merge_asof(
            ohlcv_daily.sort_values('dt'),
            oi[['dt', 'open_interest']].sort_values('dt'),
            on='dt', direction='nearest'
        )
        merged['oi_chg'] = merged['open_interest'].pct_change() * 100

        if len(merged) > 50:
            up_days = merged[merged['returns'] > 1]
            dn_days = merged[merged['returns'] < -1]

            report.append(f"\n**OI behavior during 1%+ daily moves:**")
            report.append(f"  Up days OI change: {up_days['oi_chg'].mean():+.2f}% (n={len(up_days)})")
            report.append(f"  Down days OI change: {dn_days['oi_chg'].mean():+.2f}% (n={len(dn_days)})")

    # Plot
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))

    # Return distribution
    axes[0, 0].hist(up.values, bins=80, color=COLORS['bull'], alpha=0.6, label='Up', density=True)
    axes[0, 0].hist(down.values, bins=80, color=COLORS['bear'], alpha=0.6, label='Down', density=True)
    axes[0, 0].set_title('Return Distribution (Long vs Short)', fontweight='bold')
    axes[0, 0].legend()

    # QQ-like: sorted up vs sorted down (abs)
    n = min(len(up), len(down))
    sorted_up = np.sort(up.values)[-n:]
    sorted_dn = np.sort(np.abs(down.values))[-n:]
    axes[0, 1].scatter(sorted_dn, sorted_up, alpha=0.3, s=5, color=COLORS['accent'])
    max_val = max(sorted_up.max(), sorted_dn.max())
    axes[0, 1].plot([0, max_val], [0, max_val], 'w--', alpha=0.5, label='Symmetry line')
    axes[0, 1].set_title('Up vs |Down| Quantile Plot', fontweight='bold')
    axes[0, 1].set_xlabel('|Down moves|')
    axes[0, 1].set_ylabel('Up moves')
    axes[0, 1].legend()

    # Streak analysis
    up_lens = up_streaks['length'].value_counts().sort_index()
    dn_lens = dn_streaks['length'].value_counts().sort_index()
    max_len = max(up_lens.index.max(), dn_lens.index.max()) if len(up_lens) > 0 and len(dn_lens) > 0 else 10
    x = range(1, min(int(max_len) + 1, 20))
    axes[1, 0].bar([i - 0.15 for i in x], [up_lens.get(i, 0) for i in x], 0.3, color=COLORS['bull'], label='Up streaks', alpha=0.7)
    axes[1, 0].bar([i + 0.15 for i in x], [dn_lens.get(i, 0) for i in x], 0.3, color=COLORS['bear'], label='Down streaks', alpha=0.7)
    axes[1, 0].set_title('Consecutive Move Streak Distribution', fontweight='bold')
    axes[1, 0].set_xlabel('Streak length (bars)')
    axes[1, 0].legend()

    # Autocorrelation
    lags = range(1, 49)
    acs = [df['returns'].autocorr(lag=l) for l in lags]
    axes[1, 1].bar(lags, acs, color=COLORS['neutral'], alpha=0.7)
    axes[1, 1].axhline(0, color='white', linewidth=0.5)
    axes[1, 1].set_title('Return Autocorrelation by Lag', fontweight='bold')
    axes[1, 1].set_xlabel('Lag (hours)')

    plt.tight_layout()
    plt.savefig(PLOTS / "06_long_short_asymmetry.png", bbox_inches='tight')
    plt.close()
    report.append(f"\n![Long vs Short Asymmetry]({PLOTS / '06_long_short_asymmetry.png'})")


def section_liquidation_behavior(data: dict, report: list):
    """Section 8: Liquidation Behavior."""
    report.append("\n# 8. LIQUIDATION BEHAVIOR\n")

    liq = data.get('liq_daily')
    if liq is None or liq.empty:
        report.append("No liquidation data.")
        return

    liq = liq.copy().sort_values('dt')

    report.append(f"**Liquidation Stats (daily, contracts):**")
    report.append(f"  Avg long liq: {liq['long_liquidations'].mean():,.0f}")
    report.append(f"  Avg short liq: {liq['short_liquidations'].mean():,.0f}")
    report.append(f"  Long/Short ratio: {liq['long_liquidations'].sum() / max(liq['short_liquidations'].sum(), 1):.2f}")

    # Extreme liquidation events
    liq['total_liq'] = liq['long_liquidations'] + liq['short_liquidations']
    p95 = liq['total_liq'].quantile(0.95)
    extreme_days = liq[liq['total_liq'] > p95]
    report.append(f"\n**Extreme liquidation days (>P95)**: {len(extreme_days)}")

    # Merge with price to check continuation vs reversal
    ohlcv = data.get('ohlcv_1h')
    if ohlcv is not None and not ohlcv.empty:
        daily = ohlcv.set_index('dt').resample('1D').agg({'close': 'last'}).dropna().reset_index()
        daily['fwd_1d'] = daily['close'].shift(-1) / daily['close'] - 1
        daily['fwd_3d'] = daily['close'].shift(-3) / daily['close'] - 1

        merged = pd.merge_asof(
            liq.sort_values('dt'),
            daily[['dt', 'fwd_1d', 'fwd_3d']].sort_values('dt'),
            on='dt', direction='nearest'
        )

        extreme = merged[merged['total_liq'] > p95]
        normal = merged[(merged['total_liq'] > 0) & (merged['total_liq'] <= liq['total_liq'].quantile(0.5))]

        if len(extreme) > 5 and len(normal) > 20:
            report.append(f"\n**Post-Liquidation Cascade Behavior (extreme vs normal days):**")
            report.append(f"  Extreme days avg 1d forward: {extreme['fwd_1d'].mean()*100:+.2f}%")
            report.append(f"  Normal days avg 1d forward: {normal['fwd_1d'].mean()*100:+.2f}%")
            report.append(f"  Extreme days avg 3d forward: {extreme['fwd_3d'].mean()*100:+.2f}%")
            report.append(f"  Normal days avg 3d forward: {normal['fwd_3d'].mean()*100:+.2f}%")

            # Break by long vs short dominant liquidations
            long_dom = extreme[extreme['long_liquidations'] > extreme['short_liquidations']]
            short_dom = extreme[extreme['long_liquidations'] < extreme['short_liquidations']]

            if len(long_dom) > 2 and len(short_dom) > 2:
                report.append(f"\n  Long-dominated cascade days: {len(long_dom)} → avg 1d fwd: {long_dom['fwd_1d'].mean()*100:+.2f}%")
                report.append(f"  Short-dominated cascade days: {len(short_dom)} → avg 1d fwd: {short_dom['fwd_1d'].mean()*100:+.2f}%")

    # Plot liquidations
    fig, axes = plt.subplots(2, 1, figsize=(14, 8))

    axes[0].bar(liq['dt'], liq['long_liquidations'], color=COLORS['bear'], alpha=0.6, label='Long Liq (longs stopped)', width=1)
    axes[0].bar(liq['dt'], -liq['short_liquidations'], color=COLORS['bull'], alpha=0.6, label='Short Liq (shorts stopped)', width=1)
    axes[0].set_title('SOLUSDT Daily Liquidations', fontweight='bold')
    axes[0].set_ylabel('Liquidated contracts')
    axes[0].legend()

    # Cumulative
    liq['net_liq'] = liq['long_liquidations'] - liq['short_liquidations']
    axes[1].plot(liq['dt'], liq['net_liq'].cumsum(), color=COLORS['accent'], linewidth=1)
    axes[1].axhline(0, color='white', linewidth=0.5)
    axes[1].set_title('Cumulative Net Liquidations (Long - Short)', fontweight='bold')
    axes[1].set_ylabel('Cumulative net')

    plt.tight_layout()
    plt.savefig(PLOTS / "07_liquidations.png", bbox_inches='tight')
    plt.close()
    report.append(f"\n![Liquidations]({PLOTS / '07_liquidations.png'})")


def section_regime_structures(data: dict, report: list):
    """Section 9: Observable Regime Structures."""
    report.append("\n# 9. REGIME STRUCTURE IDENTIFICATION\n")

    ohlcv = data.get('ohlcv_1h')
    oi = data.get('oi_daily')

    if ohlcv is None or ohlcv.empty:
        report.append("No OHLCV data.")
        return

    df = ohlcv.copy()
    df = add_all_volatility(df)

    # Price/OI interaction
    if oi is not None and not oi.empty:
        # Resample OHLCV to daily
        daily = df.set_index('dt').resample('1D').agg({
            'open': 'first', 'close': 'last', 'high': 'max',
            'low': 'min', 'volume': 'sum'
        }).dropna().reset_index()
        daily['returns'] = daily['close'].pct_change() * 100

        merged = pd.merge_asof(
            daily.sort_values('dt'),
            oi[['dt', 'open_interest']].sort_values('dt'),
            on='dt', direction='nearest'
        )
        merged = price_oi_state(merged, price_col='close', oi_col='open_interest')

        # Distribution of states
        state_counts = merged['poi_state'].value_counts()
        report.append("**Price/OI State Distribution:**")
        for state, count in state_counts.items():
            pct = count / len(merged) * 100
            report.append(f"  {state}: {count} days ({pct:.1f}%)")

        # Forward returns by state
        merged['fwd_1d'] = merged['close'].shift(-1) / merged['close'] - 1
        merged['fwd_3d'] = merged['close'].shift(-3) / merged['close'] - 1

        report.append(f"\n**Avg Forward Returns by Price/OI State:**")
        report.append("| State | N | 1D Fwd | 3D Fwd |")
        report.append("|-------|---|--------|--------|")
        for state in ['long_build', 'short_cover', 'short_build', 'long_liquidation', 'neutral']:
            subset = merged[merged['poi_state'] == state]
            if len(subset) > 5:
                r1 = subset['fwd_1d'].mean() * 100
                r3 = subset['fwd_3d'].mean() * 100
                report.append(f"| {state} | {len(subset)} | {r1:+.3f}% | {r3:+.3f}% |")

    # Volatility regime
    if 'compression_pctl_100' in df.columns:
        cp = df['compression_pctl_100'].dropna()
        df_valid = df.dropna(subset=['compression_pctl_100'])

        df_valid['vol_regime'] = pd.cut(df_valid['compression_pctl_100'],
                                         bins=[0, 20, 40, 60, 80, 100],
                                         labels=['very_low', 'low', 'medium', 'high', 'very_high'])

        df_valid['abs_ret'] = df_valid['close'].pct_change().abs() * 100

        report.append(f"\n**Behavior by Volatility Regime:**")
        report.append("| Regime | Bars | Avg |Ret%| | Avg Vol |")
        report.append("|--------|------|-----------|---------|")
        for regime in ['very_low', 'low', 'medium', 'high', 'very_high']:
            r = df_valid[df_valid['vol_regime'] == regime]
            if len(r) > 10:
                report.append(f"| {regime} | {len(r)} | {r['abs_ret'].mean():.4f}% | {r['volume'].mean():,.0f} |")


def main():
    logger.info("=" * 60)
    logger.info("SOLUSDT EXPLORATORY ANALYSIS")
    logger.info("=" * 60)

    data = load_all_data()

    if not data:
        logger.error("No data available. Run data collection first.")
        return

    report = ["# SOLUSDT EXPLORATORY ANALYSIS", f"*Generated: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}*\n"]

    section_data_audit(data, report)
    section_oi_behavior(data, report)
    section_funding_behavior(data, report)
    section_taker_flow(data, report)
    section_volatility_clustering(data, report)
    section_session_analysis(data, report)
    section_long_short_asymmetry(data, report)
    section_liquidation_behavior(data, report)
    section_regime_structures(data, report)

    # Save report
    report_text = "\n".join(report)
    out_path = REPORTS_DIR / "sol_exploratory_analysis.md"
    out_path.write_text(report_text)
    logger.info(f"\nReport saved to {out_path}")
    logger.info(f"Plots saved to {PLOTS}")


if __name__ == "__main__":
    main()

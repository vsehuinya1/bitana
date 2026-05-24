"""
Liquidation Cluster Expansion Signal — V2 High-Selectivity.

PHILOSOPHY:
"Join confirmed expansion early, not predict expansion."

We intentionally miss the first portion of the move in exchange for
fewer fake starts. Target: 3-8 trades/week.

CONTEXT LAYER (daily):
- Liquidation cluster ONLY (liq > rolling P90, 2-day window)

ENTRY (5m):
MANDATORY: cascade_active must be True
SCORED CONFLUENCE: require N-of-6 confirmations (default 4):
  1. Breakout: close above local range high (60-bar)
  2. Taker imbalance spike: z > threshold
  3. Volume surge: z > threshold
  4. Candle body strength: body > 60% of range
  5. Minimum impulse: bar return > 0.3%
  6. Momentum aligned: close > EMA(20)

ADDITIONAL:
- Cooldown: 36 bars (3 hours) between entries
- No re-entry after stop-out in same cascade window

EXITS (unchanged — they work):
- Volatility trail (2x ATR)
- Structure trail (below swing lows)
- Expansion decay (R decays 30% from peak)
- Partials + runner (50% off at 2R)
"""
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
from loguru import logger

from research.engine.signals import SignalGenerator
from research.engine.backtest import Position


# ════════════════════════════════════════════════════════════
# Configuration
# ════════════════════════════════════════════════════════════

@dataclass
class LiqClusterConfig:
    """High-selectivity liq cluster signal config."""

    # ── Context (daily) ──
    liq_lookback: int = 90
    liq_percentile: float = 0.90
    liq_min_lookback: int = 30
    liq_window: int = 2            # Cascade window stays active N days
    require_short_squeeze: bool = True  # Only trade short-squeeze cascades (forensic: -0.56σ)
    ret5d_min: float = -5.0        # Skip cascades during deep sell-offs (DD filter)

    # ── Entry confirmation (5m) ──
    range_lookback: int = 60       # 5 hours for range
    imb_z_threshold: float = 2.0   # Taker imbalance z-score
    vol_z_threshold: float = 3.0   # Volume z-score (forensic: winners median 3.89)
    body_strength_min: float = 0.60  # Min body/range ratio
    impulse_min_pct: float = 0.30  # Min bar return %
    ema_period: int = 20           # Momentum EMA
    z_lookback: int = 100          # Z-score rolling window
    min_confirmations: int = 4     # Require N-of-6 confirmations

    # ── Selectivity ──
    cooldown_bars: int = 36        # 3 hours between trades
    no_reentry_after_stop: bool = True  # Don't re-enter after stop in same window

    # ── Risk ──
    atr_period: int = 14
    initial_stop_atr: float = 2.5  # Widened from 2.0 (forensic: 31% of winners dip past 2.0)
    risk_fraction: float = 0.01

    # ── Exits ──
    vol_trail_atr: float = 2.0
    struct_lookback: int = 12
    decay_threshold: float = 0.30
    partial_r: float = 2.5         # Partial take-profit level (sweep: 2.5R > 2.0R)
    partial_fraction: float = 0.50
    max_hold_bars: int = 288       # 24 hours


# ════════════════════════════════════════════════════════════
# Context Layer (daily cascade detection)
# ════════════════════════════════════════════════════════════

def classify_cascade_context(daily: pd.DataFrame, cfg: LiqClusterConfig) -> pd.DataFrame:
    """
    Mark cascade windows on daily data.
    Returns daily df with 'cascade_active' and 'cascade_strength' columns.
    """
    daily = daily.copy()

    if 'total_liq' not in daily.columns:
        daily['total_liq'] = daily.get('long_liquidations', 0) + daily.get('short_liquidations', 0)

    daily['liq_p90'] = daily['total_liq'].rolling(
        cfg.liq_lookback, min_periods=cfg.liq_min_lookback
    ).quantile(cfg.liq_percentile)

    daily['liq_spike'] = (daily['total_liq'] > daily['liq_p90']) & (daily['liq_p90'] > 0)
    daily['cascade_strength'] = (daily['total_liq'] / daily['liq_p90'].replace(0, np.nan)).fillna(0)

    # Liq direction imbalance: negative = more short liqs (short squeeze)
    if 'long_liquidations' in daily.columns and 'short_liquidations' in daily.columns:
        daily['liq_direction_imb'] = (
            (daily['long_liquidations'] - daily['short_liquidations']) /
            daily['total_liq'].replace(0, np.nan)
        ).fillna(0)

    # Window: cascade stays active for N days after spike
    daily['cascade_active'] = False
    for i in range(cfg.liq_window + 1):
        daily['cascade_active'] = daily['cascade_active'] | daily['liq_spike'].shift(i).fillna(False)

    # Daily momentum for DD filter
    daily['ret_5d'] = daily['close'].pct_change(5) * 100

    active = daily['cascade_active'].sum()
    spikes = daily['liq_spike'].sum()
    logger.info(f"Cascade context: {spikes} spikes → {active} active days "
                f"({active / len(daily) * 100:.1f}%) in {len(daily)} daily bars")

    return daily


# ════════════════════════════════════════════════════════════
# 5m Feature Computation
# ════════════════════════════════════════════════════════════

def compute_5m_features(df: pd.DataFrame, cfg: LiqClusterConfig) -> pd.DataFrame:
    """Compute all 5m features needed for entry confirmation."""
    df = df.copy()
    n = cfg.z_lookback

    # ATR
    prev_close = df['close'].shift(1)
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - prev_close).abs(),
        (df['low'] - prev_close).abs(),
    ], axis=1).max(axis=1)
    df['atr'] = tr.ewm(span=cfg.atr_period, adjust=False).mean()

    # EMA for momentum
    df['ema'] = df['close'].ewm(span=cfg.ema_period, adjust=False).mean()

    # Range high (breakout reference)
    df['range_high'] = df['high'].rolling(cfg.range_lookback, min_periods=10).max().shift(1)

    # Taker imbalance z-score
    if 'taker_buy_volume' in df.columns and 'taker_sell_volume' in df.columns:
        total = df['taker_buy_volume'] + df['taker_sell_volume']
        imb = (df['taker_buy_volume'] - df['taker_sell_volume']) / total.replace(0, np.nan)
        imb_mean = imb.rolling(n, min_periods=20).mean()
        imb_std = imb.rolling(n, min_periods=20).std().replace(0, np.nan)
        df['imb_z'] = (imb - imb_mean) / imb_std
    else:
        df['imb_z'] = 0

    # Volume z-score
    vol_mean = df['volume'].rolling(n, min_periods=20).mean()
    vol_std = df['volume'].rolling(n, min_periods=20).std().replace(0, np.nan)
    df['vol_z'] = (df['volume'] - vol_mean) / vol_std

    # Candle body strength
    candle_range = df['high'] - df['low']
    candle_body = (df['close'] - df['open']).abs()
    df['body_strength'] = candle_body / candle_range.replace(0, np.nan)

    # Bar return %
    df['bar_return_pct'] = ((df['close'] - df['open']) / df['open'] * 100).fillna(0)

    # Swing low for structure trail
    df['swing_low'] = df['low'].rolling(cfg.struct_lookback, min_periods=3).min()

    logger.info(f"5m features computed on {len(df)} bars")
    return df


# ════════════════════════════════════════════════════════════
# Signal
# ════════════════════════════════════════════════════════════

class LiqClusterExpansionSignal(SignalGenerator):
    """
    High-selectivity liquidation cluster expansion signal.

    Usage:
        signal = LiqClusterExpansionSignal(config)
        daily = classify_cascade_context(daily, config)
        df_5m = signal.prepare(df_5m, daily)
        engine.run(df_5m, signal.evaluate)
    """

    def __init__(self, config: LiqClusterConfig | None = None):
        self.cfg = config or LiqClusterConfig()
        self._cooldown: int = 0
        self._in_trade: bool = False
        self._bars_held: int = 0
        self._entry_price: float = 0
        self._risk_per_unit: float = 0
        self._partial_taken: bool = False
        self._best_price: float = 0
        self._vol_trail: float = 0
        self._struct_trail: float = 0
        self._stopped_in_window: bool = False
        self._last_cascade_state: bool = False

    def name(self) -> str:
        return "liq_cluster_expansion_v2"

    def prepare(self, df_5m: pd.DataFrame, df_daily: pd.DataFrame) -> pd.DataFrame:
        """Merge cascade context into 5m and compute features."""
        # Compute 5m features
        df_5m = compute_5m_features(df_5m, self.cfg)

        # Merge cascade context from daily
        context_cols = ['dt', 'cascade_active', 'cascade_strength', 'liq_direction_imb', 'ret_5d']
        available = [c for c in context_cols if c in df_daily.columns]

        if 'dt' in df_5m.columns and 'dt' in df_daily.columns:
            df_5m = pd.merge_asof(
                df_5m.sort_values('dt'),
                df_daily[available].sort_values('dt'),
                on='dt', direction='backward',
            )
        elif 'timestamp' in df_5m.columns and 'timestamp' in df_daily.columns:
            ts_cols = ['timestamp', 'cascade_active', 'cascade_strength', 'liq_direction_imb']
            ts_avail = [c for c in ts_cols if c in df_daily.columns]
            df_5m = pd.merge_asof(
                df_5m.sort_values('timestamp'),
                df_daily[ts_avail].sort_values('timestamp'),
                on='timestamp', direction='backward',
            )

        df_5m['cascade_active'] = df_5m['cascade_active'].fillna(False)

        # Liq direction filter (forensic: -0.56σ effect, strongest separator)
        # Short-squeeze bias = liq_direction_imb < 0 (more short liqs)
        if self.cfg.require_short_squeeze and 'liq_direction_imb' in df_5m.columns:
            df_5m['cascade_active'] = df_5m['cascade_active'] & (df_5m['liq_direction_imb'] < 0)

        # Daily momentum filter: skip cascades during deep sell-offs
        if self.cfg.ret5d_min is not None and 'ret_5d' in df_5m.columns:
            df_5m['cascade_active'] = df_5m['cascade_active'] & (df_5m['ret_5d'] > self.cfg.ret5d_min)

        # Check individual confirmations
        df_5m['confirm_breakout'] = df_5m['close'] > df_5m['range_high']
        df_5m['confirm_imb'] = df_5m['imb_z'] > self.cfg.imb_z_threshold
        df_5m['confirm_vol'] = df_5m['vol_z'] > self.cfg.vol_z_threshold
        df_5m['confirm_body'] = df_5m['body_strength'] > self.cfg.body_strength_min
        df_5m['confirm_impulse'] = df_5m['bar_return_pct'] > self.cfg.impulse_min_pct
        df_5m['confirm_momentum'] = df_5m['close'] > df_5m['ema']

        # Scored confluence: count how many confirmations pass
        confirm_cols = ['confirm_breakout', 'confirm_imb', 'confirm_vol',
                        'confirm_body', 'confirm_impulse', 'confirm_momentum']
        df_5m['confirm_count'] = sum(df_5m[c].astype(int) for c in confirm_cols)

        # Entry: cascade active (mandatory) + N-of-6 confirmations
        df_5m['entry_signal'] = (
            df_5m['cascade_active'] &
            (df_5m['confirm_count'] >= self.cfg.min_confirmations)
        )

        # Stats
        cascade_bars = df_5m['cascade_active'].sum()
        signals = df_5m['entry_signal'].sum()
        days = len(df_5m) / 288 if len(df_5m) > 0 else 1
        logger.info(f"Signal prep: {len(df_5m)} bars ({days:.0f} days)")
        logger.info(f"  Cascade active: {cascade_bars} bars ({cascade_bars / len(df_5m) * 100:.1f}%)")
        logger.info(f"  Entry signals: {signals} ({signals / days:.1f}/day)")

        # Per-condition stats during cascade windows
        cascade_mask = df_5m['cascade_active']
        if cascade_bars > 0:
            for cond in ['confirm_breakout', 'confirm_imb', 'confirm_vol',
                         'confirm_body', 'confirm_impulse', 'confirm_momentum']:
                pct = df_5m.loc[cascade_mask, cond].mean() * 100
                logger.info(f"    {cond}: {pct:.1f}% of cascade bars")

        return df_5m

    def evaluate(
        self,
        bar: pd.Series,
        position: Optional[Position],
        context: dict,
    ) -> dict | None:

        # Track cascade window transitions (reset stop flag on new window)
        current_cascade = bool(bar.get('cascade_active', False))
        if current_cascade and not self._last_cascade_state:
            self._stopped_in_window = False
        self._last_cascade_state = current_cascade

        # ── EXIT LOGIC ──
        if position is not None:
            return self._handle_exit(bar, position)

        # ── COOLDOWN ──
        if self._cooldown > 0:
            self._cooldown -= 1
            return None

        # ── No re-entry after stop in same window ──
        if self.cfg.no_reentry_after_stop and self._stopped_in_window:
            return None

        # ── ENTRY ──
        if not bar.get('entry_signal', False):
            return None

        atr = bar.get('atr', 0)
        if pd.isna(atr) or atr <= 0:
            return None

        entry_price = bar['close']
        stop_distance = atr * self.cfg.initial_stop_atr
        stop_price = entry_price - stop_distance

        capital = context.get('capital', 10_000)
        risk_amount = capital * self.cfg.risk_fraction
        size = risk_amount / stop_distance if stop_distance > 0 else 0
        if size <= 0:
            return None

        # Initialize state
        self._in_trade = True
        self._bars_held = 0
        self._entry_price = entry_price
        self._risk_per_unit = stop_distance
        self._partial_taken = False
        self._best_price = entry_price
        self._vol_trail = 0
        self._struct_trail = 0

        return {
            'action': 'buy',
            'size': size,
            'stop_loss': stop_price,
            'metadata': {
                'signal': self.name(),
                'cascade_strength': bar.get('cascade_strength', 0),
                'imb_z': bar.get('imb_z', 0),
                'vol_z': bar.get('vol_z', 0),
                'body_strength': bar.get('body_strength', 0),
                'bar_return_pct': bar.get('bar_return_pct', 0),
                'atr': atr,
                'risk_per_unit': stop_distance,
            },
        }

    def _handle_exit(self, bar: pd.Series, position: Position) -> dict | None:
        """Adaptive exit logic."""
        self._bars_held += 1
        price = bar['close']
        atr = bar.get('atr', self._risk_per_unit / self.cfg.initial_stop_atr)

        if price > self._best_price:
            self._best_price = price

        current_r = (price - self._entry_price) / self._risk_per_unit if self._risk_per_unit > 0 else 0

        # ── Partial at 2R ──
        if not self._partial_taken and current_r >= self.cfg.partial_r:
            self._partial_taken = True
            return {
                'action': 'close',
                'exit_reason': f'partial_{self.cfg.partial_r:.0f}R',
                'metadata': {'r_at_partial': current_r, 'bars_held': self._bars_held},
            }

        # ── Volatility trail ──
        new_vol_trail = price - atr * self.cfg.vol_trail_atr
        if new_vol_trail > self._vol_trail:
            self._vol_trail = new_vol_trail
        if self._vol_trail > self._entry_price and bar['low'] <= self._vol_trail:
            self._in_trade = False
            self._cooldown = self.cfg.cooldown_bars
            return {
                'action': 'close',
                'exit_reason': 'vol_trail',
                'metadata': {'r_at_exit': current_r, 'bars_held': self._bars_held},
            }

        # ── Structure trail ──
        swing = bar.get('swing_low', 0)
        if swing > self._struct_trail:
            self._struct_trail = swing
        if self._struct_trail > self._entry_price and bar['low'] <= self._struct_trail:
            self._in_trade = False
            self._cooldown = self.cfg.cooldown_bars
            return {
                'action': 'close',
                'exit_reason': 'struct_trail',
                'metadata': {'r_at_exit': current_r, 'bars_held': self._bars_held},
            }

        # ── Expansion decay ──
        if self._bars_held > 6 and current_r > 0.5:
            peak_r = (self._best_price - self._entry_price) / self._risk_per_unit
            if peak_r > 0 and (current_r / peak_r) < (1 - self.cfg.decay_threshold):
                self._in_trade = False
                self._cooldown = self.cfg.cooldown_bars
                return {
                    'action': 'close',
                    'exit_reason': 'expansion_decay',
                    'metadata': {'peak_r': peak_r, 'current_r': current_r, 'bars_held': self._bars_held},
                }

        # ── Time stop ──
        if self._bars_held >= self.cfg.max_hold_bars:
            self._in_trade = False
            self._cooldown = self.cfg.cooldown_bars
            return {
                'action': 'close',
                'exit_reason': 'time_stop',
                'metadata': {'r_at_exit': current_r, 'bars_held': self._bars_held},
            }

        # ── Stop loss hit (engine handles, but set flag) ──
        if bar['low'] <= position.stop_loss:
            self._in_trade = False
            self._stopped_in_window = True
            self._cooldown = self.cfg.cooldown_bars

        return None

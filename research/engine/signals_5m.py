"""
5-Minute Execution Signal — Multi-Timeframe Entry + Adaptive Exit.

This is the execution layer. It operates on 5m bars and uses regime
context from the daily/1h classifier to decide WHEN to enter.

ENTRY TRIGGERS (any one within an active regime window):
1. Taker imbalance explosion   — sudden aggressive buying (imb z-score > 2)
2. Local range break           — price breaks above N-bar high
3. Aggressive delta persistence — sustained taker buying over M bars
4. Volume acceleration         — volume ramp-up (vol z > 2)
5. Volatility expansion        — ATR breaks above compression range

ADAPTIVE EXITS:
1. Volatility trail     — trail stop at N * ATR, adjusts with vol
2. Structure trail      — trail below swing lows
3. Expansion decay exit — exit when expansion momentum fades
4. Partials + runner    — take 50% at 2R, trail remainder
"""
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
from loguru import logger

from research.engine.signals import SignalGenerator
from research.engine.backtest import Position, Side
from research.engine.regime_classifier import Regime, RegimeState


# ════════════════════════════════════════════════════════════
# Configuration
# ════════════════════════════════════════════════════════════

@dataclass
class EntryConfig:
    """5m entry trigger configuration."""
    # Taker imbalance explosion
    imb_z_threshold: float = 2.0       # Z-score threshold for taker imbalance spike
    imb_lookback: int = 100            # Bars for z-score calculation

    # Local range break
    range_lookback: int = 60           # 60 bars = 5 hours on 5m
    range_buffer_pct: float = 0.05     # 0.05% buffer above range high

    # Aggressive delta persistence
    delta_lookback: int = 12           # 12 bars = 1 hour sustained buying
    delta_min_ratio: float = 0.65      # 65% of bars must be buy-dominant

    # Volume acceleration
    vol_z_threshold: float = 2.0       # Volume z-score threshold
    vol_lookback: int = 100            # Bars for z-score calculation

    # Volatility expansion
    vexp_atr_period: int = 14
    vexp_compression_pctl: float = 20  # Must have been compressed recently
    vexp_expansion_mult: float = 1.5   # ATR must expand by this multiplier

    # General
    min_triggers: int = 2              # Minimum number of triggers firing simultaneously
    cooldown_bars: int = 12            # Min bars between entries (1 hour)


@dataclass
class ExitConfig:
    """Adaptive exit configuration."""
    # Volatility trail
    vol_trail_atr_mult: float = 2.0    # Trail at N * current ATR
    vol_trail_min_atr_mult: float = 1.0  # Minimum trail (when vol compressed)

    # Structure trail
    struct_lookback: int = 12          # Swing low lookback (1h of 5m bars)
    struct_buffer_pct: float = 0.1     # Buffer below swing low

    # Expansion decay
    decay_lookback: int = 6            # Check if momentum is fading
    decay_threshold: float = 0.3       # If returns slow by this fraction, exit

    # Partials + runner
    partial_r: float = 2.0             # Take first partial at 2R
    partial_fraction: float = 0.50     # Close 50% at partial target
    runner_trail_atr: float = 1.5      # Trail the runner tighter

    # Time stop
    max_hold_bars: int = 288           # Max 288 5m bars = 24 hours
    time_exit_after: int = 144         # Start considering time exit after 12h

    # Stop loss
    initial_stop_atr: float = 2.0      # Initial stop = entry - N * ATR(5m)


@dataclass
class ExecutionConfig:
    """Combined execution config."""
    entry: EntryConfig = field(default_factory=EntryConfig)
    exit: ExitConfig = field(default_factory=ExitConfig)
    risk_fraction: float = 0.01         # Risk 1% per trade
    allowed_regimes: list[Regime] = field(default_factory=lambda: [
        Regime.LIQUIDATION_CLUSTER,
        Regime.OI_EXPANSION,
        Regime.COMPRESSION,
        Regime.TREND_UP,
    ])


# ════════════════════════════════════════════════════════════
# Entry Trigger Detection
# ════════════════════════════════════════════════════════════

class EntryTriggers:
    """Detects 5m entry triggers."""

    def __init__(self, config: EntryConfig):
        self.cfg = config

    def compute_triggers(self, df: pd.DataFrame) -> pd.DataFrame:
        """Pre-compute all trigger columns on 5m dataframe."""
        df = df.copy()

        # ── 1. Taker imbalance explosion ──
        if 'taker_buy_volume' in df.columns and 'taker_sell_volume' in df.columns:
            total = df['taker_buy_volume'] + df['taker_sell_volume']
            imb = (df['taker_buy_volume'] - df['taker_sell_volume']) / total.replace(0, np.nan)
            imb_mean = imb.rolling(self.cfg.imb_lookback, min_periods=20).mean()
            imb_std = imb.rolling(self.cfg.imb_lookback, min_periods=20).std()
            df['imb_z'] = (imb - imb_mean) / imb_std.replace(0, np.nan)
            df['trigger_imb_explosion'] = df['imb_z'] > self.cfg.imb_z_threshold
        else:
            df['trigger_imb_explosion'] = False

        # ── 2. Local range break ──
        df['range_high'] = df['high'].rolling(self.cfg.range_lookback, min_periods=10).max()
        buffer = df['range_high'] * (self.cfg.range_buffer_pct / 100)
        df['trigger_range_break'] = df['close'] > (df['range_high'].shift(1) + buffer.shift(1))

        # ── 3. Aggressive delta persistence ──
        if 'taker_buy_volume' in df.columns:
            buy_dominant = (df['taker_buy_volume'] > df['taker_sell_volume']).astype(float)
            buy_ratio = buy_dominant.rolling(self.cfg.delta_lookback, min_periods=6).mean()
            df['delta_persistence'] = buy_ratio
            df['trigger_delta_persist'] = buy_ratio > self.cfg.delta_min_ratio
        else:
            df['trigger_delta_persist'] = False

        # ── 4. Volume acceleration ──
        vol_mean = df['volume'].rolling(self.cfg.vol_lookback, min_periods=20).mean()
        vol_std = df['volume'].rolling(self.cfg.vol_lookback, min_periods=20).std()
        df['vol_z'] = (df['volume'] - vol_mean) / vol_std.replace(0, np.nan)
        df['trigger_vol_accel'] = df['vol_z'] > self.cfg.vol_z_threshold

        # ── 5. Volatility expansion ──
        prev_close = df['close'].shift(1)
        tr = pd.concat([
            df['high'] - df['low'],
            (df['high'] - prev_close).abs(),
            (df['low'] - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr = tr.ewm(span=self.cfg.vexp_atr_period, adjust=False).mean()
        df['atr_5m'] = atr

        # ATR percentile
        atr_pctl = atr.rolling(self.cfg.imb_lookback, min_periods=20).apply(
            lambda x: pd.Series(x).rank(pct=True).iloc[-1] * 100, raw=False
        )
        # Was compressed recently (within last 12 bars) and now expanding
        was_compressed = (atr_pctl.rolling(12).min() < self.cfg.vexp_compression_pctl)
        atr_ratio = atr / atr.rolling(self.cfg.vexp_atr_period).mean().replace(0, np.nan)
        df['trigger_vol_expansion'] = was_compressed & (atr_ratio > self.cfg.vexp_expansion_mult)

        # ── Combined trigger count ──
        trigger_cols = [
            'trigger_imb_explosion',
            'trigger_range_break',
            'trigger_delta_persist',
            'trigger_vol_accel',
            'trigger_vol_expansion',
        ]
        df['trigger_count'] = sum(df[c].astype(int) for c in trigger_cols)
        df['any_trigger'] = df['trigger_count'] >= self.cfg.min_triggers

        return df

    def check(self, bar: pd.Series) -> tuple[bool, list[str]]:
        """Check if any triggers are firing on current bar."""
        triggers = []
        if bar.get('trigger_imb_explosion', False):
            triggers.append('imb_explosion')
        if bar.get('trigger_range_break', False):
            triggers.append('range_break')
        if bar.get('trigger_delta_persist', False):
            triggers.append('delta_persist')
        if bar.get('trigger_vol_accel', False):
            triggers.append('vol_accel')
        if bar.get('trigger_vol_expansion', False):
            triggers.append('vol_expansion')

        return len(triggers) >= self.cfg.min_triggers, triggers


# ════════════════════════════════════════════════════════════
# Adaptive Exit Manager
# ════════════════════════════════════════════════════════════

class AdaptiveExitManager:
    """Manages adaptive exits for an open position."""

    def __init__(self, config: ExitConfig):
        self.cfg = config
        self.bars_held: int = 0
        self.entry_price: float = 0
        self.risk_per_unit: float = 0
        self.partial_taken: bool = False
        self.remaining_fraction: float = 1.0
        self.best_price: float = 0
        self.vol_trail_stop: float = 0
        self.struct_trail_stop: float = 0

    def reset(self, entry_price: float, risk_per_unit: float):
        """Reset for new trade."""
        self.bars_held = 0
        self.entry_price = entry_price
        self.risk_per_unit = risk_per_unit
        self.partial_taken = False
        self.remaining_fraction = 1.0
        self.best_price = entry_price
        self.vol_trail_stop = entry_price - self.cfg.initial_stop_atr * risk_per_unit / self.cfg.initial_stop_atr
        self.struct_trail_stop = 0

    def evaluate(self, bar: pd.Series, position: Position) -> dict | None:
        """
        Evaluate exit conditions. Returns action dict or None.
        """
        self.bars_held += 1
        current_price = bar['close']
        atr = bar.get('atr_5m', self.risk_per_unit / self.cfg.initial_stop_atr)

        # Track best price
        if current_price > self.best_price:
            self.best_price = current_price

        current_r = (current_price - self.entry_price) / self.risk_per_unit if self.risk_per_unit > 0 else 0

        # ── 1. Partials + runner ──
        if not self.partial_taken and current_r >= self.cfg.partial_r:
            self.partial_taken = True
            self.remaining_fraction = 1.0 - self.cfg.partial_fraction
            return {
                'action': 'partial_close',
                'fraction': self.cfg.partial_fraction,
                'exit_reason': f'partial_{self.cfg.partial_r:.0f}R',
                'metadata': {'r_at_partial': current_r},
            }

        # ── 2. Volatility trail ──
        vol_trail_distance = max(
            atr * self.cfg.vol_trail_atr_mult,
            atr * self.cfg.vol_trail_min_atr_mult
        )
        new_vol_trail = current_price - vol_trail_distance
        if new_vol_trail > self.vol_trail_stop:
            self.vol_trail_stop = new_vol_trail

        if bar['low'] <= self.vol_trail_stop and self.vol_trail_stop > self.entry_price:
            return {
                'action': 'close',
                'exit_reason': 'vol_trail',
                'metadata': {'r_at_exit': current_r, 'bars_held': self.bars_held},
            }

        # ── 3. Structure trail ──
        swing_low = bar.get('swing_low', 0)
        if swing_low > 0:
            struct_stop = swing_low * (1 - self.cfg.struct_buffer_pct / 100)
            if struct_stop > self.struct_trail_stop:
                self.struct_trail_stop = struct_stop

        if self.struct_trail_stop > 0 and bar['low'] <= self.struct_trail_stop and self.struct_trail_stop > self.entry_price:
            return {
                'action': 'close',
                'exit_reason': 'struct_trail',
                'metadata': {'r_at_exit': current_r, 'bars_held': self.bars_held},
            }

        # ── 4. Expansion decay ──
        if self.bars_held > self.cfg.decay_lookback and current_r > 0.5:
            # Check if momentum is fading
            recent_returns = bar.get('returns_6bar', 0)
            peak_r = (self.best_price - self.entry_price) / self.risk_per_unit if self.risk_per_unit > 0 else 0
            decay_ratio = current_r / peak_r if peak_r > 0 else 1.0

            if decay_ratio < (1 - self.cfg.decay_threshold):
                return {
                    'action': 'close',
                    'exit_reason': 'expansion_decay',
                    'metadata': {'peak_r': peak_r, 'current_r': current_r, 'decay': 1 - decay_ratio},
                }

        # ── 5. Time stop ──
        if self.bars_held >= self.cfg.max_hold_bars:
            return {
                'action': 'close',
                'exit_reason': 'time_stop',
                'metadata': {'r_at_exit': current_r, 'bars_held': self.bars_held},
            }

        return None


# ════════════════════════════════════════════════════════════
# Main 5m Execution Signal
# ════════════════════════════════════════════════════════════

class MultiTFExecutionSignal(SignalGenerator):
    """
    Multi-timeframe execution signal.

    Context layer (daily/1h): RegimeClassifier determines active regimes.
    Execution layer (5m): EntryTriggers detect precise entry points within regime windows.
    Exit layer (5m): AdaptiveExitManager handles trailing, partials, decay.

    Usage:
        # 1. Classify regimes on daily
        classifier = RegimeClassifier()
        daily = classifier.classify(daily_df)

        # 2. Prepare 5m data with triggers
        signal = MultiTFExecutionSignal(config)
        df_5m = signal.prepare(df_5m, daily)

        # 3. Run backtest on 5m
        engine.run(df_5m, signal.evaluate)
    """

    def __init__(self, config: ExecutionConfig | None = None):
        self.config = config or ExecutionConfig()
        self.triggers = EntryTriggers(self.config.entry)
        self.exit_mgr = AdaptiveExitManager(self.config.exit)
        self._cooldown_counter: int = 0
        self._in_trade: bool = False

    def name(self) -> str:
        return "multi_tf_execution"

    def prepare(self, df_5m: pd.DataFrame, df_context: pd.DataFrame) -> pd.DataFrame:
        """
        Prepare 5m data by:
        1. Computing entry triggers on 5m
        2. Merging regime context from daily/1h
        3. Adding swing low for structure trail

        Args:
            df_5m: 5-minute OHLCV DataFrame
            df_context: Daily/1h DataFrame with regime columns (from RegimeClassifier)
        """
        # Compute 5m triggers
        df_5m = self.triggers.compute_triggers(df_5m)

        # Merge regime context (forward-fill daily regimes to 5m)
        regime_cols = [c for c in df_context.columns if c.startswith('regime_') or c == 'primary_regime'
                       or c in ['liq_strength', 'oi_expansion_rate', 'compression_pctl', 'trend_strength']]

        if 'dt' in df_5m.columns and 'dt' in df_context.columns:
            context_subset = df_context[['dt'] + regime_cols].copy()
            df_5m = pd.merge_asof(
                df_5m.sort_values('dt'),
                context_subset.sort_values('dt'),
                on='dt',
                direction='backward',  # Use most recent regime
            )
        elif 'timestamp' in df_5m.columns and 'timestamp' in df_context.columns:
            context_subset = df_context[['timestamp'] + regime_cols].copy()
            df_5m = pd.merge_asof(
                df_5m.sort_values('timestamp'),
                context_subset.sort_values('timestamp'),
                on='timestamp',
                direction='backward',
            )

        # Active regime flag (any allowed regime is active)
        df_5m['regime_active'] = False
        for regime in self.config.allowed_regimes:
            col = f'regime_{regime.name.lower()}'
            if col in df_5m.columns:
                df_5m['regime_active'] = df_5m['regime_active'] | df_5m[col].fillna(False)

        # Swing low for structure trail
        df_5m['swing_low'] = df_5m['low'].rolling(
            self.config.exit.struct_lookback, min_periods=3
        ).min()

        # Returns for decay detection
        df_5m['returns_6bar'] = df_5m['close'].pct_change(self.config.exit.decay_lookback)

        # Entry signal: regime active AND triggers firing
        df_5m['entry_signal'] = df_5m['regime_active'] & df_5m['any_trigger']

        active_count = df_5m['regime_active'].sum()
        signal_count = df_5m['entry_signal'].sum()
        logger.info(f"5m preparation: {len(df_5m)} bars")
        logger.info(f"  Regime active: {active_count} ({active_count/len(df_5m)*100:.1f}%)")
        logger.info(f"  Entry signals: {signal_count} ({signal_count/len(df_5m)*100:.1f}%)")

        return df_5m

    def evaluate(
        self,
        bar: pd.Series,
        position: Optional[Position],
        context: dict,
    ) -> dict | None:
        """Evaluate signal on a 5m bar."""

        # ── EXIT LOGIC ──
        if position is not None:
            self._in_trade = True
            exit_action = self.exit_mgr.evaluate(bar, position)

            if exit_action:
                # Handle partial close (engine doesn't natively support this,
                # so we convert to full close with metadata)
                if exit_action.get('action') == 'partial_close':
                    exit_action['action'] = 'close'
                    exit_action['exit_reason'] = exit_action.get('exit_reason', 'partial')
                self._in_trade = False
                self._cooldown_counter = self.config.entry.cooldown_bars
                return exit_action

            return None

        # ── COOLDOWN ──
        if self._cooldown_counter > 0:
            self._cooldown_counter -= 1
            return None

        # ── ENTRY LOGIC ──
        if not bar.get('entry_signal', False):
            return None

        # Verify triggers are actually firing
        triggered, trigger_names = self.triggers.check(bar)
        if not triggered:
            return None

        # Calculate position sizing
        atr = bar.get('atr_5m', 0)
        if pd.isna(atr) or atr <= 0:
            return None

        entry_price = bar['close']
        stop_distance = atr * self.config.exit.initial_stop_atr
        stop_price = entry_price - stop_distance
        risk_per_unit = stop_distance

        capital = context.get('capital', 10_000)
        risk_amount = capital * self.config.risk_fraction
        size = risk_amount / risk_per_unit if risk_per_unit > 0 else 0

        if size <= 0:
            return None

        # Initialize exit manager
        self.exit_mgr.reset(entry_price, risk_per_unit)
        self._in_trade = True

        primary_regime = bar.get('primary_regime', 'unknown')

        return {
            'action': 'buy',
            'size': size,
            'stop_loss': stop_price,
            'metadata': {
                'signal': self.name(),
                'triggers': trigger_names,
                'trigger_count': len(trigger_names),
                'regime': primary_regime,
                'liq_strength': bar.get('liq_strength', 0),
                'atr_5m': atr,
                'risk_per_unit': risk_per_unit,
            },
        }

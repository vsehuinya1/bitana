"""
Liquidation Cascade Signal — Long Entry.

THESIS:
After extreme daily liquidation events (>rolling P90), SOLUSDT shows
structural continuation to the upside. This is driven by:
1. Forced position closure creating temporary price dislocations
2. SOL's structural long bias (1.48x asymmetry at >5% hourly moves)
3. OI rebuilds aggressively on up days (+1.12% vs +0.06% on down)
4. All 4 price/OI states revert to positive within 3 days

ENTRY: Long at next bar open after a cascade event is detected.
STOP: 2x ATR below entry.
TARGET: Time-based exit after hold_bars, or trailing stop.

This signal requires DAILY liquidation data merged into the bar context.
"""
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
from loguru import logger

from research.engine.signals import SignalGenerator
from research.engine.backtest import Position
from research.engine.risk import fixed_fractional_size, atr_stop


@dataclass
class CascadeConfig:
    """Configuration for the liquidation cascade signal."""
    # Detection
    lookback: int = 90              # Rolling window for P90 threshold
    percentile: float = 0.90        # Liquidation spike threshold
    min_lookback: int = 30          # Minimum bars before threshold is valid

    # Risk
    atr_stop_mult: float = 2.0      # Stop = entry - N * ATR
    risk_fraction: float = 0.01     # Risk 1% of capital per trade
    atr_period: int = 14            # ATR lookback

    # Exit
    hold_bars: int = 5              # Max bars to hold (daily)
    use_trailing: bool = True       # Enable trailing stop
    trail_atr_mult: float = 2.5     # Trailing stop distance in ATR
    trail_activation_r: float = 1.0 # Activate trailing after 1R profit

    # Filters
    require_oi_drop: bool = False   # If True, only take trades where OI dropped (actual liquidation, not just volume)
    min_cascade_ratio: float = 1.0  # Minimum ratio of liq to P90 threshold (1.0 = just above)


class LiquidationCascadeSignal(SignalGenerator):
    """
    Liquidation Cascade -> Long Entry Signal.

    Usage with BacktestEngine:
        signal = LiquidationCascadeSignal(config)
        signal.prepare(daily_df)  # Pre-compute cascade events on daily
        engine.run(daily_df, signal.evaluate)

    The signal operates on DAILY bars that must contain:
        - 'total_liq' or 'long_liquidations' + 'short_liquidations'
        - 'atr_14' (or similar ATR column)
        - Standard OHLCV columns
    """

    def __init__(self, config: CascadeConfig | None = None):
        self.config = config or CascadeConfig()
        self._cascade_flags: pd.Series | None = None
        self._cascade_threshold: pd.Series | None = None
        self._bars_in_trade: int = 0
        self._entry_atr: float = 0
        self._entry_r: float = 0

    def name(self) -> str:
        return "liquidation_cascade_long"

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Pre-compute cascade detection on the full DataFrame.

        Must be called before running the backtest. Adds columns:
        - 'total_liq': combined long + short liquidations
        - 'liq_p90': rolling P90 threshold
        - 'is_cascade': True on days where liq > threshold
        - 'cascade_strength': ratio of liq to threshold

        Args:
            df: Daily DataFrame with liquidation + OHLCV + ATR data

        Returns: DataFrame with cascade columns added
        """
        df = df.copy()
        cfg = self.config

        # Ensure total liquidation column
        if 'total_liq' not in df.columns:
            if 'long_liquidations' in df.columns and 'short_liquidations' in df.columns:
                df['total_liq'] = df['long_liquidations'] + df['short_liquidations']
            else:
                logger.error("No liquidation columns found")
                return df

        # Ensure ATR
        if f'atr_{cfg.atr_period}' not in df.columns:
            df = self._compute_atr(df, cfg.atr_period)

        # Rolling P90 threshold
        df['liq_p90'] = df['total_liq'].rolling(
            cfg.lookback, min_periods=cfg.min_lookback
        ).quantile(cfg.percentile)

        # Cascade detection
        df['is_cascade'] = (
            (df['total_liq'] > df['liq_p90']) &
            (df['liq_p90'] > 0)
        )

        # Cascade strength (how far above threshold)
        df['cascade_strength'] = df['total_liq'] / df['liq_p90'].replace(0, np.nan)
        df['cascade_strength'] = df['cascade_strength'].fillna(0)

        # Apply minimum ratio filter
        if cfg.min_cascade_ratio > 1.0:
            df['is_cascade'] = df['is_cascade'] & (df['cascade_strength'] >= cfg.min_cascade_ratio)

        # OI filter: if required, only trigger when OI dropped (real liquidation)
        if cfg.require_oi_drop and 'open_interest' in df.columns:
            oi_change = df['open_interest'].pct_change()
            df['is_cascade'] = df['is_cascade'] & (oi_change < 0)

        # Signal: entry on NEXT bar after cascade (shift forward)
        df['cascade_entry_signal'] = df['is_cascade'].shift(1).fillna(False)

        cascade_count = df['is_cascade'].sum()
        logger.info(f"Cascade detection: {cascade_count} events in {len(df)} bars "
                    f"({cascade_count / len(df) * 100:.1f}%)")

        self._cascade_flags = df['cascade_entry_signal']
        return df

    def evaluate(
        self,
        bar: pd.Series,
        position: Optional[Position],
        context: dict,
    ) -> dict | None:
        """
        Evaluate the cascade signal on current bar.

        This handles both entry and exit logic.
        """
        cfg = self.config
        atr_col = f'atr_{cfg.atr_period}'
        atr = bar.get(atr_col, 0)

        # ── EXIT LOGIC ──
        if position is not None:
            self._bars_in_trade += 1

            # Time-based exit
            if self._bars_in_trade >= cfg.hold_bars:
                return {
                    'action': 'close',
                    'exit_reason': f'time_exit_{cfg.hold_bars}d',
                }

            # Trailing stop activation
            if cfg.use_trailing and self._entry_atr > 0:
                current_r = (bar['close'] - position.entry_price) / (self._entry_atr * cfg.atr_stop_mult)
                if current_r >= cfg.trail_activation_r:
                    trail_distance = self._entry_atr * cfg.trail_atr_mult
                    # Update trailing stop on position
                    new_trail = bar['close'] - trail_distance
                    if position.trailing_stop is None or new_trail > position.trailing_stop:
                        position.trailing_stop = new_trail
                        position.trailing_distance = trail_distance

            return None

        # ── ENTRY LOGIC ──
        # Check for cascade entry signal
        if not bar.get('cascade_entry_signal', False):
            return None

        if pd.isna(atr) or atr <= 0:
            return None

        entry_price = bar['close']  # Will be executed at open of next bar by engine
        stop_price = atr_stop(entry_price, atr, cfg.atr_stop_mult, side='long')
        risk_per_unit = entry_price - stop_price

        if risk_per_unit <= 0:
            return None

        # Size calculation
        capital = context.get('capital', 10_000)
        size = fixed_fractional_size(capital, cfg.risk_fraction, entry_price, stop_price)

        if size <= 0:
            return None

        # Store for exit management
        self._bars_in_trade = 0
        self._entry_atr = atr
        self._entry_r = risk_per_unit

        cascade_str = bar.get('cascade_strength', 0)
        total_liq = bar.get('total_liq', 0)

        return {
            'action': 'buy',
            'size': size,
            'stop_loss': stop_price,
            'trailing_distance': atr * cfg.trail_atr_mult if cfg.use_trailing else None,
            'metadata': {
                'signal': self.name(),
                'cascade_strength': cascade_str,
                'total_liq': total_liq,
                'atr_at_entry': atr,
                'risk_per_unit': risk_per_unit,
            },
        }

    @staticmethod
    def _compute_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """Compute ATR if not present."""
        df = df.copy()
        prev_close = df['close'].shift(1)
        tr = pd.concat([
            df['high'] - df['low'],
            (df['high'] - prev_close).abs(),
            (df['low'] - prev_close).abs(),
        ], axis=1).max(axis=1)
        df[f'atr_{period}'] = tr.ewm(span=period, adjust=False).mean()
        return df

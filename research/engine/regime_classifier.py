"""
Regime Classifier — Daily/1h context layer.

Classifies the current market state into one or more active regimes.
These regimes serve as context/filter for 5m trade execution.

Regimes:
1. LIQUIDATION_CLUSTER: Extreme daily liquidation spike → expect continuation
2. OI_EXPANSION: OI rising + price rising (long_build) → momentum continuation
3. COMPRESSION: ATR squeeze → expect breakout
4. TREND: Sustained directional displacement → pullback entries
"""
import pandas as pd
import numpy as np
from dataclasses import dataclass
from enum import Enum, auto
from loguru import logger


class Regime(Enum):
    """Active regime types."""
    LIQUIDATION_CLUSTER = auto()
    OI_EXPANSION = auto()
    COMPRESSION = auto()
    TREND_UP = auto()
    TREND_DOWN = auto()
    NEUTRAL = auto()


@dataclass
class RegimeState:
    """Current regime state with metadata."""
    active_regimes: list[Regime]
    liquidation_strength: float = 0.0   # How far above P90
    oi_expansion_rate: float = 0.0      # OI change rate
    compression_pctl: float = 50.0      # ATR compression percentile
    trend_strength: float = 0.0         # Directional strength
    trend_direction: int = 0            # +1 up, -1 down, 0 neutral
    timestamp: int = 0


@dataclass
class RegimeConfig:
    """Configuration for regime classification."""
    # Liquidation cluster
    liq_lookback: int = 90          # Rolling window for P90
    liq_percentile: float = 0.90   # Threshold percentile
    liq_min_lookback: int = 30     # Min bars for valid threshold
    liq_window: int = 2            # How many bars the regime stays active after event

    # OI expansion
    oi_lookback: int = 5           # Bars to measure OI change
    oi_min_expansion: float = 0.03 # Min 3% OI increase over lookback
    oi_price_confirm: bool = True  # Require price also rising

    # Compression
    atr_period: int = 14
    compression_lookback: int = 100   # Percentile ranking window
    compression_threshold: float = 15 # Below this pctl = compressed
    bbw_period: int = 20

    # Trend
    trend_period: int = 50         # Displacement period
    trend_ema_fast: int = 20       # Fast EMA
    trend_ema_slow: int = 50       # Slow EMA
    trend_min_displacement: float = 0.10  # Min 10% displacement for trend


class RegimeClassifier:
    """
    Multi-regime classifier operating on daily/1h data.

    Usage:
        classifier = RegimeClassifier(config)
        df = classifier.classify(df)  # Adds regime columns
        state = classifier.get_state(bar)  # Get current regime for a bar
    """

    def __init__(self, config: RegimeConfig | None = None):
        self.config = config or RegimeConfig()

    def classify(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Classify all bars into regimes. Adds regime columns to DataFrame.

        Requires columns: open, high, low, close, volume
        Optional columns: total_liq, open_interest, long_liquidations, short_liquidations
        """
        df = df.copy()
        cfg = self.config

        # Ensure ATR
        if f'atr_{cfg.atr_period}' not in df.columns:
            df = self._compute_atr(df, cfg.atr_period)

        # ── 1. Liquidation Cluster ──
        df = self._classify_liquidation(df)

        # ── 2. OI Expansion ──
        df = self._classify_oi_expansion(df)

        # ── 3. Compression ──
        df = self._classify_compression(df)

        # ── 4. Trend ──
        df = self._classify_trend(df)

        # ── Combined regime column ──
        df['regime_count'] = (
            df['regime_liq_cluster'].astype(int) +
            df['regime_oi_expansion'].astype(int) +
            df['regime_compression'].astype(int) +
            df['regime_trend_up'].astype(int)
        )

        # Summary regime label (primary)
        conditions = [
            df['regime_liq_cluster'],
            df['regime_oi_expansion'],
            df['regime_compression'],
            df['regime_trend_up'],
        ]
        labels = ['liq_cluster', 'oi_expansion', 'compression', 'trend_up']
        df['primary_regime'] = 'neutral'
        # Priority: liquidation > OI expansion > compression > trend
        for cond, label in reversed(list(zip(conditions, labels))):
            df.loc[cond, 'primary_regime'] = label

        self._log_regime_stats(df)
        return df

    def get_state(self, bar: pd.Series) -> RegimeState:
        """Get the RegimeState for a single bar."""
        regimes = []

        if bar.get('regime_liq_cluster', False):
            regimes.append(Regime.LIQUIDATION_CLUSTER)
        if bar.get('regime_oi_expansion', False):
            regimes.append(Regime.OI_EXPANSION)
        if bar.get('regime_compression', False):
            regimes.append(Regime.COMPRESSION)
        if bar.get('regime_trend_up', False):
            regimes.append(Regime.TREND_UP)
        if bar.get('regime_trend_down', False):
            regimes.append(Regime.TREND_DOWN)

        if not regimes:
            regimes.append(Regime.NEUTRAL)

        return RegimeState(
            active_regimes=regimes,
            liquidation_strength=bar.get('liq_strength', 0),
            oi_expansion_rate=bar.get('oi_expansion_rate', 0),
            compression_pctl=bar.get('compression_pctl', 50),
            trend_strength=bar.get('trend_strength', 0),
            trend_direction=int(bar.get('trend_direction', 0)),
            timestamp=int(bar.get('timestamp', 0)),
        )

    # ── Private classification methods ──

    def _classify_liquidation(self, df: pd.DataFrame) -> pd.DataFrame:
        """Detect liquidation cluster regime."""
        cfg = self.config

        if 'total_liq' not in df.columns:
            if 'long_liquidations' in df.columns:
                df['total_liq'] = df['long_liquidations'].fillna(0) + df['short_liquidations'].fillna(0)
            else:
                df['regime_liq_cluster'] = False
                df['liq_strength'] = 0.0
                return df

        df['liq_p90'] = df['total_liq'].rolling(
            cfg.liq_lookback, min_periods=cfg.liq_min_lookback
        ).quantile(cfg.liq_percentile)

        df['liq_spike'] = (df['total_liq'] > df['liq_p90']) & (df['liq_p90'] > 0)
        df['liq_strength'] = (df['total_liq'] / df['liq_p90'].replace(0, np.nan)).fillna(0)

        # Regime stays active for N bars after spike
        df['regime_liq_cluster'] = False
        for i in range(cfg.liq_window + 1):
            df['regime_liq_cluster'] = df['regime_liq_cluster'] | df['liq_spike'].shift(i).fillna(False)

        return df

    def _classify_oi_expansion(self, df: pd.DataFrame) -> pd.DataFrame:
        """Detect OI expansion regime (new money entering in direction of price)."""
        cfg = self.config

        if 'open_interest' not in df.columns:
            df['regime_oi_expansion'] = False
            df['oi_expansion_rate'] = 0.0
            return df

        oi_change = df['open_interest'].pct_change(cfg.oi_lookback)
        price_change = df['close'].pct_change(cfg.oi_lookback)

        df['oi_expansion_rate'] = oi_change

        if cfg.oi_price_confirm:
            # OI rising AND price rising = long build
            df['regime_oi_expansion'] = (
                (oi_change > cfg.oi_min_expansion) &
                (price_change > 0)
            )
        else:
            df['regime_oi_expansion'] = (oi_change > cfg.oi_min_expansion)

        return df

    def _classify_compression(self, df: pd.DataFrame) -> pd.DataFrame:
        """Detect volatility compression regime."""
        cfg = self.config
        atr_col = f'atr_{cfg.atr_period}'

        if atr_col not in df.columns:
            df['regime_compression'] = False
            df['compression_pctl'] = 50.0
            return df

        # Percentile rank of ATR over lookback
        def pctl_rank(series, lookback):
            return series.rolling(lookback, min_periods=lookback // 2).apply(
                lambda x: pd.Series(x).rank(pct=True).iloc[-1] * 100,
                raw=False
            )

        df['compression_pctl'] = pctl_rank(df[atr_col], cfg.compression_lookback)
        df['regime_compression'] = df['compression_pctl'] < cfg.compression_threshold

        return df

    def _classify_trend(self, df: pd.DataFrame) -> pd.DataFrame:
        """Detect trend regime using EMA cross + displacement."""
        cfg = self.config

        ema_fast = df['close'].ewm(span=cfg.trend_ema_fast, adjust=False).mean()
        ema_slow = df['close'].ewm(span=cfg.trend_ema_slow, adjust=False).mean()

        # Displacement: how far price has moved from slow EMA
        displacement = (df['close'] - ema_slow) / ema_slow
        df['trend_strength'] = displacement.abs()
        df['trend_direction'] = np.sign(displacement)

        # Trend up: fast > slow AND displacement > threshold
        df['regime_trend_up'] = (
            (ema_fast > ema_slow) &
            (displacement > cfg.trend_min_displacement)
        )

        # Trend down: fast < slow AND displacement < -threshold
        df['regime_trend_down'] = (
            (ema_fast < ema_slow) &
            (displacement < -cfg.trend_min_displacement)
        )

        return df

    @staticmethod
    def _compute_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        df = df.copy()
        prev_close = df['close'].shift(1)
        tr = pd.concat([
            df['high'] - df['low'],
            (df['high'] - prev_close).abs(),
            (df['low'] - prev_close).abs(),
        ], axis=1).max(axis=1)
        df[f'atr_{period}'] = tr.ewm(span=period, adjust=False).mean()
        return df

    def _log_regime_stats(self, df: pd.DataFrame):
        n = len(df)
        liq = df['regime_liq_cluster'].sum()
        oi = df['regime_oi_expansion'].sum()
        comp = df['regime_compression'].sum()
        trend = df['regime_trend_up'].sum()
        neutral = (df['primary_regime'] == 'neutral').sum()

        logger.info(f"Regime classification ({n} bars):")
        logger.info(f"  Liq cluster:  {liq:4d} ({liq/n*100:5.1f}%)")
        logger.info(f"  OI expansion: {oi:4d} ({oi/n*100:5.1f}%)")
        logger.info(f"  Compression:  {comp:4d} ({comp/n*100:5.1f}%)")
        logger.info(f"  Trend up:     {trend:4d} ({trend/n*100:5.1f}%)")
        logger.info(f"  Neutral:      {neutral:4d} ({neutral/n*100:5.1f}%)")

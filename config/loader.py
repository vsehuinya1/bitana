"""
Bitana Configuration Loader

Pydantic-validated config with:
- YAML loading + .env secrets merge
- SHA-256 checksum logging
- Config version validation
- Per-symbol parameter deep-merge
"""
from __future__ import annotations

import hashlib
import copy
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

# Expected config version — warn if mismatch
EXPECTED_CONFIG_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

class CompressionConfig(BaseModel):
    atr_period: int = 14
    atr_percentile_threshold: float = 25.0
    atr_lookback: int = 100
    bb_period: int = 20
    bb_std: float = 2.0
    bb_width_percentile_threshold: float = 25.0
    volume_avg_period: int = 20
    min_compression_candles: int = 6
    breakout_volume_multiplier: float = 1.5
    max_wick_body_ratio: float = 3.0
    confirmation_timeout_1m_candles: int = 3


class SessionBurstRule(BaseModel):
    """Per-session burst entry/exit profile (mirrors shadow SHADOW_STRATEGIES winners)."""
    shadow_strategy: str = ""
    side_mode: Literal["follow", "fade"] = "follow"
    pos_imb_only: bool = False
    neg_imb_only: bool = False
    hours: list[int] | None = None
    exclude_weekdays: list[int] | None = None  # 0=Mon .. 6=Sun (UTC bar time)
    min_imb: float = 0.0
    min_cascade_strength: float = 0.0
    min_vol_z: float = 0.0
    min_n_confirms: int = 1
    min_decile: int = 2
    stop_atr: float = 10.0
    tp_atr: float = 3.0
    time_bars: int = 6
    time_exit_only: bool = False
    trail_atr: float | None = None
    trail_trigger_r: float | None = None
    # When set, overrides burst_follow.allowed_btc_regimes for this session only.
    allowed_btc_regimes: list[str] | None = None
    # When set, skip entries whose BTC regime age (4h bars since last flip)
    # exceeds this many bars. None = no age gate.
    max_regime_age_bars: int | None = None


class LiqBurstFollowConfig(BaseModel):
    enabled: bool = True
    min_cascade_strength: float = 0.0
    min_vol_z: float = 0.0
    min_n_confirms: int = 1
    min_decile: int = 2
    pos_imb_only: bool = True
    min_burst_volume_30m: float = 20_000.0
    min_burst_events_30m: int = 3
    dedup_bars: int = 3
    force_order_db_path: str = "storage/force_orders.db"
    force_order_read_only: bool = False
    liq_cache_db_path: str = "storage/v5_forward_test.db"
    stop_atr: float = 10.0
    trail_atr: float = 1.5
    trail_trigger_r: float = 1.0
    time_bars: int = 36
    time_exit_only: bool = True
    sessions: list[str] | None = None
    risk_pct: float = 4.0
    btc_regime_gate_enabled: bool = False
    allowed_btc_regimes: list[str] = Field(default_factory=lambda: ["bear"])
    max_regime_age_bars: int | None = None
    session_rules: dict[str, SessionBurstRule] = Field(default_factory=dict)


class EnginesConfig(BaseModel):
    compression_enabled: bool = True
    squeeze_enabled: bool = True
    burst_follow_enabled: bool = False


class SymbolDefaults(BaseModel):
    compression: CompressionConfig = Field(default_factory=CompressionConfig)
    risk_pct: float = 1.5
    burst_follow: LiqBurstFollowConfig = Field(default_factory=LiqBurstFollowConfig)


class TimeframesConfig(BaseModel):
    regime: str = "15m"
    primary: str = "5m"
    confirmation: str = "1m"


class RiskConfig(BaseModel):
    default_risk_pct: float = 1.5
    reduced_risk_pct: float = 0.75
    drawdown_reduce_threshold: float = 0.15
    drawdown_restore_threshold: float = 0.10
    max_leverage: int = 10
    liquidation_buffer_pct: float = 0.05


class BrakesConfig(BaseModel):
    daily_loss_limit_pct: float = 0.04
    consecutive_loss_threshold: int = 3
    consecutive_loss_reduced_trades: int = 5
    equity_pause_drawdown: float = 0.25
    equity_shutdown_drawdown: float = 0.40


class StopMethodConfig(BaseModel):
    method: str = "structure_or_atr"
    atr_multiplier: float = 1.5
    buffer_pct: float = 0.002


class StopsConfig(BaseModel):
    compression: StopMethodConfig = Field(
        default_factory=lambda: StopMethodConfig(method="structure_or_atr", atr_multiplier=1.5)
    )
    squeeze: StopMethodConfig = Field(
        default_factory=lambda: StopMethodConfig(method="trigger_extreme", buffer_pct=0.002)
    )


class ProfitTakingConfig(BaseModel):
    partial_close_pct: float = 0.50
    partial_close_r: float = 1.5
    trail_atr_multiplier: float = 1.0
    time_stop_r_threshold: float = 1.0
    time_stop_candles: int = 8


class PortfolioConfig(BaseModel):
    max_concurrent_positions: int = 2
    max_per_symbol: int = 1
    max_cluster_positions: int = 0
    cluster_window_minutes: int = 15
    limit_by_engine: bool = False
    btc_priority: bool = True
    correlation_require_independent: bool = True
    correlation_sizing_reduction: float = 0.0


class ExecutionConfig(BaseModel):
    max_spread_bps: float = 15.0
    max_slippage_bps: float = 10.0
    partial_fill_timeout_s: int = 30
    client_order_id_prefix: str = "BITANA"


class ReconciliationConfig(BaseModel):
    interval_s: int = 30
    external_position_stop_atr_mult: float = 2.0


class SessionFilterConfig(BaseModel):
    enabled: bool = False
    allowed_utc_hours: list[int] = Field(default_factory=lambda: list(range(8, 21)))


class BlackoutWindow(BaseModel):
    start: str
    end: str
    reason: str = ""


class RegimeFiltersConfig(BaseModel):
    enabled: bool = True
    min_atr_15m: float = 0.001
    avoid_low_vol_drift: bool = True
    session_filter: SessionFilterConfig = Field(default_factory=SessionFilterConfig)
    blackout_windows: list[BlackoutWindow] = Field(default_factory=list)


class FeesConfig(BaseModel):
    taker_bps: float = 4.0
    maker_bps: float = 2.0
    default_slippage_bps: float = 2.0


class TelegramConfig(BaseModel):
    enabled: bool = True
    flatten_confirm_timeout_s: int = 30
    max_log_lines: int = 50


class HealthConfig(BaseModel):
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 8080


class LoggingConfig(BaseModel):
    level: str = "INFO"
    file: str = "logs/bitana.log"
    trade_file: str = "logs/trades.jsonl"
    max_bytes: int = 52_428_800
    backup_count: int = 10
    retention_days: int = 30


class DataConfig(BaseModel):
    candle_history_limit: int = 500
    rest_candle_check_interval_s: int = 60
    ws_reconnect_max_retries: int = 10
    ws_reconnect_base_delay_s: float = 1.0
    server_time_sync_interval_s: int = 300


class RateLimiterConfig(BaseModel):
    order_weight_per_minute: int = 1200
    data_weight_per_minute: int = 2400
    warn_threshold_pct: float = 0.80


class WatchdogConfig(BaseModel):
    heartbeat_interval_s: int = 30
    max_restart_attempts: int = 5
    restart_backoff_base_s: float = 2.0


class SqueezeConfig(BaseModel):
    enabled: bool = False
    oi_poll_interval_s: int = 15
    oi_drop_threshold_pct: float = 0.02
    velocity_threshold: float = 0.005
    impulse_lookback_candles: int = 5


class DatabaseConfig(BaseModel):
    path: str = "data/bitana.db"


class SymbolsConfig(BaseModel):
    active: list[str] = Field(default_factory=lambda: ["BTCUSDT", "SOLUSDT"])
    defaults: SymbolDefaults = Field(default_factory=SymbolDefaults)
    # Raw per-symbol overrides — processed at load time
    overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Secrets from .env
# ---------------------------------------------------------------------------

class Secrets(BaseSettings):
    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_testnet: bool = True
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


# ---------------------------------------------------------------------------
# Top-level config
# ---------------------------------------------------------------------------

class AppConfig(BaseModel):
    config_version: str = EXPECTED_CONFIG_VERSION
    config_checksum: str = ""
    mode: str = "paper"

    symbols: SymbolsConfig = Field(default_factory=SymbolsConfig)
    timeframes: TimeframesConfig = Field(default_factory=TimeframesConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    brakes: BrakesConfig = Field(default_factory=BrakesConfig)
    stops: StopsConfig = Field(default_factory=StopsConfig)
    profit_taking: ProfitTakingConfig = Field(default_factory=ProfitTakingConfig)
    portfolio: PortfolioConfig = Field(default_factory=PortfolioConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    reconciliation: ReconciliationConfig = Field(default_factory=ReconciliationConfig)
    regime_filters: RegimeFiltersConfig = Field(default_factory=RegimeFiltersConfig)
    fees: FeesConfig = Field(default_factory=FeesConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    health: HealthConfig = Field(default_factory=HealthConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    rate_limiter: RateLimiterConfig = Field(default_factory=RateLimiterConfig)
    watchdog: WatchdogConfig = Field(default_factory=WatchdogConfig)
    squeeze: SqueezeConfig = Field(default_factory=SqueezeConfig)
    engines: EnginesConfig = Field(default_factory=EnginesConfig)
    burst_follow: LiqBurstFollowConfig = Field(default_factory=LiqBurstFollowConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)

    secrets: Secrets = Field(default_factory=Secrets)


# ---------------------------------------------------------------------------
# Resolved per-symbol config
# ---------------------------------------------------------------------------

class ResolvedSymbolConfig(BaseModel):
    """Fully resolved config for a single symbol (defaults + overrides merged)."""
    symbol: str
    compression: CompressionConfig
    risk_pct: float
    burst_follow: LiqBurstFollowConfig


# ---------------------------------------------------------------------------
# Deep merge utility
# ---------------------------------------------------------------------------

def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base, returning new dict."""
    result = copy.deepcopy(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = copy.deepcopy(val)
    return result


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_config(
    config_path: str | Path = "config/settings.yaml",
    env_path: str | Path = ".env",
) -> AppConfig:
    """Load and validate application config.

    Returns AppConfig with:
    - SHA-256 checksum set
    - Per-symbol overrides extracted into symbols.overrides
    - Secrets loaded from .env
    """
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    raw_bytes = config_path.read_bytes()
    checksum = hashlib.sha256(raw_bytes).hexdigest()

    raw = yaml.safe_load(raw_bytes.decode("utf-8"))
    if raw is None:
        raw = {}

    # Extract per-symbol overrides from symbols section
    symbols_section = raw.get("symbols", {})
    overrides: dict[str, dict[str, Any]] = {}
    active = symbols_section.get("active", ["BTCUSDT", "SOLUSDT"])
    for sym in active:
        sym_data = symbols_section.get(sym, {})
        if sym_data:
            overrides[sym] = sym_data

    # Clean symbol keys from raw before Pydantic parse
    symbols_clean = {
        "active": active,
        "defaults": symbols_section.get("defaults", {}),
        "overrides": overrides,
    }
    raw["symbols"] = symbols_clean

    # Load secrets
    load_dotenv(env_path)
    secrets = Secrets()

    raw["secrets"] = secrets.model_dump()
    raw["config_checksum"] = checksum

    config = AppConfig.model_validate(raw)

    # Version check
    if config.config_version != EXPECTED_CONFIG_VERSION:
        import warnings
        warnings.warn(
            f"Config version mismatch: file={config.config_version}, "
            f"expected={EXPECTED_CONFIG_VERSION}. Review config for breaking changes.",
            stacklevel=2,
        )

    return config


def resolve_symbol_config(config: AppConfig, symbol: str) -> ResolvedSymbolConfig:
    """Resolve per-symbol config by merging defaults with symbol overrides."""
    defaults = config.symbols.defaults.model_dump()
    overrides = config.symbols.overrides.get(symbol, {})
    merged = _deep_merge(defaults, overrides)

    # burst_follow: symbol defaults < top-level YAML < per-symbol override
    defaults_bf = config.symbols.defaults.burst_follow.model_dump()
    override_bf = overrides.get("burst_follow", {})
    top_bf = config.burst_follow.model_dump()
    burst_merged = _deep_merge(defaults_bf, top_bf)
    burst_merged = _deep_merge(burst_merged, override_bf)

    return ResolvedSymbolConfig(
        symbol=symbol,
        compression=CompressionConfig.model_validate(merged.get("compression", {})),
        risk_pct=merged.get("risk_pct", config.risk.default_risk_pct),
        burst_follow=LiqBurstFollowConfig.model_validate(burst_merged),
    )

"""
Central configuration for the crypto derivatives research stack.
All constants, symbol lists, timeframe definitions, session windows,
data paths, and cost model defaults live here.
"""
from pathlib import Path
from typing import Dict, List, Tuple

# ──────────────────────────────────────────────
# Paths
# ──────────────────────────────────────────────
RESEARCH_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = RESEARCH_ROOT / "output" / "data"
REPORTS_DIR = RESEARCH_ROOT / "output" / "reports"
PLOTS_DIR = RESEARCH_ROOT / "output" / "plots"

# Create output dirs on import
for _d in [DATA_DIR, REPORTS_DIR, PLOTS_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

# Data subdirectories
OHLCV_DIR = DATA_DIR / "ohlcv"
FUNDING_DIR = DATA_DIR / "funding"
OI_DIR = DATA_DIR / "oi"
LIQUIDATION_DIR = DATA_DIR / "liquidations"
TAKER_DIR = DATA_DIR / "taker"

for _d in [OHLCV_DIR, FUNDING_DIR, OI_DIR, LIQUIDATION_DIR, TAKER_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

# ──────────────────────────────────────────────
# Symbols
# ──────────────────────────────────────────────
PRIMARY_SYMBOLS: List[str] = ["SOLUSDT"]

CONTROL_SYMBOLS: List[str] = ["BTCUSDT", "ETHUSDT"]

SECONDARY_SYMBOLS: List[str] = [
    "XRPUSDT",
    "BNBUSDT",
    "DOGEUSDT",
    "SUIUSDT",
]

ALL_SYMBOLS: List[str] = PRIMARY_SYMBOLS + CONTROL_SYMBOLS + SECONDARY_SYMBOLS

# Coinalyze uses a different symbol format for Binance perpetuals
COINALYZE_SYMBOL_MAP: Dict[str, str] = {
    "SOLUSDT": "SOLUSDT_PERP.A",
    "BTCUSDT": "BTCUSDT_PERP.A",
    "ETHUSDT": "ETHUSDT_PERP.A",
    "XRPUSDT": "XRPUSDT_PERP.A",
    "BNBUSDT": "BNBUSDT_PERP.A",
    "DOGEUSDT": "DOGEUSDT_PERP.A",
    "SUIUSDT": "SUIUSDT_PERP.A",
}

# ──────────────────────────────────────────────
# Timeframes
# ──────────────────────────────────────────────
OHLCV_TIMEFRAMES: List[str] = ["1m", "5m", "15m", "1h"]

# Binance kline interval strings
BINANCE_INTERVAL_MAP: Dict[str, str] = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
}

# Timeframe to milliseconds
TF_TO_MS: Dict[str, int] = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}

# Coinalyze interval mapping
# Coinalyze API uses: 1min, 5min, 15min, 30min, 1hour, 2hour, 4hour, 6hour, 12hour, daily
COINALYZE_INTERVAL_MAP: Dict[str, str] = {
    "1m": "1min",
    "5m": "5min",
    "15m": "15min",
    "1h": "1hour",
    "4h": "4hour",
    "1d": "daily",
}

# ──────────────────────────────────────────────
# Session Definitions (UTC hours)
# ──────────────────────────────────────────────
# Sessions defined as (start_hour_utc, end_hour_utc)
# Wrapping midnight is handled by the session tagger
SessionWindow = Tuple[int, int]

SESSIONS: Dict[str, SessionWindow] = {
    "asia": (0, 8),       # 00:00 - 08:00 UTC
    "london": (7, 16),    # 07:00 - 16:00 UTC
    "ny": (13, 22),       # 13:00 - 22:00 UTC
}

# Overlaps
OVERLAP_WINDOWS: Dict[str, SessionWindow] = {
    "asia_london": (7, 8),    # 07:00 - 08:00 UTC
    "london_ny": (13, 16),    # 13:00 - 16:00 UTC
}

WEEKEND_DAYS: List[int] = [5, 6]  # Saturday=5, Sunday=6

# ──────────────────────────────────────────────
# Cost Model Defaults
# ──────────────────────────────────────────────
TAKER_FEE_BPS: float = 4.0     # 0.04%
MAKER_FEE_BPS: float = 2.0     # 0.02%
DEFAULT_SLIPPAGE_BPS: float = 2.0  # 0.02% default, configurable 1-3

# ──────────────────────────────────────────────
# Risk Model Defaults
# ──────────────────────────────────────────────
RISK_FRACTIONS: List[float] = [0.01, 0.02, 0.05]  # 1%, 2%, 5%

# ──────────────────────────────────────────────
# Binance API
# ──────────────────────────────────────────────
BINANCE_FUTURES_BASE = "https://fapi.binance.com"
BINANCE_KLINES_ENDPOINT = "/fapi/v1/klines"
BINANCE_FUNDING_ENDPOINT = "/fapi/v1/fundingRate"
BINANCE_OI_HIST_ENDPOINT = "/futures/data/openInterestHist"
BINANCE_TAKER_VOL_ENDPOINT = "/futures/data/takerBuySellVol"

BINANCE_KLINES_LIMIT = 1500    # Max per request
BINANCE_FUNDING_LIMIT = 1000   # Max per request

# ──────────────────────────────────────────────
# Coinalyze API
# ──────────────────────────────────────────────
COINALYZE_BASE = "https://api.coinalyze.net/v1"
COINALYZE_OI_HISTORY = "/open-interest-history"
COINALYZE_LIQ_HISTORY = "/liquidation-history"
COINALYZE_MARKETS = "/future-markets"

COINALYZE_RATE_LIMIT = 40      # requests per minute
COINALYZE_RATE_WINDOW = 60     # seconds

# ──────────────────────────────────────────────
# Listing dates (approximate, for data bounds)
# Unix timestamp in ms
# ──────────────────────────────────────────────
SYMBOL_LISTING_MS: Dict[str, int] = {
    "SOLUSDT": 1_599_782_400_000,   # ~Sep 2020
    "BTCUSDT": 1_568_592_000_000,   # ~Sep 2019
    "ETHUSDT": 1_568_592_000_000,   # ~Sep 2019
    "XRPUSDT": 1_578_528_000_000,   # ~Jan 2020
    "BNBUSDT": 1_585_699_200_000,   # ~Apr 2020
    "DOGEUSDT": 1_594_857_600_000,  # ~Jul 2020
    "SUIUSDT": 1_683_244_800_000,   # ~May 2023
}

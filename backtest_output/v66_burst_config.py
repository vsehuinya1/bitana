"""v66 burst continuation — frozen research config."""
from __future__ import annotations

from engines.liq_burst_engine import BurstConfig

V66_SYMBOLS = [
    "NEARUSDT", "ZECUSDT", "ADAUSDT", "WLDUSDT", "UNIUSDT", "NMRUSDT",
    "PENDLEUSDT", "ARBUSDT", "RENDERUSDT", "RUNEUSDT", "FETUSDT", "DOTUSDT",
    "TONUSDT", "SOLUSDT", "1000LUNCUSDT", "ENAUSDT", "1000PEPEUSDT",
    "XRPUSDT", "FILUSDT", "BNBUSDT", "TAOUSDT", "CHZUSDT", "DASHUSDT",
    "QNTUSDT", "ICPUSDT", "XLMUSDT", "APTUSDT", "ETHUSDT",
]

# OOS note: chronological keep bar FAILS (−0.30R OOS on default params).
# Paper track only — architecture is live-honest; params need more research.
V66_CFG = BurstConfig(
    share_min=0.35,
    dir_dom=0.70,
    min_trail_usd=100_000.0,
    stop_atr=3.0,
    hold_bars=96,
    allowed_hours=frozenset(range(24)),
    dedup_hours=4,
)

MIN_TEST_N = 50
PASS_TEST_AVG = 0.0
PASS_FULL_AVG = -0.05

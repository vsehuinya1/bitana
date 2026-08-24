"""v6.5-revert research config — V5 strict entry + v6.2 decile filters + NY session."""
from __future__ import annotations

from dataclasses import replace

import engines.liq_cluster_engine_v5 as eng

# Proven 28 from historical V5 full backtest (+93 to +121R, PF ~2.9).
V5_SYMBOLS = [
    "NEARUSDT", "ZECUSDT", "ADAUSDT", "WLDUSDT", "UNIUSDT", "NMRUSDT",
    "PENDLEUSDT", "ARBUSDT", "RENDERUSDT", "RUNEUSDT", "FETUSDT", "DOTUSDT",
    "TONUSDT", "SOLUSDT", "1000LUNCUSDT", "ENAUSDT", "1000PEPEUSDT",
    "XRPUSDT", "FILUSDT", "BNBUSDT", "TAOUSDT", "CHZUSDT", "DASHUSDT",
    "QNTUSDT", "ICPUSDT", "XLMUSDT", "APTUSDT", "ETHUSDT",
]


def apply_v65_revert() -> None:
    """Patch engine module globals for v6.5-revert replay (research only)."""
    eng.CFG = replace(
        eng.CFG,
        imb_z_threshold=2.0,
        vol_z_threshold=3.0,
        body_strength_min=0.60,
        impulse_min_pct=0.30,
        min_confirmations=4,
    )
    eng.SNIPER_ALLOWED_HOURS = frozenset(range(14, 22))  # NY 14–22 UTC (skip London bleed)
    eng.SNIPER_MAX_ATR_PCT = 1e9  # V5 backtest had no ATR sniper
    eng.SNIPER_MIN_VOL_Z = -1e9  # no v645 flow gate
    eng.SNIPER_MIN_CASCADE = -1.0
    eng.MAX_RISK_PCT = 0.005  # 0.5% vol-target cap per spec


SESSIONS = {
    "ny": frozenset(range(14, 22)),
    "london": frozenset(range(8, 14)),
    "asia": frozenset(range(0, 8)),
}

# Full research universe (57) from pre-v65 config tiers — used for expansion sweeps.
UNIVERSE_57 = [
    "RUNEUSDT", "BLUAIUSDT", "BERAUSDT", "SIRENUSDT", "EDGEUSDT", "MEGAUSDT",
    "KITEUSDT", "ONTUSDT", "KAITOUSDT", "RAVEUSDT", "ENJUSDT", "CRVUSDT",
    "IRYSUSDT", "DEXEUSDT", "WIFUSDT", "ARBUSDT", "APTUSDT", "DOTUSDT",
    "FETUSDT", "TAOUSDT", "NEARUSDT", "SOLUSDT", "XRPUSDT", "PENDLEUSDT",
    "DASHUSDT", "WLDUSDT", "TONUSDT", "CHZUSDT", "QNTUSDT", "NMRUSDT",
    "UNIUSDT", "BNBUSDT", "1000LUNCUSDT", "ZECUSDT", "1000PEPEUSDT",
    "HYPEUSDT", "DOGEUSDT", "SUIUSDT", "GRASSUSDT", "ONDOUSDT", "LINKUSDT",
    "AVAXUSDT", "INJUSDT", "AAVEUSDT", "PENGUUSDT", "LTCUSDT", "TRUMPUSDT",
    "FARTCOINUSDT", "VIRTUALUSDT", "TIAUSDT", "ETHUSDT", "FILUSDT",
    "LDOUSDT", "PYTHUSDT", "SEIUSDT", "1000BONKUSDT", "ENAUSDT",
]


def apply_session(name: str) -> None:
    hours = SESSIONS.get(name, SESSIONS["ny"])
    eng.SNIPER_ALLOWED_HOURS = hours

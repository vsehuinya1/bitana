"""Logging-only signal shadow.

Turns the live (now healthy) candle + liquidation pipe into research samples
WITHOUT touching live equity. On every liquidation-cascade bar it snapshots the
exact features the v6.5 engine sees, flags which nested gate variants WOULD fire,
and tracks forward ATR-normalised returns + MFE/MAE so any rule, stop, target, or
direction can be evaluated offline.

Paper shadow trades (`shadow_trades`) simulate EVERY candidate strategy in parallel
so we can pick what to trade from live-logged P&L — not replay guesses.

Design notes
------------
- Side-effect free: never calls engine.evaluate() (that mutates live state) and
  never acquires the runner's engine lock. on_bar() is fully synchronous — safe
  to call from _on_5m_close, which already holds the lock.
- Restart-safe forward tracking: open snapshots live in the DB and are updated
  in place each bar, so a watchdog restart does not lose accumulated MFE/MAE.
- Variants nest: strict ⊂ all_hours ⊂ confirms3 ⊂ loose. We snapshot every
  cascade-active bar; variant flags on each row let us filter any rule family
  offline without re-running live code.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import numpy as np

from core.logging_setup import get_logger
from engines.liq_cluster_engine_v5 import (
    CFG,
    SNIPER_ALLOWED_HOURS,
    SymbolState,
    _atr,
    _compute_aggression,
    _ema,
    _is_decile_tradable,
    _score_to_decile,
    _z_score,
)

logger = get_logger("signal_shadow")

DB_PATH = Path("storage/signal_shadow.db")

# Forward horizons in 5m bars: 15m, 30m, 1h, 2h, 4h, 8h, 24h.
HORIZONS = (3, 6, 12, 24, 48, 96, 288)
MAX_H = HORIZONS[-1]

# Catch-all candidate floor and de-duplication window (bars).
LOOSE_MIN_CONFIRMS = 2
DEDUP_BARS = 3

SideMode = Literal["fade", "fade_short_only", "follow", "long"]

# Checkpoint bars for per-horizon MAE/MFE snapshots (5m bars).
MAE_CHECKPOINT_BARS: dict[int, str] = {36: "3h", 72: "6h", 144: "12h", 288: "24h"}
PNL_CHECKPOINT_BARS: dict[int, str] = {12: "1h", 24: "2h"}
# Post-exit observation window: keep tracking each closed shadow trade for 24h
# (288 x 5m bars) after its exit so MFE/MAE are not censored at close.
POST_EXIT_BARS = 288

ENTRY_QUALITY_COLUMNS: tuple[tuple[str, str], ...] = (
    ("spread_bps", "REAL"),
    ("book_depth_usd_5bps", "REAL"),
    ("fill_price_next_open", "REAL"),
    ("btc_adx", "REAL"),
    ("btc_regime_age_bars", "INTEGER"),
    ("btc_realized_vol_24h", "REAL"),
    ("symbol_trend_state", "TEXT"),
    ("cluster_breadth", "INTEGER"),
    ("market_liq_flow_usd", "REAL"),
    ("burst_vol_zscore", "REAL"),
    ("entry_lag_bars", "INTEGER"),
    ("oi_delta_30m_pct", "REAL"),
    ("bars_to_mfe_peak", "INTEGER"),
    ("pnl_1h", "REAL"),
    ("pnl_2h", "REAL"),
    ("would_live_accept", "INTEGER"),
    ("cluster_bucket", "TEXT"),
)


def _cluster_bucket(bar_time: datetime, window_min: int = 15) -> str:
    minute = (bar_time.minute // window_min) * window_min
    bt = bar_time.replace(minute=minute, second=0, microsecond=0)
    if bt.tzinfo is None:
        bt = bt.replace(tzinfo=timezone.utc)
    return bt.isoformat()


@dataclass
class ShadowPortfolioConfig:
    """Portfolio caps — disabled by default so shadow logs everything in parallel."""

    max_concurrent: int | None = None
    max_per_symbol_session: int = 1
    max_net_delta: int | None = None  # |open longs - open shorts|
    live_max_concurrent: int = 3
    live_max_per_symbol: int = 1
    live_max_cluster: int = 3


DEFAULT_PORTFOLIO = ShadowPortfolioConfig()


@dataclass
class MarketContext:
    """Regime + execution snapshot supplied by the runner at bar time."""

    btc_trend_state: str | None = None
    btc_distance_from_ema_pct: float | None = None
    btc_adx: float | None = None
    btc_regime_age_bars: int | None = None
    btc_realized_vol_24h: float | None = None
    market_breadth_pct: float | None = None
    funding_rate_btc: float | None = None
    funding_rate_symbol: float | None = None
    symbol_trend_state: str | None = None
    spread_bps: float | None = None
    book_depth_usd_5bps: float | None = None
    cluster_breadth: int | None = None
    market_liq_flow_usd: float | None = None
    oi_delta_30m_pct: float | None = None
    entry_lag_bars: int | None = None
    burst_vol_zscore: float | None = None


@dataclass
class PortfolioSnapshot:
    concurrent_positions_total: int
    concurrent_positions_same_side: int
    net_delta_at_entry: int
    gross_exposure_at_entry: float
    symbols_active_count: int


@dataclass(frozen=True)
class ShadowStrategy:
    name: str
    trigger: Literal["burst", "bar"]
    side_mode: SideMode
    stop_atr: float
    tp_atr: float
    time_bars: int = 6
    sessions: frozenset[str] | None = None
    hours: frozenset[int] | None = None
    min_imb: float = 0.5
    min_burst_vol: float = 20_000.0
    min_burst_events: int = 3
    require_v_confirms3: bool = False
    require_v_strict: bool = False
    require_cascade: bool = False
    require_above_ema_zero: bool = False
    exclude_ny: bool = False
    pos_imb_only: bool = False
    neg_imb_only: bool = False
    time_exit_only: bool = False  # skip TP — pure horizon study
    min_cascade_strength: float = 0.0
    min_vol_z: float | None = None
    min_n_confirms: int = 0
    min_decile: int = 0
    trail_atr: float | None = None
    trail_trigger_r: float | None = None
    limit_entry_atr: float | None = None  # resting limit offset from signal close (ATR)
    limit_entry_max_bars: int = 36  # 5m bars to wait for fill (default 3h)
    scale_in_atr: float | None = None  # one add-on unit at entry ∓ this many ATR (adverse side)
    scale_after_bars: int = 12  # earliest 5m bar the scale leg is eligible (default 1h)
    # Live weekday gate mirror (2026-08-30): set ONLY on strategies that mirror a live
    # burst_follow session arm, using Python weekday() convention 0=Mon..6=Sun — same
    # convention as the live engine gate (liq_burst_follow_engine.py:318). Applied in
    # _maybe_open_shadow_trade to would_live_accept (NOT _matches_strategy — the match
    # path must keep logging all regimes/days for research; WLA is the live-reality flag).
    # Mirrors config/live_burst_ny_asia.yaml session_rules: london [5,6], asia [1,5,6], ny [0,5,6].
    exclude_weekdays: frozenset[int] | None = None


# Quality floor for 3h/6h follow/fade shadow variants (blocks WLD-style noise).
_FOLLOW_QUALITY = dict(
    min_cascade_strength=0.5,
    min_vol_z=0.0,
    min_n_confirms=1,
    min_decile=2,
)


# Every candidate rule family — logged in parallel; pick winners offline.
SHADOW_STRATEGIES: tuple[ShadowStrategy, ...] = (
    # ── Burst / intraday liq (on_intraday_burst) ──
    ShadowStrategy("late_fade", "burst", "fade", 12.0, 3.0, sessions=frozenset({"late"})),
    ShadowStrategy("asia_burst_fade", "burst", "fade", 4.0, 3.0, sessions=frozenset({"asia"})),
    ShadowStrategy("london_burst_fade", "burst", "fade", 4.0, 3.0, sessions=frozenset({"london"})),
    ShadowStrategy("burst_follow", "burst", "follow", 10.0, 3.0,
                   exclude_weekdays=frozenset({5, 6})),  # live london arm: Sat+Sun out
    ShadowStrategy(
        "nony_momentum", "burst", "follow", 10.0, 3.0,
        min_imb=0.9, min_burst_events=10, require_above_ema_zero=True, exclude_ny=True,
    ),
    # ── 3h / 6h follow variants (pos_imb only, no TP — pure time exit) ──
    # These test Nemo's finding: burst follow edge lives at 3-6h, not 30min.
    # pos_imb_only=True means only fire when liq_imbalance > 0 (long-liq dominated = bear pressure).
    ShadowStrategy(
        "follow_3h_all", "burst", "follow", 10.0, 999.0, time_bars=36,
        pos_imb_only=True, time_exit_only=True, **_FOLLOW_QUALITY,
    ),
    ShadowStrategy(
        "follow_6h_all", "burst", "follow", 10.0, 999.0, time_bars=72,
        pos_imb_only=True, time_exit_only=True, **_FOLLOW_QUALITY,
    ),
    ShadowStrategy(
        "follow_3h_tsl_1_5_1", "burst", "follow", 10.0, 999.0, time_bars=36,
        pos_imb_only=True, trail_atr=1.5, trail_trigger_r=1.0, time_exit_only=True,
        **_FOLLOW_QUALITY,
    ),
    ShadowStrategy(
        "follow_3h_tsl_1_0_05", "burst", "follow", 10.0, 999.0, time_bars=36,
        pos_imb_only=True, trail_atr=1.0, trail_trigger_r=0.5, time_exit_only=True,
        **_FOLLOW_QUALITY,
    ),
    ShadowStrategy(
        "follow_3h_asia", "burst", "follow", 10.0, 999.0, time_bars=36,
        sessions=frozenset({"asia"}), pos_imb_only=True, time_exit_only=True,
        **_FOLLOW_QUALITY,
    ),
    ShadowStrategy(
        "follow_6h_asia", "burst", "follow", 10.0, 999.0, time_bars=72,
        sessions=frozenset({"asia"}), pos_imb_only=True, time_exit_only=True,
        **_FOLLOW_QUALITY,
    ),
    ShadowStrategy(
        "follow_3h_london", "burst", "follow", 10.0, 999.0, time_bars=36,
        sessions=frozenset({"london"}), pos_imb_only=True, time_exit_only=True,
        **_FOLLOW_QUALITY,
    ),
    # G0 (Aug 16): 1h/2h time-exit variants of London follow (baseline is 3h).
    ShadowStrategy(
        "follow_1h_london", "burst", "follow", 10.0, 999.0, time_bars=12,
        sessions=frozenset({"london"}), pos_imb_only=True, time_exit_only=True,
        **_FOLLOW_QUALITY,
    ),
    ShadowStrategy(
        "follow_2h_london", "burst", "follow", 10.0, 999.0, time_bars=24,
        sessions=frozenset({"london"}), pos_imb_only=True, time_exit_only=True,
        **_FOLLOW_QUALITY,
    ),
    ShadowStrategy(
        "follow_6h_london", "burst", "follow", 10.0, 999.0, time_bars=72,
        sessions=frozenset({"london"}), pos_imb_only=True, time_exit_only=True,
        **_FOLLOW_QUALITY,
    ),
    # NY hour-filter variants disabled — hour filter killed edge (kept in git history).
    ShadowStrategy(
        "follow_3h_late", "burst", "follow", 10.0, 999.0, time_bars=36,
        sessions=frozenset({"late"}), pos_imb_only=True, time_exit_only=True,
        **_FOLLOW_QUALITY,
    ),
    ShadowStrategy(
        "follow_6h_late", "burst", "follow", 10.0, 999.0, time_bars=72,
        sessions=frozenset({"late"}), pos_imb_only=True, time_exit_only=True,
        **_FOLLOW_QUALITY,
    ),
    # neg_imb fade for Asia/London; pos_imb fade for NY/Late (historical split).
    ShadowStrategy(
        "fade_3h_asia", "burst", "fade", 10.0, 999.0, time_bars=36,
        sessions=frozenset({"asia"}), neg_imb_only=True, time_exit_only=True,
        **_FOLLOW_QUALITY,
    ),
    ShadowStrategy(
        "fade_6h_asia", "burst", "fade", 10.0, 999.0, time_bars=72,
        sessions=frozenset({"asia"}), neg_imb_only=True, time_exit_only=True,
        **_FOLLOW_QUALITY,
    ),
    ShadowStrategy(
        "fade_3h_london", "burst", "fade", 10.0, 999.0, time_bars=36,
        sessions=frozenset({"london"}), neg_imb_only=True, time_exit_only=True,
        **_FOLLOW_QUALITY,
    ),
    ShadowStrategy(
        "fade_6h_london", "burst", "fade", 10.0, 999.0, time_bars=72,
        sessions=frozenset({"london"}), neg_imb_only=True, time_exit_only=True,
        **_FOLLOW_QUALITY,
    ),
    ShadowStrategy(
        "fade_3h_late", "burst", "fade", 10.0, 999.0, time_bars=36,
        sessions=frozenset({"late"}), pos_imb_only=True, time_exit_only=True,
        **_FOLLOW_QUALITY,
    ),
    ShadowStrategy(
        "fade_6h_late", "burst", "fade", 10.0, 999.0, time_bars=72,
        sessions=frozenset({"late"}), pos_imb_only=True, time_exit_only=True,
        **_FOLLOW_QUALITY,
    ),
    # ── Session-direction edges (shadow_signal.db raw analysis, Jun19-Jul6) ──
    # NY long-liq flush: after price dumps on long liquidations, buy the flush.
    # Only stable positive edge across all 3 weeks (4h +0.45, 8h +0.82 ATR).
    # No quality floor: validated on raw |imb|>=0.5 snapshots, not the follow-quality subset.
    ShadowStrategy(
        "ny_flush_buy_4h", "burst", "follow", 10.0, 999.0, time_bars=48,
        sessions=frozenset({"ny"}), min_imb=0.5, pos_imb_only=True, time_exit_only=True,
        exclude_weekdays=frozenset({0, 5, 6}),  # live ny arm: Mon+Sat+Sun out (0=Mon)
    ),
    # G0 (Aug 16): 1h time-exit variant. NOTE: prior "~70% peak by bar 6" claim
    # was disproven (winner mean MFE peak = bar 32; only 3-6% peak within 6 bars).
    # Logging 1h/2h/4h as real strategy variants so pnl_atr is the PRIMARY exit
    # value (unconditional at close), NOT the checkpoint columns (pnl_1h/pnl_2h),
    # which are logger-uptime-gapped and anti-correlated with performance.
    ShadowStrategy(
        "ny_flush_buy_1h", "burst", "follow", 10.0, 999.0, time_bars=12,
        sessions=frozenset({"ny"}), min_imb=0.5, pos_imb_only=True, time_exit_only=True,
    ),
    ShadowStrategy(
        "ny_flush_buy_2h", "burst", "follow", 10.0, 999.0, time_bars=24,
        sessions=frozenset({"ny"}), min_imb=0.5, pos_imb_only=True, time_exit_only=True,
    ),
    # Full-session NY stop ladder (pairs with live ny_flush_buy_4h, not open-window).
    ShadowStrategy(
        "ny_flush_buy_4h_s4", "burst", "follow", 4.0, 999.0, time_bars=48,
        sessions=frozenset({"ny"}), min_imb=0.5, pos_imb_only=True, time_exit_only=True,
    ),
    ShadowStrategy(
        "ny_flush_buy_4h_s6", "burst", "follow", 6.0, 999.0, time_bars=48,
        sessions=frozenset({"ny"}), min_imb=0.5, pos_imb_only=True, time_exit_only=True,
    ),
    ShadowStrategy(
        "ny_flush_buy_4h_s8", "burst", "follow", 8.0, 999.0, time_bars=48,
        sessions=frozenset({"ny"}), min_imb=0.5, pos_imb_only=True, time_exit_only=True,
    ),
    ShadowStrategy(
        "ny_flush_buy_8h", "burst", "follow", 10.0, 999.0, time_bars=96,
        sessions=frozenset({"ny"}), min_imb=0.5, pos_imb_only=True, time_exit_only=True,
    ),
    ShadowStrategy(
        "ny_flush_buy_24h", "burst", "follow", 10.0, 999.0, time_bars=288,
        sessions=frozenset({"ny"}), min_imb=0.5, pos_imb_only=True, time_exit_only=True,
    ),
    # G0 (Aug 21): scale-in variant. Resting add-on unit from bar 12 (1h): if price
    # trades 0.5 ATR adverse of entry, add ONCE at the level (blended avg entry);
    # SL/time anchors stay on the FIRST entry. Paired baseline = ny_flush_buy_4h.
    # Expected scale-fill rate ~14%/entry (v9 Jun 9 replay). Checkpoint Sep 13.
    ShadowStrategy(
        "ny_flush_buy_4h_scalein", "burst", "follow", 10.0, 999.0, time_bars=48,
        sessions=frozenset({"ny"}), min_imb=0.5, pos_imb_only=True, time_exit_only=True,
        scale_in_atr=0.5, scale_after_bars=12,
    ),
    # Asia short-liq squeeze: after price pumps on short liquidations, short the pump (4h only).
    ShadowStrategy(
        "asia_pump_short_4h", "burst", "follow", 10.0, 999.0, time_bars=48,
        sessions=frozenset({"asia"}), min_imb=0.5, neg_imb_only=True, time_exit_only=True,
        exclude_weekdays=frozenset({1, 5, 6}),  # live asia arm: Tue+Sat+Sun out (0=Mon)
    ),
    # G0 (Aug 16): 1h time-exit variant of the Asia pump short.
    ShadowStrategy(
        "asia_pump_short_1h", "burst", "follow", 10.0, 999.0, time_bars=12,
        sessions=frozenset({"asia"}), min_imb=0.5, neg_imb_only=True, time_exit_only=True,
    ),
    ShadowStrategy(
        "asia_pump_short_2h", "burst", "follow", 10.0, 999.0, time_bars=24,
        sessions=frozenset({"asia"}), min_imb=0.5, neg_imb_only=True, time_exit_only=True,
    ),
    ShadowStrategy(
        "asia_pump_short_24h", "burst", "follow", 10.0, 999.0, time_bars=288,
        sessions=frozenset({"asia"}), min_imb=0.5, neg_imb_only=True, time_exit_only=True,
    ),
    # TSL variants: lock profit after +2 ATR MFE, trail 1.5 ATR behind peak.
    ShadowStrategy(
        "ny_flush_buy_4h_tsl", "burst", "follow", 10.0, 999.0, time_bars=48,
        sessions=frozenset({"ny"}), min_imb=0.5, pos_imb_only=True, time_exit_only=True,
        trail_atr=1.5, trail_trigger_r=2.0,
    ),
    # NY-open window only (14–17 UTC): skip late-NY flushes that bled in live shadow.
    ShadowStrategy(
        "ny_flush_buy_4h_open", "burst", "follow", 10.0, 999.0, time_bars=48,
        sessions=frozenset({"ny"}), hours=frozenset({14, 15, 16, 17}),
        min_imb=0.5, pos_imb_only=True, time_exit_only=True,
    ),
    ShadowStrategy(
        "ny_flush_buy_4h_open_tsl", "burst", "follow", 10.0, 999.0, time_bars=48,
        sessions=frozenset({"ny"}), hours=frozenset({14, 15, 16, 17}),
        min_imb=0.5, pos_imb_only=True, time_exit_only=True,
        trail_atr=1.5, trail_trigger_r=2.0,
    ),
    ShadowStrategy(
        "asia_pump_short_4h_tsl", "burst", "follow", 10.0, 999.0, time_bars=48,
        sessions=frozenset({"asia"}), min_imb=0.5, neg_imb_only=True, time_exit_only=True,
        trail_atr=1.5, trail_trigger_r=2.0,
    ),
    # Limit-entry variants: rest 1.5 ATR pullback from burst close, 3h fill window.
    ShadowStrategy(
        "ny_flush_buy_4h_limit15", "burst", "follow", 10.0, 999.0, time_bars=48,
        sessions=frozenset({"ny"}), min_imb=0.5, pos_imb_only=True, time_exit_only=True,
        limit_entry_atr=1.5, limit_entry_max_bars=36,
    ),
    ShadowStrategy(
        "asia_pump_short_4h_limit15", "burst", "follow", 10.0, 999.0, time_bars=48,
        sessions=frozenset({"asia"}), min_imb=0.5, neg_imb_only=True, time_exit_only=True,
        limit_entry_atr=1.5, limit_entry_max_bars=36,
    ),
    # Stop-distance counterfactuals on live books (logging-only).
    ShadowStrategy(
        "asia_pump_short_4h_s4", "burst", "follow", 4.0, 999.0, time_bars=48,
        sessions=frozenset({"asia"}), min_imb=0.5, neg_imb_only=True, time_exit_only=True,
    ),
    ShadowStrategy(
        "asia_pump_short_4h_s6", "burst", "follow", 6.0, 999.0, time_bars=48,
        sessions=frozenset({"asia"}), min_imb=0.5, neg_imb_only=True, time_exit_only=True,
    ),
    ShadowStrategy(
        "asia_pump_short_4h_s8", "burst", "follow", 8.0, 999.0, time_bars=48,
        sessions=frozenset({"asia"}), min_imb=0.5, neg_imb_only=True, time_exit_only=True,
    ),
    ShadowStrategy(
        "ny_flush_buy_4h_open_s4", "burst", "follow", 4.0, 999.0, time_bars=48,
        sessions=frozenset({"ny"}), hours=frozenset({14, 15, 16, 17}),
        min_imb=0.5, pos_imb_only=True, time_exit_only=True,
    ),
    ShadowStrategy(
        "ny_flush_buy_4h_open_s6", "burst", "follow", 6.0, 999.0, time_bars=48,
        sessions=frozenset({"ny"}), hours=frozenset({14, 15, 16, 17}),
        min_imb=0.5, pos_imb_only=True, time_exit_only=True,
    ),
    ShadowStrategy(
        "ny_flush_buy_4h_open_s8", "burst", "follow", 8.0, 999.0, time_bars=48,
        sessions=frozenset({"ny"}), hours=frozenset({14, 15, 16, 17}),
        min_imb=0.5, pos_imb_only=True, time_exit_only=True,
    ),
    # ── Setup / bar-close (on_bar, v_confirms3 snapshot) ──
    ShadowStrategy(
        "setup_fade", "bar", "fade", 4.0, 3.0, require_v_confirms3=True,
    ),
    ShadowStrategy(
        "setup_fade_late", "bar", "fade", 12.0, 3.0,
        require_v_confirms3=True, sessions=frozenset({"late"}),
    ),
    ShadowStrategy(
        "setup_fade_asia", "bar", "fade", 4.0, 3.0,
        require_v_confirms3=True, sessions=frozenset({"asia"}),
    ),
    ShadowStrategy(
        "setup_fade_london", "bar", "fade", 4.0, 3.0,
        require_v_confirms3=True, sessions=frozenset({"london"}),
    ),
    ShadowStrategy(
        "setup_follow", "bar", "follow", 10.0, 3.0, require_v_confirms3=True,
    ),
    ShadowStrategy(
        "v65_strict_long", "bar", "long", 4.0, 3.0,
        require_v_strict=True, require_cascade=True,
    ),
    ShadowStrategy(
        "v65_strict_ny_long", "bar", "long", 4.0, 3.0,
        require_v_strict=True, require_cascade=True,
        sessions=frozenset({"ny"}),
    ),
)

BURST_STRATEGIES = tuple(s for s in SHADOW_STRATEGIES if s.trigger == "burst")
BAR_STRATEGIES = tuple(s for s in SHADOW_STRATEGIES if s.trigger == "bar")
_STRATEGY_BY_NAME = {s.name: s for s in SHADOW_STRATEGIES}


def _session(hour: int) -> str:
    if 0 <= hour < 8:
        return "asia"
    if 8 <= hour < 14:
        return "london"
    if 14 <= hour < 22:
        return "ny"
    return "late"


def _resolve_side(mode: SideMode, imb: float) -> str | None:
    if mode == "long":
        return "LONG"
    if mode == "fade_short_only":
        return "SHORT" if imb > 0 else None
    if mode == "fade":
        return "SHORT" if imb > 0 else "LONG"
    # follow
    return "LONG" if imb > 0 else "SHORT"


def _matches_strategy(
    spec: ShadowStrategy,
    f: dict,
    *,
    imb: float,
    burst_vol: float = 0.0,
    burst_events: int = 0,
    cascade_active: bool = False,
) -> bool:
    if abs(imb) < spec.min_imb:
        return False
    if spec.pos_imb_only and imb <= 0:
        return False
    if spec.neg_imb_only and imb >= 0:
        return False
    if spec.sessions is not None and f["session"] not in spec.sessions:
        return False
    if spec.hours is not None and f["hour"] not in spec.hours:
        return False
    if spec.exclude_ny and f["session"] == "ny":
        return False
    if spec.require_v_confirms3 and not f.get("v_confirms3"):
        return False
    if spec.require_v_strict and not f.get("v_strict"):
        return False
    if spec.require_cascade and not cascade_active:
        return False
    if spec.require_above_ema_zero and f.get("above_ema") != 0:
        return False
    if spec.trigger == "burst":
        if burst_vol < spec.min_burst_vol or burst_events < spec.min_burst_events:
            return False
    if spec.min_cascade_strength > 0 and f.get("cascade_strength", 0.0) < spec.min_cascade_strength:
        return False
    if spec.min_vol_z is not None and f.get("vol_z", 0.0) < spec.min_vol_z:
        return False
    if spec.min_n_confirms > 0 and f.get("n_confirms", 0) < spec.min_n_confirms:
        return False
    if spec.min_decile > 0 and f.get("decile", 0) < spec.min_decile:
        return False
    return True


class SignalShadow:
    def __init__(
        self,
        db_path: Path = DB_PATH,
        portfolio: ShadowPortfolioConfig | None = None,
    ):
        self.portfolio = portfolio or DEFAULT_PORTFOLIO
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_db()
        self._last_snap_bar: dict[str, datetime] = {}
        self._last_burst_bar: dict[str, datetime] = {}
        self._last_setup_bar: dict[str, datetime] = {}
        self._market_ctx = MarketContext()
        self._writes = 0
        # Forward windows of snapshots still open at shutdown last run never get a
        # final bar; mark them so analysis can exclude truncated paths.
        cutoff = (datetime.now(timezone.utc).timestamp() - MAX_H * 300,)
        self.conn.execute(
            "UPDATE snapshots SET status='orphaned' WHERE status='open' AND created_at < ?",
            cutoff,
        )
        self.conn.execute(
            "UPDATE burst_snapshots SET status='orphaned' WHERE status='open' AND created_at < ?",
            cutoff,
        )
        self.conn.execute(
            "UPDATE setup_snapshots SET status='orphaned' WHERE status='open' AND created_at < ?",
            cutoff,
        )
        self.conn.commit()

    def _init_db(self):
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bar_time TEXT, symbol TEXT, hour INT, session TEXT,
                close REAL, atr REAL, atr_pct REAL,
                cascade_strength REAL, liq_direction_imb REAL, ret_5d REAL,
                vol_z REAL, imb_z REAL, breakout_distance_pct REAL,
                body_ratio REAL, impulse_pct REAL, above_ema INT, breakout INT,
                n_confirms INT, decile INT, aggression REAL,
                v_strict INT, v_allhours INT, v_confirms3 INT, v_loose INT,
                status TEXT DEFAULT 'open', bars_tracked INT DEFAULT 0,
                fwd_atr_3 REAL, fwd_atr_6 REAL, fwd_atr_12 REAL,
                fwd_atr_24 REAL, fwd_atr_48 REAL, fwd_atr_96 REAL, fwd_atr_288 REAL,
                mfe_atr REAL DEFAULT 0, mae_atr REAL DEFAULT 0,
                created_at REAL
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sym_status ON snapshots(symbol, status)"
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS burst_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bar_time TEXT, symbol TEXT, hour INT, session TEXT,
                close REAL, atr REAL, atr_pct REAL,
                burst_volume_15m REAL, burst_volume_30m REAL, burst_volume_60m REAL,
                burst_events_15m INT, burst_events_30m INT, burst_events_60m INT,
                long_liq_30m REAL, short_liq_30m REAL, liq_imbalance_30m REAL,
                max_order_usd_30m REAL,
                cascade_strength REAL, liq_direction_imb REAL, ret_5d REAL,
                vol_z REAL, imb_z REAL, breakout_distance_pct REAL,
                body_ratio REAL, impulse_pct REAL, above_ema INT, breakout INT,
                n_confirms INT, decile INT, aggression REAL,
                status TEXT DEFAULT 'open', bars_tracked INT DEFAULT 0,
                fwd_atr_3 REAL, fwd_atr_6 REAL, fwd_atr_12 REAL,
                fwd_atr_24 REAL, fwd_atr_48 REAL, fwd_atr_96 REAL, fwd_atr_288 REAL,
                mfe_atr REAL DEFAULT 0, mae_atr REAL DEFAULT 0,
                created_at REAL
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_burst_sym_status ON burst_snapshots(symbol, status)"
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS setup_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bar_time TEXT, symbol TEXT, hour INT, session TEXT,
                close REAL, atr REAL, atr_pct REAL,
                cascade_strength REAL, liq_direction_imb REAL, ret_5d REAL,
                vol_z REAL, imb_z REAL, breakout_distance_pct REAL,
                body_ratio REAL, impulse_pct REAL, above_ema INT, breakout INT,
                n_confirms INT, decile INT, aggression REAL,
                cascade_active INT,
                v_strict INT, v_allhours INT, v_confirms3 INT, v_loose INT,
                status TEXT DEFAULT 'open', bars_tracked INT DEFAULT 0,
                fwd_atr_3 REAL, fwd_atr_6 REAL, fwd_atr_12 REAL,
                fwd_atr_24 REAL, fwd_atr_48 REAL, fwd_atr_96 REAL, fwd_atr_288 REAL,
                mfe_atr REAL DEFAULT 0, mae_atr REAL DEFAULT 0,
                created_at REAL
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_setup_sym_status ON setup_snapshots(symbol, status)"
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shadow_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy TEXT,
                symbol TEXT,
                side TEXT,
                entry_time TEXT,
                entry_price REAL,
                stop_price REAL,
                tp_price REAL,
                atr REAL,
                status TEXT DEFAULT 'open',
                pnl_atr REAL DEFAULT 0,
                exit_time TEXT,
                exit_price REAL,
                exit_reason TEXT,
                bars_held INT DEFAULT 0,
                created_at REAL,
                session TEXT,
                hour INT,
                decile INT,
                stop_atr REAL,
                tp_atr REAL,
                time_bars INT DEFAULT 6,
                liq_imb REAL,
                burst_vol_30m REAL,
                v_confirms3 INT,
                v_strict INT,
                cascade_active INT,
                trigger TEXT,
                entry_cascade_strength REAL,
                entry_impulse_pct REAL,
                entry_vol_z REAL,
                entry_atr_pct REAL,
                time_exit_only INT DEFAULT 0,
                run_mae_atr REAL DEFAULT 0,
                run_mfe_atr REAL DEFAULT 0,
                mae_3h REAL,
                mae_6h REAL,
                mae_12h REAL,
                mae_24h REAL,
                mfe_3h REAL,
                mfe_6h REAL,
                mfe_12h REAL,
                mfe_24h REAL,
                concurrent_positions_total INT,
                concurrent_positions_same_side INT,
                net_delta_at_entry INT,
                gross_exposure_at_entry REAL,
                symbols_active_count INT,
                btc_trend_state TEXT,
                btc_distance_from_ema_pct REAL,
                market_breadth_pct REAL,
                funding_rate_btc REAL,
                funding_rate_symbol REAL,
                is_weekend INT
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_shadow_trades_status ON shadow_trades(status)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_shadow_trades_strategy ON shadow_trades(strategy, status)"
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shadow_pending_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy TEXT,
                symbol TEXT,
                side TEXT,
                signal_time TEXT,
                signal_price REAL,
                limit_price REAL,
                atr REAL,
                limit_offset_atr REAL,
                max_bars INT,
                bars_waited INT DEFAULT 0,
                status TEXT DEFAULT 'pending',
                session TEXT,
                hour INT,
                decile INT,
                stop_atr REAL,
                tp_atr REAL,
                time_bars INT,
                liq_imb REAL,
                burst_vol_30m REAL,
                v_confirms3 INT,
                v_strict INT,
                cascade_active INT,
                trigger TEXT,
                entry_cascade_strength REAL,
                entry_impulse_pct REAL,
                entry_vol_z REAL,
                entry_atr_pct REAL,
                time_exit_only INT,
                is_weekend INT,
                fill_time TEXT,
                created_at REAL
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_shadow_pending_status "
            "ON shadow_pending_entries(symbol, strategy, status)"
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS setup_r_path (
                setup_id INTEGER NOT NULL,
                bar_num  INTEGER NOT NULL,
                r_close  REAL,
                r_high   REAL,
                r_low    REAL,
                PRIMARY KEY (setup_id, bar_num),
                FOREIGN KEY (setup_id) REFERENCES setup_snapshots(id)
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_setup_r_path_setup ON setup_r_path(setup_id)"
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_r_path (
                trade_id INTEGER,
                phase TEXT,
                bar_num INTEGER,
                r_high REAL,
                r_low REAL,
                r_close REAL,
                PRIMARY KEY (trade_id, phase, bar_num)
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_trade_r_path_trade ON trade_r_path(trade_id)"
        )
        self._migrate_forward_horizons()
        self._migrate_shadow_trades()
        self.conn.commit()

    def _migrate_forward_horizons(self):
        """Ensure forward-return horizon columns exist on all snapshot tables."""
        for table in ("snapshots", "burst_snapshots", "setup_snapshots"):
            existing = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            for h in HORIZONS:
                col = f"fwd_atr_{h}"
                if col not in existing:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} REAL")

    def _migrate_shadow_trades(self):
        """Add metadata columns to pre-v6.5.5 shadow_trades tables."""
        existing = {r[1] for r in self.conn.execute("PRAGMA table_info(shadow_trades)")}
        for col, typ in (
            ("session", "TEXT"),
            ("hour", "INTEGER"),
            ("decile", "INTEGER"),
            ("stop_atr", "REAL"),
            ("tp_atr", "REAL"),
            ("time_bars", "INTEGER DEFAULT 6"),
            ("liq_imb", "REAL"),
            ("burst_vol_30m", "REAL"),
            ("v_confirms3", "INTEGER"),
            ("v_strict", "INTEGER"),
            ("cascade_active", "INTEGER"),
            ("trigger", "TEXT"),
            ("entry_cascade_strength", "REAL"),
            ("entry_impulse_pct", "REAL"),
            ("entry_vol_z", "REAL"),
            ("entry_atr_pct", "REAL"),
            ("time_exit_only", "INTEGER DEFAULT 0"),
            ("run_mae_atr", "REAL DEFAULT 0"),
            ("run_mfe_atr", "REAL DEFAULT 0"),
            ("mae_3h", "REAL"),
            ("mae_6h", "REAL"),
            ("mae_12h", "REAL"),
            ("mae_24h", "REAL"),
            ("mfe_3h", "REAL"),
            ("mfe_6h", "REAL"),
            ("mfe_12h", "REAL"),
            ("mfe_24h", "REAL"),
            ("concurrent_positions_total", "INTEGER"),
            ("concurrent_positions_same_side", "INTEGER"),
            ("net_delta_at_entry", "INTEGER"),
            ("gross_exposure_at_entry", "REAL"),
            ("symbols_active_count", "INTEGER"),
            ("btc_trend_state", "TEXT"),
            ("btc_distance_from_ema_pct", "REAL"),
            ("market_breadth_pct", "REAL"),
            ("funding_rate_btc", "REAL"),
            ("funding_rate_symbol", "REAL"),
            ("is_weekend", "INTEGER"),
            ("post_bars", "INTEGER DEFAULT 0"),
            ("post_mfe_atr", "REAL DEFAULT 0"),
            ("post_mae_atr", "REAL DEFAULT 0"),
            ("scale_filled_price", "REAL"),
        ):
            if col not in existing:
                self.conn.execute(f"ALTER TABLE shadow_trades ADD COLUMN {col} {typ}")
        for col, typ in ENTRY_QUALITY_COLUMNS:
            if col not in existing:
                self.conn.execute(f"ALTER TABLE shadow_trades ADD COLUMN {col} {typ}")
        if "post_bars" not in existing:
            # One-time stamp: closed trades that predate post-exit tracking have
            # no forward window left to observe; mark their window as elapsed so
            # only newly-closed trades are tracked from their real exit bar.
            self.conn.execute(
                "UPDATE shadow_trades SET post_bars=? "
                "WHERE status='closed' AND exit_time IS NOT NULL",
                (POST_EXIT_BARS,),
            )

    def _would_live_accept(
        self,
        strategy: str,
        symbol: str,
        side: str,
        session: str,
        cluster_bucket: str,
    ) -> int:
        """Whether this strategy's independent cap-3 book accepts the entry.

        Parallel shadow variants must never consume one another's slots. Only
        previously accepted positions from the same strategy count here;
        rejected shadow rows remain observational and do not occupy capacity.
        """
        cfg = self.portfolio
        rows = self.conn.execute(
            """
            SELECT symbol, side, session, cluster_bucket
            FROM shadow_trades
            WHERE strategy=? AND status='open' AND would_live_accept=1
            """,
            (strategy,),
        ).fetchall()
        if len(rows) >= cfg.live_max_concurrent:
            return 0
        if sum(1 for r in rows if r["symbol"] == symbol) >= cfg.live_max_per_symbol:
            return 0
        cluster_n = sum(
            1 for r in rows
            if r["side"] == side
            and r["session"] == session
            and r["cluster_bucket"] == cluster_bucket
        )
        if cluster_n >= cfg.live_max_cluster:
            return 0
        return 1

    def _signed_pnl_atr(self, side: str, entry: float, price: float, atr: float) -> float:
        if atr <= 0:
            return 0.0
        if side == "LONG":
            return (price - entry) / atr
        return (entry - price) / atr

    def _features(self, candles_5m: list, st: SymbolState) -> dict | None:
        """Pure feature extraction mirroring engine.evaluate() — no side effects."""
        n_needed = max(CFG.range_lookback, CFG.z_lookback, CFG.ema_period * 3)
        if len(candles_5m) < n_needed:
            return None

        closes = np.array([c.close for c in candles_5m])
        highs = np.array([c.high for c in candles_5m])
        lows = np.array([c.low for c in candles_5m])
        volumes = np.array([c.volume for c in candles_5m])
        bar = candles_5m[-1]

        atr = _atr(highs, lows, closes, CFG.atr_period)
        if atr <= 0:
            return None
        ema = _ema(closes, CFG.ema_period)

        if len(highs) > CFG.range_lookback:
            range_high = float(np.max(highs[-(CFG.range_lookback + 1):-1]))
        else:
            range_high = float(np.max(highs[:-1])) if len(highs) > 1 else highs[0]

        vol_z = _z_score(volumes, CFG.z_lookback)

        taker_buys = np.array([c.taker_buy_volume for c in candles_5m])
        if len(taker_buys) >= CFG.z_lookback and np.any(taker_buys[-CFG.z_lookback:] > 0):
            taker_ratios = taker_buys / np.maximum(volumes, 1e-10)
            imb_z = _z_score(taker_ratios, CFG.z_lookback)
        else:
            imb_z = 0.0

        close = float(closes[-1])
        body = abs(close - bar.open)
        total_range = highs[-1] - lows[-1]
        body_ratio = (body / total_range) if total_range > 0 else 0.0
        impulse_pct = abs(close - bar.open) / bar.open * 100 if bar.open else 0.0
        bd_abs = close - range_high
        bd_pct = (bd_abs / range_high * 100) if range_high > 0 else 0.0

        confirmations = {
            "breakout": close > range_high,
            "imb": imb_z > CFG.imb_z_threshold,
            "vol": vol_z > CFG.vol_z_threshold,
            "body": body_ratio >= CFG.body_strength_min,
            "impulse": impulse_pct >= CFG.impulse_min_pct,
            "momentum": close > ema,
        }
        n_confirms = sum(1 for v in confirmations.values() if v)

        aggression = _compute_aggression(candles_5m)
        decile = _score_to_decile(aggression)

        bar_time = bar.close_time if getattr(bar, "close_time", None) else datetime.now(timezone.utc)
        if bar_time.tzinfo is None:
            bar_time = bar_time.replace(tzinfo=timezone.utc)
        hour = bar_time.hour

        decile_ok = _is_decile_tradable(decile, confirmations)
        bd_ok = bd_pct >= -2.0

        return {
            "bar_time": bar_time,
            "hour": hour,
            "session": _session(hour),
            "close": close,
            "atr": atr,
            "atr_pct": (atr / close * 100) if close > 0 else 0.0,
            "cascade_strength": float(st.cascade_strength),
            "liq_direction_imb": float(st.liq_direction_imb),
            "ret_5d": float(st.ret_5d),
            "vol_z": float(vol_z),
            "imb_z": float(imb_z),
            "breakout_distance_pct": float(bd_pct),
            "body_ratio": float(body_ratio),
            "impulse_pct": float(impulse_pct),
            "above_ema": int(confirmations["momentum"]),
            "breakout": int(confirmations["breakout"]),
            "n_confirms": int(n_confirms),
            "decile": int(decile),
            "aggression": float(aggression),
            "v_strict": int(bd_ok and hour in SNIPER_ALLOWED_HOURS
                            and n_confirms >= CFG.min_confirmations and decile_ok),
            "v_allhours": int(bd_ok and n_confirms >= CFG.min_confirmations and decile_ok),
            "v_confirms3": int(bd_ok and n_confirms >= 3 and decile_ok),
            "v_loose": int(n_confirms >= LOOSE_MIN_CONFIRMS),
        }

    def _advance_open(self, table: str, symbol: str, high: float, low: float, close: float):
        """Advance forward windows for one shadow table."""
        if table not in {"snapshots", "burst_snapshots", "setup_snapshots"}:
            raise ValueError(f"unsupported shadow table: {table}")
        rows = self.conn.execute(
            "SELECT id, close, atr, bars_tracked, mfe_atr, mae_atr "
            f"FROM {table} WHERE symbol=? AND status='open'",
            (symbol,),
        ).fetchall()
        for r in rows:
            atr = r["atr"]
            if atr <= 0:
                self.conn.execute(f"UPDATE {table} SET status='done' WHERE id=?", (r["id"],))
                continue
            entry = r["close"]
            nb = r["bars_tracked"] + 1
            fav = (high - entry) / atr
            adv = (low - entry) / atr
            mfe = max(r["mfe_atr"], fav)
            mae = min(r["mae_atr"], adv)
            sets = ["bars_tracked=?", "mfe_atr=?", "mae_atr=?"]
            vals: list = [nb, mfe, mae]
            if nb in HORIZONS:
                sets.append(f"fwd_atr_{nb}=?")
                vals.append((close - entry) / atr)
            if nb >= MAX_H:
                sets.append("status='done'")
            vals.append(r["id"])
            self.conn.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE id=?", vals)

            if table == "setup_snapshots":
                self.conn.execute(
                    """
                    INSERT OR REPLACE INTO setup_r_path (setup_id, bar_num, r_close, r_high, r_low)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        r["id"],
                        nb,
                        (close - entry) / atr,
                        (high - entry) / atr,
                        (low - entry) / atr,
                    ),
                )
                self._writes += 1

    def _eval_shadow_strategies(
        self,
        strategies: tuple[ShadowStrategy, ...],
        symbol: str,
        f: dict,
        *,
        imb: float,
        cascade_active: bool = False,
        burst_vol: float = 0.0,
        burst_events: int = 0,
    ):
        for spec in strategies:
            if not _matches_strategy(
                spec, f, imb=imb, burst_vol=burst_vol, burst_events=burst_events,
                cascade_active=cascade_active,
            ):
                continue
            side = _resolve_side(spec.side_mode, imb)
            if side is None:
                continue
            self._maybe_open_shadow_trade(
                spec, symbol, f, side,
                imb=imb, cascade_active=cascade_active, burst_vol=burst_vol,
            )

    def set_market_context(self, ctx: MarketContext | None):
        self._market_ctx = ctx or MarketContext()

    def _portfolio_at_entry(
        self, strategy: str, symbol: str, side: str, stop_atr: float,
    ) -> PortfolioSnapshot:
        """Snapshot this strategy's accepted open book before the new entry."""
        rows = self.conn.execute(
            """
            SELECT symbol, side, stop_atr
            FROM shadow_trades
            WHERE strategy=? AND status='open' AND would_live_accept=1
            """,
            (strategy,),
        ).fetchall()
        n_long = sum(1 for r in rows if r["side"] == "LONG")
        n_short = sum(1 for r in rows if r["side"] == "SHORT")
        same_side = n_long if side == "LONG" else n_short
        symbols = {r["symbol"] for r in rows}
        gross = sum(1.0 / max(float(r["stop_atr"] or 1.0), 0.1) for r in rows)
        gross += 1.0 / max(stop_atr, 0.1)
        delta = n_long - n_short + (1 if side == "LONG" else -1)
        sym_count = len(symbols | {symbol})
        return PortfolioSnapshot(
            concurrent_positions_total=len(rows),
            concurrent_positions_same_side=same_side,
            net_delta_at_entry=delta,
            gross_exposure_at_entry=gross,
            symbols_active_count=sym_count,
        )

    def on_bar(self, symbol: str, candles_5m: list, st: SymbolState, *, market_ctx: MarketContext | None = None):
        """Advance open forward-windows for this symbol, then maybe snapshot."""
        if market_ctx is not None:
            self._market_ctx = market_ctx
        if not candles_5m:
            return
        bar = candles_5m[-1]
        self._advance_open("snapshots", symbol, bar.high, bar.low, bar.close)
        self._advance_open("setup_snapshots", symbol, bar.high, bar.low, bar.close)
        self._process_pending_entries(symbol, bar)
        self._manage_shadow_trades(symbol, bar)
        self._advance_post_exits(symbol, bar.high, bar.low, bar.close)

        f = self._features(candles_5m, st)

        if f is not None and f["v_confirms3"] == 1:
            last_setup = self._last_setup_bar.get(symbol)
            if last_setup is None or (f["bar_time"] - last_setup).total_seconds() >= DEDUP_BARS * 300:
                self._last_setup_bar[symbol] = f["bar_time"]
                self.conn.execute(
                    """
                    INSERT INTO setup_snapshots (
                        bar_time, symbol, hour, session, close, atr, atr_pct,
                        cascade_strength, liq_direction_imb, ret_5d,
                        vol_z, imb_z, breakout_distance_pct,
                        body_ratio, impulse_pct, above_ema, breakout,
                        n_confirms, decile, aggression,
                        cascade_active,
                        v_strict, v_allhours, v_confirms3, v_loose,
                        created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        f["bar_time"].isoformat(), symbol, f["hour"], _session(f["hour"]),
                        f["close"], f["atr"], f["atr_pct"],
                        f["cascade_strength"], f["liq_direction_imb"], f["ret_5d"],
                        f["vol_z"], f["imb_z"], f["breakout_distance_pct"],
                        f["body_ratio"], f["impulse_pct"], f["above_ema"], f["breakout"],
                        f["n_confirms"], f["decile"], f["aggression"],
                        int(st.cascade_active),
                        f["v_strict"], f["v_allhours"], f["v_confirms3"], f["v_loose"],
                        datetime.now(timezone.utc).timestamp(),
                    ),
                )
                self._writes += 1
                self._maybe_commit(force=True)

                imb = float(f["liq_direction_imb"])
                if abs(imb) >= 0.01:
                    self._eval_shadow_strategies(
                        BAR_STRATEGIES, symbol, f, imb=imb,
                        cascade_active=bool(st.cascade_active),
                    )

        if not st.cascade_active or f is None:
            self._maybe_commit()
            return

        last = self._last_snap_bar.get(symbol)
        if last is not None and (f["bar_time"] - last).total_seconds() < DEDUP_BARS * 300:
            self._maybe_commit()
            return
        self._last_snap_bar[symbol] = f["bar_time"]

        self.conn.execute(
            """
            INSERT INTO snapshots (
                bar_time, symbol, hour, session, close, atr, atr_pct,
                cascade_strength, liq_direction_imb, ret_5d,
                vol_z, imb_z, breakout_distance_pct,
                body_ratio, impulse_pct, above_ema, breakout,
                n_confirms, decile, aggression,
                v_strict, v_allhours, v_confirms3, v_loose,
                created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                f["bar_time"].isoformat(), symbol, f["hour"], _session(f["hour"]),
                f["close"], f["atr"], f["atr_pct"],
                f["cascade_strength"], f["liq_direction_imb"], f["ret_5d"],
                f["vol_z"], f["imb_z"], f["breakout_distance_pct"],
                f["body_ratio"], f["impulse_pct"], f["above_ema"], f["breakout"],
                f["n_confirms"], f["decile"], f["aggression"],
                f["v_strict"], f["v_allhours"], f["v_confirms3"], f["v_loose"],
                datetime.now(timezone.utc).timestamp(),
            ),
        )
        self._writes += 1
        self._maybe_commit(force=True)

    def on_intraday_burst(
        self,
        symbol: str,
        candles_5m: list,
        st: SymbolState,
        burst: dict,
        *,
        min_volume_usd: float = 20_000.0,
        min_events: int = 3,
        dedup_bars: int = DEDUP_BARS,
        market_ctx: MarketContext | None = None,
    ):
        """Snapshot force-order intraday bursts, independent of daily cascade state."""
        if market_ctx is not None:
            self._market_ctx = market_ctx
        if not candles_5m:
            return
        bar = candles_5m[-1]
        self._advance_open("burst_snapshots", symbol, bar.high, bar.low, bar.close)

        vol_30m = float(burst.get("volume_30m", 0.0))
        events_30m = int(burst.get("events_30m", 0))
        if vol_30m < min_volume_usd or events_30m < min_events:
            self._maybe_commit()
            return

        f = self._features(candles_5m, st)
        if f is None:
            self._maybe_commit()
            return

        last = self._last_burst_bar.get(symbol)
        if last is not None and (f["bar_time"] - last).total_seconds() < dedup_bars * 300:
            self._maybe_commit()
            return
        self._last_burst_bar[symbol] = f["bar_time"]

        imb_30m = float(burst.get("imbalance_30m", 0.0))

        self.conn.execute(
            """
            INSERT INTO burst_snapshots (
                bar_time, symbol, hour, session, close, atr, atr_pct,
                burst_volume_15m, burst_volume_30m, burst_volume_60m,
                burst_events_15m, burst_events_30m, burst_events_60m,
                long_liq_30m, short_liq_30m, liq_imbalance_30m, max_order_usd_30m,
                cascade_strength, liq_direction_imb, ret_5d,
                vol_z, imb_z, breakout_distance_pct,
                body_ratio, impulse_pct, above_ema, breakout,
                n_confirms, decile, aggression,
                created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                f["bar_time"].isoformat(), symbol, f["hour"], _session(f["hour"]),
                f["close"], f["atr"], f["atr_pct"],
                float(burst.get("volume_15m", 0.0)),
                vol_30m,
                float(burst.get("volume_60m", 0.0)),
                int(burst.get("events_15m", 0)),
                events_30m,
                int(burst.get("events_60m", 0)),
                float(burst.get("long_liq_30m", 0.0)),
                float(burst.get("short_liq_30m", 0.0)),
                imb_30m,
                float(burst.get("max_order_usd_30m", 0.0)),
                f["cascade_strength"], f["liq_direction_imb"], f["ret_5d"],
                f["vol_z"], f["imb_z"], f["breakout_distance_pct"],
                f["body_ratio"], f["impulse_pct"], f["above_ema"], f["breakout"],
                f["n_confirms"], f["decile"], f["aggression"],
                datetime.now(timezone.utc).timestamp(),
            ),
        )
        self._writes += 1
        self._maybe_commit(force=True)

        self._eval_shadow_strategies(
            BURST_STRATEGIES, symbol, f, imb=imb_30m,
            cascade_active=bool(st.cascade_active),
            burst_vol=vol_30m, burst_events=events_30m,
        )

    def _maybe_commit(self, force: bool = False):
        self._writes += 1
        if force or self._writes >= 20:
            self.conn.commit()
            self._writes = 0

    def _trade_path_atr(self, side: str, entry: float, atr: float, high: float, low: float) -> tuple[float, float]:
        """Return (favorable, adverse) in signed ATR units for this bar."""
        if side == "LONG":
            return (high - entry) / atr, (low - entry) / atr
        return (entry - low) / atr, (entry - high) / atr

    def _portfolio_allows_open(self, symbol: str, session: str, side: str) -> bool:
        cfg = self.portfolio
        if cfg.max_concurrent is None:
            return True

        total_open = self.conn.execute(
            "SELECT COUNT(*) FROM shadow_trades WHERE status='open'",
        ).fetchone()[0]
        if total_open >= cfg.max_concurrent:
            return False

        sym_sess = self.conn.execute(
            "SELECT COUNT(*) FROM shadow_trades WHERE symbol=? AND session=? AND status='open'",
            (symbol, session),
        ).fetchone()[0]
        if sym_sess >= cfg.max_per_symbol_session:
            return False

        if cfg.max_net_delta is not None:
            rows = self.conn.execute(
                "SELECT side FROM shadow_trades WHERE status='open'",
            ).fetchall()
            n_long = sum(1 for r in rows if r["side"] == "LONG")
            n_short = sum(1 for r in rows if r["side"] == "SHORT")
            delta = n_long - n_short
            if side == "LONG" and delta + 1 > cfg.max_net_delta:
                return False
            if side == "SHORT" and delta - 1 < -cfg.max_net_delta:
                return False
        return True

    def _manage_shadow_trades(self, symbol: str, bar):
        """Manage open shadow trades for this symbol on bar close."""
        high = float(bar.high)
        low = float(bar.low)
        close = float(bar.close)
        close_time_str = (
            bar.close_time.isoformat()
            if hasattr(bar, "close_time")
            else datetime.now(timezone.utc).isoformat()
        )

        rows = self.conn.execute(
            """
            SELECT id, strategy, side, entry_price, stop_price, tp_price, atr,
                   bars_held, time_bars, time_exit_only, run_mae_atr, run_mfe_atr,
                   bars_to_mfe_peak, fill_price_next_open, scale_filled_price
            FROM shadow_trades WHERE symbol=? AND status='open'
            """,
            (symbol,),
        ).fetchall()

        for r in rows:
            tid = r["id"]
            strategy = r["strategy"]
            side = r["side"]
            entry = r["entry_price"]
            sl = r["stop_price"]
            tp = r["tp_price"]
            atr = r["atr"]
            bars_held = r["bars_held"] + 1
            max_bars = int(r["time_bars"] or 6)
            skip_tp = bool(r["time_exit_only"])

            fav, adv = self._trade_path_atr(side, entry, atr, high, low)
            run_mfe = max(float(r["run_mfe_atr"] or 0.0), fav)
            run_mae = min(float(r["run_mae_atr"] or 0.0), adv)

            mfe_peak_bar = int(r["bars_to_mfe_peak"] or bars_held)
            if fav > float(r["run_mfe_atr"] or 0.0):
                mfe_peak_bar = bars_held

            self.conn.execute(
                """
                INSERT OR REPLACE INTO trade_r_path
                    (trade_id, phase, bar_num, r_high, r_low, r_close)
                VALUES (?, 'open', ?, ?, ?, ?)
                """,
                (
                    tid,
                    bars_held,
                    fav,
                    adv,
                    self._signed_pnl_atr(side, entry, close, atr),
                ),
            )

            fill_next_open = r["fill_price_next_open"]
            if bars_held == 1:
                fill_next_open = float(bar.open)

            spec = _STRATEGY_BY_NAME.get(strategy)
            effective_sl = sl
            trail_active = False
            if (
                spec is not None
                and spec.trail_atr is not None
                and spec.trail_trigger_r is not None
                and run_mfe >= spec.trail_trigger_r
            ):
                trail_active = True
                if side == "LONG":
                    trail_sl = entry + (run_mfe - spec.trail_atr) * atr
                    effective_sl = max(sl, trail_sl)
                else:
                    trail_sl = entry - (run_mfe - spec.trail_atr) * atr
                    effective_sl = min(sl, trail_sl)

            pnl_atr = 0.0
            exit_price = 0.0
            exit_reason = None

            if side == "LONG":
                if low <= effective_sl:
                    exit_price = effective_sl
                    pnl_atr = (effective_sl - entry) / atr
                    exit_reason = "trail" if trail_active and effective_sl > sl else "stop"
                elif not skip_tp and high >= tp:
                    exit_price = tp
                    pnl_atr = (tp - entry) / atr
                    exit_reason = "tp"
            else:
                if high >= effective_sl:
                    exit_price = effective_sl
                    pnl_atr = (entry - effective_sl) / atr
                    exit_reason = "trail" if trail_active and effective_sl < sl else "stop"
                elif not skip_tp and low <= tp:
                    exit_price = tp
                    pnl_atr = (entry - tp) / atr
                    exit_reason = "tp"

            if exit_reason is None and bars_held >= max_bars:
                exit_price = close
                pnl_atr = (close - entry) / atr if side == "LONG" else (entry - close) / atr
                exit_reason = "time"

            # Scale-in add-on (G0 Aug 21): from scale_after_bars onward, a resting
            # limit at entry ∓ scale_in_atr*ATR (adverse side) fills ONCE and blends
            # the average entry. SL/time anchors stay on the FIRST entry; exits are
            # evaluated pre-scale on the trigger bar (conservative stop-first).
            # Post-scale, pnl/MFE/MAE/path rows measure vs the blended entry — the
            # paired baseline is the unscaled sibling variant.
            if (
                exit_reason is None
                and spec is not None
                and spec.scale_in_atr is not None
                and r["scale_filled_price"] is None
                and bars_held >= int(spec.scale_after_bars)
                and atr > 0
            ):
                level = (
                    entry - spec.scale_in_atr * atr
                    if side == "LONG"
                    else entry + spec.scale_in_atr * atr
                )
                filled = low <= level if side == "LONG" else high >= level
                if filled:
                    blended = (entry + level) / 2.0
                    self.conn.execute(
                        "UPDATE shadow_trades SET entry_price=?, scale_filled_price=? "
                        "WHERE id=?",
                        (blended, level, tid),
                    )
                    self.conn.commit()
                    entry = blended
                    logger.info(
                        "➕ [SHADOW SCALE-IN] Strategy=%s Symbol=%s leg2=%.6f avg=%.6f",
                        strategy, symbol, level, blended,
                    )

            horizon_updates: list[str] = []
            horizon_vals: list[float] = []
            if bars_held in MAE_CHECKPOINT_BARS:
                label = MAE_CHECKPOINT_BARS[bars_held]
                horizon_updates.extend([f"mae_{label}=?", f"mfe_{label}=?"])
                horizon_vals.extend([run_mae, run_mfe])
            if bars_held in PNL_CHECKPOINT_BARS:
                pnl_label = PNL_CHECKPOINT_BARS[bars_held]
                horizon_updates.append(f"pnl_{pnl_label}=?")
                horizon_vals.append(self._signed_pnl_atr(side, entry, close, atr))

            if exit_reason is not None:
                sets = [
                    "status='closed'", "pnl_atr=?", "exit_time=?", "exit_price=?",
                    "exit_reason=?", "bars_held=?", "run_mae_atr=?", "run_mfe_atr=?",
                    "bars_to_mfe_peak=?",
                ]
                vals: list = [
                    pnl_atr, close_time_str, exit_price, exit_reason, bars_held,
                    run_mae, run_mfe, mfe_peak_bar,
                ]
                if fill_next_open is not None:
                    sets.append("fill_price_next_open=?")
                    vals.append(fill_next_open)
                sets.extend(horizon_updates)
                vals.extend(horizon_vals)
                vals.append(tid)
                self.conn.execute(
                    f"UPDATE shadow_trades SET {', '.join(sets)} WHERE id=?",
                    vals,
                )
                self.conn.commit()
                logger.info(
                    "💰 [SHADOW TRADE CLOSED] Strategy=%s Symbol=%s Side=%s "
                    "Entry=%.6f Exit=%.6f PnL=%.3f ATR (%s) MAE=%.3f",
                    strategy, symbol, side, entry, exit_price, pnl_atr, exit_reason, run_mae,
                )
            else:
                sets = [
                    "bars_held=?", "run_mae_atr=?", "run_mfe_atr=?",
                    "bars_to_mfe_peak=?",
                ]
                vals = [bars_held, run_mae, run_mfe, mfe_peak_bar]
                if fill_next_open is not None:
                    sets.append("fill_price_next_open=?")
                    vals.append(fill_next_open)
                sets.extend(horizon_updates)
                vals.extend(horizon_vals)
                vals.append(tid)
                self.conn.execute(
                    f"UPDATE shadow_trades SET {', '.join(sets)} WHERE id=?",
                    vals,
                )

    def _advance_post_exits(self, symbol: str, high: float, low: float, close: float):
        """Advance post-exit MFE/MAE windows for closed trades of this symbol.

        Keeps tracking for POST_EXIT_BARS (24h) after close so analysis sees
        what price did after the strategy flat-lined — the censoring that
        hides post-exit runners/dips in run_mfe_atr/run_mae_atr.
        Entry-referenced ATR units, same convention as run_* columns.
        """
        rows = self.conn.execute(
            """
            SELECT id, side, entry_price, atr, post_bars, post_mfe_atr, post_mae_atr
            FROM shadow_trades
            WHERE symbol=? AND status='closed' AND post_bars < ?
            """,
            (symbol, POST_EXIT_BARS),
        ).fetchall()
        for r in rows:
            atr = float(r["atr"] or 0.0)
            if atr <= 0:
                continue
            entry = float(r["entry_price"])
            nb = int(r["post_bars"] or 0) + 1
            fav, adv = self._trade_path_atr(r["side"], entry, atr, high, low)
            pmfe = max(float(r["post_mfe_atr"] or 0.0), fav)
            pmae = min(float(r["post_mae_atr"] or 0.0), adv)
            self.conn.execute(
                "UPDATE shadow_trades SET post_bars=?, post_mfe_atr=?, post_mae_atr=? "
                "WHERE id=?",
                (nb, pmfe, pmae, r["id"]),
            )
            self.conn.execute(
                """
                INSERT OR REPLACE INTO trade_r_path
                    (trade_id, phase, bar_num, r_high, r_low, r_close)
                VALUES (?, 'post', ?, ?, ?, ?)
                """,
                (
                    r["id"],
                    nb,
                    fav,
                    adv,
                    self._signed_pnl_atr(r["side"], entry, close, atr),
                ),
            )

    def _shadow_trade_exists(self, symbol: str, strategy: str) -> bool:
        open_n = self.conn.execute(
            "SELECT COUNT(*) FROM shadow_trades WHERE symbol=? AND strategy=? AND status='open'",
            (symbol, strategy),
        ).fetchone()[0]
        if open_n > 0:
            return True
        pending_n = self.conn.execute(
            "SELECT COUNT(*) FROM shadow_pending_entries "
            "WHERE symbol=? AND strategy=? AND status='pending'",
            (symbol, strategy),
        ).fetchone()[0]
        return pending_n > 0

    def _insert_shadow_trade(
        self,
        spec: ShadowStrategy,
        symbol: str,
        f: dict,
        side: str,
        entry_price: float,
        entry_time_str: str,
        *,
        imb: float,
        cascade_active: bool = False,
        burst_vol: float = 0.0,
        signal_price: float | None = None,
        limit_offset_atr: float | None = None,
    ):
        atr = f["atr"]
        if side == "LONG":
            stop_price = entry_price - spec.stop_atr * atr
            tp_price = entry_price + spec.tp_atr * atr
        else:
            stop_price = entry_price + spec.stop_atr * atr
            tp_price = entry_price - spec.tp_atr * atr

        is_weekend = 1 if f["bar_time"].weekday() >= 5 else 0
        port = self._portfolio_at_entry(spec.name, symbol, side, spec.stop_atr)
        mkt = self._market_ctx
        cluster_bucket = _cluster_bucket(f["bar_time"])
        would_live = self._would_live_accept(
            spec.name, symbol, side, f["session"], cluster_bucket,
        )
        # 2026-08-30: live weekday-gate mirror (0=Mon..6=Sun, same convention as the
        # live engine gate). Live arms exclude certain weekdays; WLA must answer
        # "would LIVE accept", so excluded weekday cells are WLA=0 even though the
        # match path keeps logging them for research.
        if would_live and spec.exclude_weekdays and f["bar_time"].weekday() in spec.exclude_weekdays:
            would_live = 0

        self.conn.execute(
            """
            INSERT INTO shadow_trades (
                strategy, symbol, side, entry_time, entry_price, stop_price, tp_price, atr,
                status, created_at,
                session, hour, decile, stop_atr, tp_atr, time_bars,
                liq_imb, burst_vol_30m, v_confirms3, v_strict, cascade_active, trigger,
                entry_cascade_strength, entry_impulse_pct, entry_vol_z, entry_atr_pct,
                time_exit_only, is_weekend,
                concurrent_positions_total, concurrent_positions_same_side,
                net_delta_at_entry, gross_exposure_at_entry, symbols_active_count,
                btc_trend_state, btc_distance_from_ema_pct, market_breadth_pct,
                funding_rate_btc, funding_rate_symbol,
                spread_bps, book_depth_usd_5bps, fill_price_next_open,
                btc_adx, btc_regime_age_bars, btc_realized_vol_24h,
                symbol_trend_state, cluster_breadth, market_liq_flow_usd,
                burst_vol_zscore, entry_lag_bars, oi_delta_30m_pct,
                would_live_accept, cluster_bucket
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                spec.name, symbol, side, entry_time_str, entry_price, stop_price, tp_price, atr,
                datetime.now(timezone.utc).timestamp(),
                f["session"], f["hour"], f["decile"],
                spec.stop_atr, spec.tp_atr, spec.time_bars,
                imb, burst_vol or None,
                f.get("v_confirms3"), f.get("v_strict"), int(cascade_active), spec.trigger,
                f.get("cascade_strength"), f.get("impulse_pct"), f.get("vol_z"), f.get("atr_pct"),
                int(spec.time_exit_only), is_weekend,
                port.concurrent_positions_total, port.concurrent_positions_same_side,
                port.net_delta_at_entry, port.gross_exposure_at_entry, port.symbols_active_count,
                mkt.btc_trend_state, mkt.btc_distance_from_ema_pct, mkt.market_breadth_pct,
                mkt.funding_rate_btc, mkt.funding_rate_symbol,
                mkt.spread_bps, mkt.book_depth_usd_5bps, None,
                mkt.btc_adx, mkt.btc_regime_age_bars, mkt.btc_realized_vol_24h,
                mkt.symbol_trend_state, mkt.cluster_breadth, mkt.market_liq_flow_usd,
                mkt.burst_vol_zscore, mkt.entry_lag_bars, mkt.oi_delta_30m_pct,
                would_live, cluster_bucket,
            ),
        )
        self.conn.commit()
        if limit_offset_atr is not None and signal_price is not None:
            logger.info(
                "🚀 [SHADOW LIMIT FILLED] Strategy=%s Symbol=%s Side=%s "
                "Signal=%.6f Limit=%.6f Entry=%.6f",
                spec.name, symbol, side, signal_price, entry_price, entry_price,
            )
        else:
            logger.info(
                "🚀 [SHADOW TRADE OPENED] Strategy=%s Symbol=%s Side=%s Entry=%.6f",
                spec.name, symbol, side, entry_price,
            )

    def _process_pending_entries(self, symbol: str, bar):
        """Try to fill or expire resting limit entries for this symbol."""
        high = float(bar.high)
        low = float(bar.low)
        close_time_str = (
            bar.close_time.isoformat()
            if hasattr(bar, "close_time")
            else datetime.now(timezone.utc).isoformat()
        )

        rows = self.conn.execute(
            """
            SELECT id, strategy, side, signal_price, limit_price, atr, limit_offset_atr,
                   max_bars, bars_waited, session, hour, decile, stop_atr, tp_atr, time_bars,
                   liq_imb, burst_vol_30m, v_confirms3, v_strict, cascade_active, trigger,
                   entry_cascade_strength, entry_impulse_pct, entry_vol_z, entry_atr_pct,
                   time_exit_only, is_weekend
            FROM shadow_pending_entries
            WHERE symbol=? AND status='pending'
            """,
            (symbol,),
        ).fetchall()

        for r in rows:
            spec = _STRATEGY_BY_NAME.get(r["strategy"])
            if spec is None:
                continue

            side = r["side"]
            limit_price = float(r["limit_price"])
            filled = (side == "LONG" and low <= limit_price) or (side == "SHORT" and high >= limit_price)

            if filled:
                if not self._portfolio_allows_open(symbol, r["session"], side):
                    self.conn.execute(
                        "UPDATE shadow_pending_entries SET status='cancelled', fill_time=? WHERE id=?",
                        (close_time_str, r["id"]),
                    )
                    continue

                f = {
                    "bar_time": bar.close_time if hasattr(bar, "close_time") else datetime.now(timezone.utc),
                    "session": r["session"],
                    "hour": r["hour"],
                    "decile": r["decile"],
                    "atr": r["atr"],
                    "v_confirms3": r["v_confirms3"],
                    "v_strict": r["v_strict"],
                    "cascade_strength": r["entry_cascade_strength"],
                    "impulse_pct": r["entry_impulse_pct"],
                    "vol_z": r["entry_vol_z"],
                    "atr_pct": r["entry_atr_pct"],
                }
                self._insert_shadow_trade(
                    spec, symbol, f, side, limit_price, close_time_str,
                    imb=float(r["liq_imb"] or 0.0),
                    cascade_active=bool(r["cascade_active"]),
                    burst_vol=float(r["burst_vol_30m"] or 0.0),
                    signal_price=float(r["signal_price"]),
                    limit_offset_atr=float(r["limit_offset_atr"]),
                )
                self.conn.execute(
                    "UPDATE shadow_pending_entries SET status='filled', fill_time=? WHERE id=?",
                    (close_time_str, r["id"]),
                )
                continue

            bars_waited = int(r["bars_waited"] or 0) + 1
            if bars_waited >= int(r["max_bars"] or 36):
                self.conn.execute(
                    "UPDATE shadow_pending_entries SET status='cancelled', "
                    "bars_waited=?, fill_time=? WHERE id=?",
                    (bars_waited, close_time_str, r["id"]),
                )
            else:
                self.conn.execute(
                    "UPDATE shadow_pending_entries SET bars_waited=? WHERE id=?",
                    (bars_waited, r["id"]),
                )

    def _maybe_open_shadow_trade(
        self,
        spec: ShadowStrategy,
        symbol: str,
        f: dict,
        side: str,
        *,
        imb: float,
        cascade_active: bool = False,
        burst_vol: float = 0.0,
    ):
        """Open a paper shadow trade, or queue a resting limit entry."""
        if self._shadow_trade_exists(symbol, spec.name):
            return

        if not self._portfolio_allows_open(symbol, f["session"], side):
            return

        atr = f["atr"]
        if atr <= 0:
            return

        signal_price = f["close"]
        bar_time_str = (
            f["bar_time"].isoformat()
            if hasattr(f["bar_time"], "isoformat")
            else str(f["bar_time"])
        )

        if spec.limit_entry_atr is not None:
            offset = spec.limit_entry_atr
            if side == "LONG":
                limit_price = signal_price - offset * atr
            else:
                limit_price = signal_price + offset * atr

            is_weekend = 1 if f["bar_time"].weekday() >= 5 else 0
            self.conn.execute(
                """
                INSERT INTO shadow_pending_entries (
                    strategy, symbol, side, signal_time, signal_price, limit_price, atr,
                    limit_offset_atr, max_bars, session, hour, decile, stop_atr, tp_atr, time_bars,
                    liq_imb, burst_vol_30m, v_confirms3, v_strict, cascade_active, trigger,
                    entry_cascade_strength, entry_impulse_pct, entry_vol_z, entry_atr_pct,
                    time_exit_only, is_weekend, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    spec.name, symbol, side, bar_time_str, signal_price, limit_price, atr,
                    offset, spec.limit_entry_max_bars,
                    f["session"], f["hour"], f["decile"],
                    spec.stop_atr, spec.tp_atr, spec.time_bars,
                    imb, burst_vol or None,
                    f.get("v_confirms3"), f.get("v_strict"), int(cascade_active), spec.trigger,
                    f.get("cascade_strength"), f.get("impulse_pct"), f.get("vol_z"), f.get("atr_pct"),
                    int(spec.time_exit_only), is_weekend,
                    datetime.now(timezone.utc).timestamp(),
                ),
            )
            self.conn.commit()
            logger.info(
                "📋 [SHADOW LIMIT QUEUED] Strategy=%s Symbol=%s Side=%s "
                "Signal=%.6f Limit=%.6f (%.1f ATR)",
                spec.name, symbol, side, signal_price, limit_price, offset,
            )
            return

        self._insert_shadow_trade(
            spec, symbol, f, side, signal_price, bar_time_str,
            imb=imb, cascade_active=cascade_active, burst_vol=burst_vol,
        )

    def close(self):
        """Best-effort flush for graceful runner shutdown."""
        try:
            self.conn.commit()
        finally:
            self.conn.close()

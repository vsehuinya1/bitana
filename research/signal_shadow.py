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

# Forward horizons in 5m bars: 15m, 30m, 1h, 2h, 4h, 8h.
HORIZONS = (3, 6, 12, 24, 48, 96)
MAX_H = HORIZONS[-1]

# Catch-all candidate floor and de-duplication window (bars).
LOOSE_MIN_CONFIRMS = 2
DEDUP_BARS = 3

SideMode = Literal["fade", "fade_short_only", "follow", "long"]

# Checkpoint bars for per-horizon MAE/MFE snapshots (5m bars).
MAE_CHECKPOINT_BARS: dict[int, str] = {36: "3h", 72: "6h", 144: "12h", 288: "24h"}


@dataclass
class ShadowPortfolioConfig:
    """Portfolio caps — disabled by default so shadow logs everything in parallel."""

    max_concurrent: int | None = None
    max_per_symbol_session: int = 1
    max_net_delta: int | None = None  # |open longs - open shorts|


DEFAULT_PORTFOLIO = ShadowPortfolioConfig()


@dataclass
class MarketContext:
    """Regime snapshot supplied by the runner at bar time."""

    btc_trend_state: str | None = None
    btc_distance_from_ema_pct: float | None = None
    market_breadth_pct: float | None = None
    funding_rate_btc: float | None = None
    funding_rate_symbol: float | None = None


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


# Every candidate rule family — logged in parallel; pick winners offline.
SHADOW_STRATEGIES: tuple[ShadowStrategy, ...] = (
    # ── Burst / intraday liq (on_intraday_burst) ──
    ShadowStrategy("late_fade", "burst", "fade", 12.0, 3.0, sessions=frozenset({"late"})),
    ShadowStrategy(
        "ny_burst_fade", "burst", "fade", 4.0, 3.0,
        sessions=frozenset({"ny"}), hours=frozenset(range(13, 18)),
    ),
    ShadowStrategy(
        "ny_burst_fade_short", "burst", "fade_short_only", 4.0, 3.0,
        sessions=frozenset({"ny"}), hours=frozenset(range(13, 18)),
    ),
    ShadowStrategy("asia_burst_fade", "burst", "fade", 4.0, 3.0, sessions=frozenset({"asia"})),
    ShadowStrategy("london_burst_fade", "burst", "fade", 4.0, 3.0, sessions=frozenset({"london"})),
    ShadowStrategy("burst_follow", "burst", "follow", 10.0, 3.0),
    ShadowStrategy(
        "ny_burst_follow", "burst", "follow", 10.0, 3.0,
        sessions=frozenset({"ny"}), hours=frozenset(range(13, 18)),
    ),
    ShadowStrategy(
        "nony_momentum", "burst", "follow", 10.0, 3.0,
        min_imb=0.9, min_burst_events=10, require_above_ema_zero=True, exclude_ny=True,
    ),
    # ── 3h / 6h follow variants (pos_imb only, no TP — pure time exit) ──
    # These test Nemo's finding: burst follow edge lives at 3-6h, not 30min.
    # pos_imb_only=True means only fire when liq_imbalance > 0 (long-liq dominated = bear pressure).
    ShadowStrategy(
        "follow_3h_all", "burst", "follow", 10.0, 999.0, time_bars=36,
        pos_imb_only=True, time_exit_only=True,
    ),
    ShadowStrategy(
        "follow_6h_all", "burst", "follow", 10.0, 999.0, time_bars=72,
        pos_imb_only=True, time_exit_only=True,
    ),
    ShadowStrategy(
        "follow_3h_asia", "burst", "follow", 10.0, 999.0, time_bars=36,
        sessions=frozenset({"asia"}), pos_imb_only=True, time_exit_only=True,
    ),
    ShadowStrategy(
        "follow_6h_asia", "burst", "follow", 10.0, 999.0, time_bars=72,
        sessions=frozenset({"asia"}), pos_imb_only=True, time_exit_only=True,
    ),
    ShadowStrategy(
        "follow_3h_london", "burst", "follow", 10.0, 999.0, time_bars=36,
        sessions=frozenset({"london"}), pos_imb_only=True, time_exit_only=True,
    ),
    ShadowStrategy(
        "follow_6h_london", "burst", "follow", 10.0, 999.0, time_bars=72,
        sessions=frozenset({"london"}), pos_imb_only=True, time_exit_only=True,
    ),
    ShadowStrategy(
        "follow_3h_ny", "burst", "follow", 10.0, 999.0, time_bars=36,
        sessions=frozenset({"ny"}), hours=frozenset(range(13, 18)),
        pos_imb_only=True, time_exit_only=True,
    ),
    ShadowStrategy(
        "follow_6h_ny", "burst", "follow", 10.0, 999.0, time_bars=72,
        sessions=frozenset({"ny"}), hours=frozenset(range(13, 18)),
        pos_imb_only=True, time_exit_only=True,
    ),
    ShadowStrategy(
        "follow_3h_late", "burst", "follow", 10.0, 999.0, time_bars=36,
        sessions=frozenset({"late"}), pos_imb_only=True, time_exit_only=True,
    ),
    ShadowStrategy(
        "follow_6h_late", "burst", "follow", 10.0, 999.0, time_bars=72,
        sessions=frozenset({"late"}), pos_imb_only=True, time_exit_only=True,
    ),
    # neg_imb fade for Asia/London; pos_imb fade for NY/Late (historical split).
    ShadowStrategy(
        "fade_3h_asia", "burst", "fade", 10.0, 999.0, time_bars=36,
        sessions=frozenset({"asia"}), neg_imb_only=True, time_exit_only=True,
    ),
    ShadowStrategy(
        "fade_6h_asia", "burst", "fade", 10.0, 999.0, time_bars=72,
        sessions=frozenset({"asia"}), neg_imb_only=True, time_exit_only=True,
    ),
    ShadowStrategy(
        "fade_3h_london", "burst", "fade", 10.0, 999.0, time_bars=36,
        sessions=frozenset({"london"}), neg_imb_only=True, time_exit_only=True,
    ),
    ShadowStrategy(
        "fade_6h_london", "burst", "fade", 10.0, 999.0, time_bars=72,
        sessions=frozenset({"london"}), neg_imb_only=True, time_exit_only=True,
    ),
    ShadowStrategy(
        "fade_3h_ny", "burst", "fade", 10.0, 999.0, time_bars=36,
        sessions=frozenset({"ny"}), hours=frozenset(range(13, 18)),
        pos_imb_only=True, time_exit_only=True,
    ),
    ShadowStrategy(
        "fade_6h_ny", "burst", "fade", 10.0, 999.0, time_bars=72,
        sessions=frozenset({"ny"}), hours=frozenset(range(13, 18)),
        pos_imb_only=True, time_exit_only=True,
    ),
    ShadowStrategy(
        "fade_3h_late", "burst", "fade", 10.0, 999.0, time_bars=36,
        sessions=frozenset({"late"}), pos_imb_only=True, time_exit_only=True,
    ),
    ShadowStrategy(
        "fade_6h_late", "burst", "fade", 10.0, 999.0, time_bars=72,
        sessions=frozenset({"late"}), pos_imb_only=True, time_exit_only=True,
    ),
    # ── Setup / bar-close (on_bar, v_confirms3 snapshot) ──
    ShadowStrategy(
        "setup_fade", "bar", "fade", 4.0, 3.0, require_v_confirms3=True,
    ),
    ShadowStrategy(
        "setup_fade_ny", "bar", "fade", 4.0, 3.0,
        require_v_confirms3=True, sessions=frozenset({"ny"}), hours=frozenset(range(13, 18)),
    ),
    ShadowStrategy(
        "setup_fade_ny_short", "bar", "fade_short_only", 4.0, 3.0,
        require_v_confirms3=True, sessions=frozenset({"ny"}), hours=frozenset(range(13, 18)),
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
                fwd_atr_24 REAL, fwd_atr_48 REAL, fwd_atr_96 REAL,
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
                fwd_atr_24 REAL, fwd_atr_48 REAL, fwd_atr_96 REAL,
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
                fwd_atr_24 REAL, fwd_atr_48 REAL, fwd_atr_96 REAL,
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
                funding_rate_symbol REAL
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
        self._migrate_shadow_trades()
        self.conn.commit()

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
        ):
            if col not in existing:
                self.conn.execute(f"ALTER TABLE shadow_trades ADD COLUMN {col} {typ}")

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

    def _portfolio_at_entry(self, symbol: str, side: str, stop_atr: float) -> PortfolioSnapshot:
        """Snapshot open-book state excluding the trade about to open."""
        rows = self.conn.execute(
            "SELECT symbol, side, stop_atr FROM shadow_trades WHERE status='open'",
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
        self._manage_shadow_trades(symbol, bar)

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
                   bars_held, time_bars, time_exit_only, run_mae_atr, run_mfe_atr
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

            pnl_atr = 0.0
            exit_price = 0.0
            exit_reason = None

            if side == "LONG":
                if low <= sl:
                    exit_price = sl
                    pnl_atr = (sl - entry) / atr
                    exit_reason = "stop"
                elif not skip_tp and high >= tp:
                    exit_price = tp
                    pnl_atr = (tp - entry) / atr
                    exit_reason = "tp"
            else:
                if high >= sl:
                    exit_price = sl
                    pnl_atr = (entry - sl) / atr
                    exit_reason = "stop"
                elif not skip_tp and low <= tp:
                    exit_price = tp
                    pnl_atr = (entry - tp) / atr
                    exit_reason = "tp"

            if exit_reason is None and bars_held >= max_bars:
                exit_price = close
                pnl_atr = (close - entry) / atr if side == "LONG" else (entry - close) / atr
                exit_reason = "time"

            horizon_updates: list[str] = []
            horizon_vals: list[float] = []
            if bars_held in MAE_CHECKPOINT_BARS:
                label = MAE_CHECKPOINT_BARS[bars_held]
                horizon_updates.extend([f"mae_{label}=?", f"mfe_{label}=?"])
                horizon_vals.extend([run_mae, run_mfe])

            if exit_reason is not None:
                sets = [
                    "status='closed'", "pnl_atr=?", "exit_time=?", "exit_price=?",
                    "exit_reason=?", "bars_held=?", "run_mae_atr=?", "run_mfe_atr=?",
                ]
                vals: list = [
                    pnl_atr, close_time_str, exit_price, exit_reason, bars_held,
                    run_mae, run_mfe,
                ]
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
                sets = ["bars_held=?", "run_mae_atr=?", "run_mfe_atr=?"]
                vals = [bars_held, run_mae, run_mfe]
                sets.extend(horizon_updates)
                vals.extend(horizon_vals)
                vals.append(tid)
                self.conn.execute(
                    f"UPDATE shadow_trades SET {', '.join(sets)} WHERE id=?",
                    vals,
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
        """Open a paper shadow trade if none active for this symbol+strategy."""
        existing = self.conn.execute(
            "SELECT COUNT(*) FROM shadow_trades WHERE symbol=? AND strategy=? AND status='open'",
            (symbol, spec.name),
        ).fetchone()[0]
        if existing > 0:
            return

        if not self._portfolio_allows_open(symbol, f["session"], side):
            return

        entry_price = f["close"]
        atr = f["atr"]
        if atr <= 0:
            return

        if side == "LONG":
            stop_price = entry_price - spec.stop_atr * atr
            tp_price = entry_price + spec.tp_atr * atr
        else:
            stop_price = entry_price + spec.stop_atr * atr
            tp_price = entry_price - spec.tp_atr * atr

        bar_time_str = (
            f["bar_time"].isoformat()
            if hasattr(f["bar_time"], "isoformat")
            else str(f["bar_time"])
        )

        port = self._portfolio_at_entry(symbol, side, spec.stop_atr)
        mkt = self._market_ctx

        self.conn.execute(
            """
            INSERT INTO shadow_trades (
                strategy, symbol, side, entry_time, entry_price, stop_price, tp_price, atr,
                status, created_at,
                session, hour, decile, stop_atr, tp_atr, time_bars,
                liq_imb, burst_vol_30m, v_confirms3, v_strict, cascade_active, trigger,
                time_exit_only,
                concurrent_positions_total, concurrent_positions_same_side,
                net_delta_at_entry, gross_exposure_at_entry, symbols_active_count,
                btc_trend_state, btc_distance_from_ema_pct, market_breadth_pct,
                funding_rate_btc, funding_rate_symbol
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                spec.name, symbol, side, bar_time_str, entry_price, stop_price, tp_price, atr,
                datetime.now(timezone.utc).timestamp(),
                f["session"], f["hour"], f["decile"],
                spec.stop_atr, spec.tp_atr, spec.time_bars,
                imb, burst_vol or None,
                f.get("v_confirms3"), f.get("v_strict"), int(cascade_active), spec.trigger,
                int(spec.time_exit_only),
                port.concurrent_positions_total, port.concurrent_positions_same_side,
                port.net_delta_at_entry, port.gross_exposure_at_entry, port.symbols_active_count,
                mkt.btc_trend_state, mkt.btc_distance_from_ema_pct, mkt.market_breadth_pct,
                mkt.funding_rate_btc, mkt.funding_rate_symbol,
            ),
        )
        self.conn.commit()
        logger.info(
            "🚀 [SHADOW TRADE OPENED] Strategy=%s Symbol=%s Side=%s Entry=%.6f",
            spec.name, symbol, side, entry_price,
        )

    def close(self):
        """Best-effort flush for graceful runner shutdown."""
        try:
            self.conn.commit()
        finally:
            self.conn.close()

"""
V6 Research Telemetry — Observational instrumentation layer.

This module is PURELY OBSERVATIONAL. It logs data for statistical analysis.
It NEVER modifies trading state, engine state, or execution flow.

Schema:
  - trade_entries: Full state snapshot at entry
  - r_path: Per-candle unrealized R evolution
  - shadow_exits: Hypothetical exit triggers
  - exit_attribution: Exit context and efficiency metrics
  - post_exit: What happened after we exited
  - regime_snapshots: Periodic market regime state
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.logging_setup import get_logger

logger = get_logger("v6_telemetry")

TELEMETRY_DB = Path("storage/v6_telemetry.db")


class TelemetryDB:
    """SQLite database for research telemetry. Purely observational."""

    def __init__(self, path: Path = TELEMETRY_DB):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript("""
            -- Full state snapshot at trade entry
            CREATE TABLE IF NOT EXISTS trade_entries (
                trade_uuid TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                entry_time TEXT NOT NULL,
                entry_price REAL NOT NULL,
                stop_price REAL NOT NULL,
                risk_per_unit REAL NOT NULL,
                decile INTEGER,
                aggression REAL,
                cascade_strength REAL,
                cascade_active INTEGER,
                liq_direction_imb REAL,
                ret_5d REAL,
                confirmations TEXT,         -- JSON dict of 6 booleans
                n_confirmations INTEGER,
                vol_z REAL,
                imb_z REAL,
                atr REAL,
                atr_pct REAL,
                risk_pct REAL,
                range_high REAL,
                ema_value REAL,
                session TEXT,               -- asia/london/ny
                equity_at_entry REAL,
                open_positions_at_entry INTEGER,
                btc_price REAL,
                created_at TEXT DEFAULT (datetime('now'))
            );

            -- Per-candle R-path while in trade
            CREATE TABLE IF NOT EXISTS r_path (
                trade_uuid TEXT NOT NULL,
                bar_index INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                price REAL NOT NULL,
                unrealized_r REAL NOT NULL,
                mae_so_far REAL NOT NULL,
                mfe_so_far REAL NOT NULL,
                atr REAL,
                consecutive_red INTEGER DEFAULT 0,
                above_ema INTEGER DEFAULT 0,
                above_range_high INTEGER DEFAULT 0,
                vol_trail_level REAL,
                struct_trail_level REAL,
                PRIMARY KEY (trade_uuid, bar_index)
            );

            -- Shadow exit evaluations (hypothetical exits)
            CREATE TABLE IF NOT EXISTS shadow_exits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_uuid TEXT NOT NULL,
                shadow_name TEXT NOT NULL,
                trigger_bar INTEGER NOT NULL,
                trigger_time TEXT NOT NULL,
                trigger_price REAL NOT NULL,
                shadow_r REAL NOT NULL,
                actual_exit_r REAL,          -- filled after trade closes
                actual_exit_bar INTEGER,     -- filled after trade closes
                delta_r REAL,                -- shadow_r - actual_exit_r
                post_trigger_mfe REAL,       -- max R reached after trigger
                UNIQUE(trade_uuid, shadow_name, trigger_bar)
            );

            -- Exit context and efficiency
            CREATE TABLE IF NOT EXISTS exit_attribution (
                trade_uuid TEXT PRIMARY KEY,
                exit_time TEXT,
                exit_price REAL,
                exit_reason TEXT,
                pnl_r REAL,
                hold_bars INTEGER,
                mae REAL,
                mfe REAL,
                mfe_bar INTEGER,             -- which bar hit peak MFE
                mae_bar INTEGER,             -- which bar hit worst MAE
                exit_session TEXT,
                r_at_midpoint REAL,          -- R at hold_bars/2
                structural_invalidation_count INTEGER DEFAULT 0,
                momentum_reversal_count INTEGER DEFAULT 0,
                optimal_exit_r REAL,         -- MFE (best possible exit)
                exit_efficiency REAL         -- actual_r / mfe if mfe > 0
            );

            -- Post-exit price tracking (continuation analysis)
            CREATE TABLE IF NOT EXISTS post_exit (
                trade_uuid TEXT NOT NULL,
                bars_after INTEGER NOT NULL,
                timestamp TEXT,
                price REAL,
                hypothetical_r REAL,         -- R if we'd held
                PRIMARY KEY (trade_uuid, bars_after)
            );

            -- Periodic regime snapshots
            CREATE TABLE IF NOT EXISTS regime_snapshots (
                timestamp TEXT PRIMARY KEY,
                btc_price REAL,
                n_cascades_active INTEGER,
                n_open_positions INTEGER,
                total_symbols INTEGER,
                equity REAL
            );

            -- Indexes for analysis queries
            CREATE INDEX IF NOT EXISTS idx_rpath_uuid ON r_path(trade_uuid);
            CREATE INDEX IF NOT EXISTS idx_shadow_uuid ON shadow_exits(trade_uuid);
            CREATE INDEX IF NOT EXISTS idx_postexit_uuid ON post_exit(trade_uuid);
            CREATE INDEX IF NOT EXISTS idx_entries_symbol ON trade_entries(symbol);
            CREATE INDEX IF NOT EXISTS idx_entries_decile ON trade_entries(decile);
            CREATE INDEX IF NOT EXISTS idx_entries_session ON trade_entries(session);
        """)
        self.conn.commit()

    # ── Entry Snapshot ────────────────────────────────────────────

    def log_entry(
        self,
        trade_uuid: str,
        symbol: str,
        side: str,
        entry_time: str,
        entry_price: float,
        stop_price: float,
        signal_data: dict,
        engine_state: dict,
        equity: float,
        open_count: int,
        btc_price: float = 0.0,
        is_experimental: bool = False,
    ):
        """Log full state snapshot at trade entry."""
        try:
            confirms = signal_data.get("confirmations", {})
            session = _get_session(entry_time)
            risk_per_unit = abs(entry_price - stop_price)
            atr = signal_data.get("atr", 0)
            atr_pct = (atr / entry_price * 100) if entry_price > 0 else 0

            self.conn.execute("""
                INSERT OR REPLACE INTO trade_entries VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?
                )
            """, (
                trade_uuid, symbol, side, entry_time, entry_price, stop_price,
                risk_per_unit,
                signal_data.get("decile", 0),
                signal_data.get("aggression_score", 0),
                signal_data.get("cascade_strength", 0),
                1 if engine_state.get("cascade_active", False) else 0,
                engine_state.get("liq_direction_imb", 0),
                engine_state.get("ret_5d", 0),
                json.dumps(confirms),
                sum(1 for v in confirms.values() if v),
                signal_data.get("vol_z", 0),
                signal_data.get("imb_z", 0),
                atr,
                atr_pct,
                signal_data.get("risk_pct", 0),
                signal_data.get("range_high", 0),
                signal_data.get("ema_value", 0),
                session,
                equity,
                open_count,
                btc_price,
                datetime.now(timezone.utc).isoformat(),
                1 if is_experimental else 0,
            ))
            self.conn.commit()
        except Exception as e:
            logger.error("Telemetry log_entry failed", error=str(e))

    # ── R-Path Logging ────────────────────────────────────────────

    def log_r_point(
        self,
        trade_uuid: str,
        bar_index: int,
        timestamp: str,
        price: float,
        unrealized_r: float,
        mae_so_far: float,
        mfe_so_far: float,
        atr: float = 0,
        consecutive_red: int = 0,
        above_ema: bool = False,
        above_range_high: bool = False,
        vol_trail_level: float = 0,
        struct_trail_level: float = 0,
    ):
        """Log one point on the unrealized-R path."""
        try:
            self.conn.execute("""
                INSERT OR REPLACE INTO r_path VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
            """, (
                trade_uuid, bar_index, timestamp, price,
                unrealized_r, mae_so_far, mfe_so_far,
                atr, consecutive_red,
                1 if above_ema else 0,
                1 if above_range_high else 0,
                vol_trail_level, struct_trail_level,
            ))
            # Don't commit per-point — batch at end of poll cycle
        except Exception as e:
            logger.error("Telemetry log_r_point failed", error=str(e))

    # ── Shadow Exit Logging ───────────────────────────────────────

    def log_shadow_trigger(
        self,
        trade_uuid: str,
        shadow_name: str,
        trigger_bar: int,
        trigger_time: str,
        trigger_price: float,
        shadow_r: float,
    ):
        """Log a hypothetical shadow exit trigger."""
        try:
            self.conn.execute("""
                INSERT OR IGNORE INTO shadow_exits
                (trade_uuid, shadow_name, trigger_bar, trigger_time,
                 trigger_price, shadow_r)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                trade_uuid, shadow_name, trigger_bar,
                trigger_time, trigger_price, shadow_r,
            ))
        except Exception as e:
            logger.error("Telemetry log_shadow failed", error=str(e))

    def finalize_shadows(self, trade_uuid: str, actual_exit_r: float, actual_exit_bar: int):
        """After trade closes, fill in actuals and compute deltas."""
        try:
            self.conn.execute("""
                UPDATE shadow_exits
                SET actual_exit_r = ?,
                    actual_exit_bar = ?,
                    delta_r = shadow_r - ?
                WHERE trade_uuid = ?
                  AND actual_exit_r IS NULL
            """, (actual_exit_r, actual_exit_bar, actual_exit_r, trade_uuid))
        except Exception as e:
            logger.error("Telemetry finalize_shadows failed", error=str(e))

    # ── Exit Attribution ──────────────────────────────────────────

    def log_exit(
        self,
        trade_uuid: str,
        exit_time: str,
        exit_price: float,
        exit_reason: str,
        pnl_r: float,
        hold_bars: int,
        mae: float,
        mfe: float,
        mfe_bar: int = 0,
        mae_bar: int = 0,
        r_at_midpoint: float = 0,
        structural_invalidation_count: int = 0,
        momentum_reversal_count: int = 0,
    ):
        """Log exit context and compute efficiency metrics."""
        try:
            exit_efficiency = (pnl_r / mfe) if mfe > 0 else 0
            session = _get_session(exit_time)

            self.conn.execute("""
                INSERT OR REPLACE INTO exit_attribution VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
            """, (
                trade_uuid, exit_time, exit_price, exit_reason,
                pnl_r, hold_bars, mae, mfe, mfe_bar, mae_bar,
                session, r_at_midpoint,
                structural_invalidation_count,
                momentum_reversal_count,
                mfe,  # optimal_exit_r = MFE
                exit_efficiency,
            ))
            self.conn.commit()
        except Exception as e:
            logger.error("Telemetry log_exit failed", error=str(e))

    # ── Post-Exit Continuation ────────────────────────────────────

    def log_post_exit_point(
        self,
        trade_uuid: str,
        bars_after: int,
        timestamp: str,
        price: float,
        hypothetical_r: float,
    ):
        """Log price action after exit for continuation analysis."""
        try:
            self.conn.execute("""
                INSERT OR IGNORE INTO post_exit VALUES (?, ?, ?, ?, ?)
            """, (trade_uuid, bars_after, timestamp, price, hypothetical_r))
        except Exception as e:
            logger.error("Telemetry log_post_exit failed", error=str(e))

    # ── Regime Snapshots ──────────────────────────────────────────

    def log_regime(
        self,
        btc_price: float,
        n_cascades: int,
        n_positions: int,
        n_symbols: int,
        equity: float,
    ):
        """Log periodic regime state."""
        try:
            ts = datetime.now(timezone.utc).isoformat()
            self.conn.execute("""
                INSERT OR REPLACE INTO regime_snapshots VALUES (?, ?, ?, ?, ?, ?)
            """, (ts, btc_price, n_cascades, n_positions, n_symbols, equity))
        except Exception as e:
            logger.error("Telemetry log_regime failed", error=str(e))

    def commit(self):
        """Batch commit for R-path and shadow data."""
        try:
            self.conn.commit()
        except Exception as e:
            logger.error("Telemetry commit failed", error=str(e))


# ── Helpers ───────────────────────────────────────────────────────

def _get_session(timestamp_str: str) -> str:
    """Classify UTC timestamp into trading session."""
    try:
        if isinstance(timestamp_str, datetime):
            hour = timestamp_str.hour
        else:
            dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            hour = dt.hour
    except Exception:
        return "unknown"

    if 0 <= hour < 8:
        return "asia"
    elif 8 <= hour < 14:
        return "london"
    elif 14 <= hour < 21:
        return "ny"
    else:
        return "late_ny"

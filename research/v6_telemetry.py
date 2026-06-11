"""
V6 Research Telemetry — Safe Observational Instrumentation Layer.

This module is PURELY OBSERVATIONAL.
It uses an asyncio bounded queue to offload all database disk writes to a
background thread, ensuring telemetry operations NEVER block or crash the
primary trading loop.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.logging_setup import get_logger

logger = get_logger("v6_telemetry")

TELEMETRY_DB = Path("storage/v6_telemetry.db")
FALLBACK_SPOOL = Path("storage/telemetry_fallback.jsonl")


class TelemetryDB:
    """SQLite database for research telemetry. Purely queue-based, non-blocking and safe."""

    def __init__(self, path: Path = TELEMETRY_DB, max_queue_size: int = 2000):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        
        # Safe sync schema initialization at startup
        try:
            self._init_schema_sync()
        except Exception as e:
            logger.error("Failed to initialize telemetry schema (non-fatal)", error=str(e))

        # Bounded asyncio Queue
        self.queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=max_queue_size)
        self._worker_task = asyncio.create_task(self._worker_loop())
        logger.info("Safe Telemetry DB system initialized with background worker task")

    def _init_schema_sync(self):
        conn = sqlite3.connect(str(self.path))
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
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
                    confirmations TEXT,
                    n_confirmations INTEGER,
                    vol_z REAL,
                    imb_z REAL,
                    atr REAL,
                    atr_pct REAL,
                    risk_pct REAL,
                    range_high REAL,
                    ema_value REAL,
                    session TEXT,
                    equity_at_entry REAL,
                    open_positions_at_entry INTEGER,
                    btc_price REAL,
                    created_at TEXT DEFAULT (datetime('now')),
                    is_experimental INTEGER DEFAULT 0
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
                    actual_exit_r REAL,
                    actual_exit_bar INTEGER,
                    delta_r REAL,
                    post_trigger_mfe REAL,
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
                    mfe_bar INTEGER,
                    mae_bar INTEGER,
                    exit_session TEXT,
                    r_at_midpoint REAL,
                    structural_invalidation_count INTEGER DEFAULT 0,
                    momentum_reversal_count INTEGER DEFAULT 0,
                    optimal_exit_r REAL,
                    exit_efficiency REAL
                );

                -- Post-exit price tracking (continuation analysis)
                CREATE TABLE IF NOT EXISTS post_exit (
                    trade_uuid TEXT NOT NULL,
                    bars_after INTEGER NOT NULL,
                    timestamp TEXT,
                    price REAL,
                    hypothetical_r REAL,
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

                -- Hypothetical entry filters (shadow — never blocks live)
                CREATE TABLE IF NOT EXISTS shadow_entry_filters (
                    trade_uuid TEXT NOT NULL,
                    filter_name TEXT NOT NULL,
                    would_take INTEGER NOT NULL,
                    PRIMARY KEY (trade_uuid, filter_name)
                );
            """)
            for col, typ in (
                ("strategy_version", "TEXT DEFAULT ''"),
                ("entry_hour", "INTEGER"),
                ("breakout_distance_pct", "REAL"),
            ):
                try:
                    conn.execute(f"ALTER TABLE trade_entries ADD COLUMN {col} {typ}")
                except sqlite3.OperationalError:
                    pass
            conn.commit()
        finally:
            conn.close()

    def enqueue_write(self, action: str, data: dict[str, Any]):
        """Non-blocking enqueuing of telemetry writes. Drops on overflow to protect loop."""
        try:
            self.queue.put_nowait({"action": action, "data": data})
        except asyncio.QueueFull:
            logger.error("Telemetry queue full! Dropping item to prevent event loop delay.", action=action)
            # Immediate background thread fallback logging to ensure zero execution blockage
            asyncio.create_task(asyncio.to_thread(self._fallback_write_sync, action, data))
        except Exception as e:
            logger.error("Telemetry enqueue failed (non-fatal)", error=str(e))

    async def _worker_loop(self):
        while True:
            try:
                item = await self.queue.get()
                if item is None:
                    self.queue.task_done()
                    break

                action = item["action"]
                data = item["data"]

                # Perform actual SQLite disk write outside the main asyncio thread
                success = await asyncio.to_thread(self._write_db_sync, action, data)
                if not success:
                    # Write to fallback JSONL log
                    await asyncio.to_thread(self._fallback_write_sync, action, data)

                self.queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Telemetry worker loop experienced an error (non-fatal)", error=str(e))
                await asyncio.sleep(1)

    def _write_db_sync(self, action: str, data: dict[str, Any]) -> bool:
        """Executes in background thread pool."""
        conn = None
        try:
            conn = sqlite3.connect(str(self.path), timeout=5.0)
            conn.execute("PRAGMA journal_mode=WAL")
            cursor = conn.cursor()
            
            if action == "log_entry":
                sig_data = data["signal_data"]
                eng_state = data["engine_state"]
                confirms = sig_data.get("confirmations", {})
                session = _get_session(data["entry_time"])
                risk_per_unit = abs(data["entry_price"] - data["stop_price"])
                atr = sig_data.get("atr", 0)
                atr_pct = (atr / data["entry_price"] * 100) if data["entry_price"] > 0 else 0

                entry_hour = _parse_hour(data["entry_time"])
                bd_pct = sig_data.get("breakout_distance_pct", 0)
                strat_ver = sig_data.get("strategy_version", "")

                cursor.execute("""
                    INSERT OR REPLACE INTO trade_entries (
                        trade_uuid, symbol, side, entry_time, entry_price, stop_price,
                        risk_per_unit, decile, aggression, cascade_strength, cascade_active,
                        liq_direction_imb, ret_5d, confirmations, n_confirmations, vol_z,
                        imb_z, atr, atr_pct, risk_pct, range_high, ema_value, session,
                        equity_at_entry, open_positions_at_entry, btc_price, is_experimental,
                        strategy_version, entry_hour, breakout_distance_pct
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    data["trade_uuid"], data["symbol"], data["side"], data["entry_time"],
                    data["entry_price"], data["stop_price"], risk_per_unit,
                    sig_data.get("decile", 0), sig_data.get("aggression_score", 0),
                    sig_data.get("cascade_strength", 0),
                    1 if eng_state.get("cascade_active", False) else 0,
                    eng_state.get("liq_direction_imb", 0), eng_state.get("ret_5d", 0),
                    json.dumps(confirms), sum(1 for v in confirms.values() if v),
                    sig_data.get("vol_z", 0), sig_data.get("imb_z", 0), atr, atr_pct,
                    sig_data.get("risk_pct", 0), sig_data.get("range_high", 0),
                    sig_data.get("ema_value", 0), session, data["equity"],
                    data["open_count"], data["btc_price"], 1 if data.get("is_experimental") else 0,
                    strat_ver, entry_hour, bd_pct,
                ))

            elif action == "log_shadow_filters":
                for name, would_take in data["filters"].items():
                    cursor.execute(
                        "INSERT OR REPLACE INTO shadow_entry_filters "
                        "(trade_uuid, filter_name, would_take) VALUES (?, ?, ?)",
                        (data["trade_uuid"], name, 1 if would_take else 0),
                    )
                
            elif action == "log_r_point":
                cursor.execute("""
                    INSERT OR REPLACE INTO r_path (
                        trade_uuid, bar_index, timestamp, price, unrealized_r, mae_so_far,
                        mfe_so_far, atr, consecutive_red, above_ema, above_range_high,
                        vol_trail_level, struct_trail_level
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    data["trade_uuid"], data["bar_index"], data["timestamp"], data["price"],
                    data["unrealized_r"], data["mae_so_far"], data["mfe_so_far"],
                    data["atr"], data["consecutive_red"], 1 if data["above_ema"] else 0,
                    1 if data["above_range_high"] else 0, data["vol_trail_level"],
                    data["struct_trail_level"]
                ))
                
            elif action == "log_shadow_trigger":
                cursor.execute("""
                    INSERT OR IGNORE INTO shadow_exits (
                        trade_uuid, shadow_name, trigger_bar, trigger_time, trigger_price, shadow_r
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    data["trade_uuid"], data["shadow_name"], data["trigger_bar"],
                    data["trigger_time"], data["trigger_price"], data["shadow_r"]
                ))
                
            elif action == "finalize_shadows":
                cursor.execute("""
                    UPDATE shadow_exits
                    SET actual_exit_r = ?,
                        actual_exit_bar = ?,
                        delta_r = shadow_r - ?
                    WHERE trade_uuid = ?
                      AND actual_exit_r IS NULL
                """, (
                    data["actual_exit_r"], data["actual_exit_bar"],
                    data["actual_exit_r"], data["trade_uuid"]
                ))
                
            elif action == "log_exit":
                exit_efficiency = (data["pnl_r"] / data["mfe"]) if data["mfe"] > 0 else 0
                session = _get_session(data["exit_time"])
                cursor.execute("""
                    INSERT OR REPLACE INTO exit_attribution (
                        trade_uuid, exit_time, exit_price, exit_reason, pnl_r, hold_bars,
                        mae, mfe, mfe_bar, mae_bar, exit_session, r_at_midpoint,
                        structural_invalidation_count, momentum_reversal_count,
                        optimal_exit_r, exit_efficiency
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    data["trade_uuid"], data["exit_time"], data["exit_price"], data["exit_reason"],
                    data["pnl_r"], data["hold_bars"], data["mae"], data["mfe"],
                    data["mfe_bar"], data["mae_bar"], session, data["r_at_midpoint"],
                    data["structural_invalidation_count"], data["momentum_reversal_count"],
                    data["mfe"], exit_efficiency
                ))
                
            elif action == "log_post_exit_point":
                cursor.execute("""
                    INSERT OR IGNORE INTO post_exit (
                        trade_uuid, bars_after, timestamp, price, hypothetical_r
                    ) VALUES (?, ?, ?, ?, ?)
                """, (
                    data["trade_uuid"], data["bars_after"], data["timestamp"],
                    data["price"], data["hypothetical_r"]
                ))
                
            elif action == "log_regime":
                ts = datetime.now(timezone.utc).isoformat()
                cursor.execute("""
                    INSERT OR REPLACE INTO regime_snapshots (
                        timestamp, btc_price, n_cascades_active, n_open_positions, total_symbols, equity
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    ts, data["btc_price"], data["n_cascades"],
                    data["n_positions"], data["n_symbols"], data["equity"]
                ))
                
            conn.commit()
            return True
        except Exception as e:
            logger.error("SQLite telemetry database background write failed", action=action, error=str(e))
            return False
        finally:
            if conn:
                conn.close()

    def _fallback_write_sync(self, action: str, data: dict[str, Any]):
        """Runs in background thread pool. Writes telemetry flat log on DB error."""
        try:
            FALLBACK_SPOOL.parent.mkdir(parents=True, exist_ok=True)
            with open(FALLBACK_SPOOL, "a") as f:
                record = {
                    "action": action,
                    "data": data,
                    "logged_at": datetime.now(timezone.utc).isoformat()
                }
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            logger.critical("Telemetry fallback file spooler failed completely!", error=str(e))

    # ── Wrapper APIs to preserve perfect signature compatibility with main bot loop ──

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
        try:
            data = {
                "trade_uuid": trade_uuid,
                "symbol": symbol,
                "side": side,
                "entry_time": entry_time,
                "entry_price": entry_price,
                "stop_price": stop_price,
                "signal_data": signal_data,
                "engine_state": engine_state,
                "equity": equity,
                "open_count": open_count,
                "btc_price": btc_price,
                "is_experimental": is_experimental,
            }
            self.enqueue_write("log_entry", data)
        except Exception as e:
            logger.error("Telemetry API call failed (non-fatal)", error=str(e))

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
        try:
            data = {
                "trade_uuid": trade_uuid,
                "bar_index": bar_index,
                "timestamp": timestamp,
                "price": price,
                "unrealized_r": unrealized_r,
                "mae_so_far": mae_so_far,
                "mfe_so_far": mfe_so_far,
                "atr": atr,
                "consecutive_red": consecutive_red,
                "above_ema": above_ema,
                "above_range_high": above_range_high,
                "vol_trail_level": vol_trail_level,
                "struct_trail_level": struct_trail_level,
            }
            self.enqueue_write("log_r_point", data)
        except Exception as e:
            logger.error("Telemetry API call failed (non-fatal)", error=str(e))

    def log_shadow_trigger(
        self,
        trade_uuid: str,
        shadow_name: str,
        trigger_bar: int,
        trigger_time: str,
        trigger_price: float,
        shadow_r: float,
    ):
        try:
            data = {
                "trade_uuid": trade_uuid,
                "shadow_name": shadow_name,
                "trigger_bar": trigger_bar,
                "trigger_time": trigger_time,
                "trigger_price": trigger_price,
                "shadow_r": shadow_r,
            }
            self.enqueue_write("log_shadow_trigger", data)
        except Exception as e:
            logger.error("Telemetry API call failed (non-fatal)", error=str(e))

    def finalize_shadows(self, trade_uuid: str, actual_exit_r: float, actual_exit_bar: int):
        try:
            data = {
                "trade_uuid": trade_uuid,
                "actual_exit_r": actual_exit_r,
                "actual_exit_bar": actual_exit_bar,
            }
            self.enqueue_write("finalize_shadows", data)
        except Exception as e:
            logger.error("Telemetry API call failed (non-fatal)", error=str(e))

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
        try:
            data = {
                "trade_uuid": trade_uuid,
                "exit_time": exit_time,
                "exit_price": exit_price,
                "exit_reason": exit_reason,
                "pnl_r": pnl_r,
                "hold_bars": hold_bars,
                "mae": mae,
                "mfe": mfe,
                "mfe_bar": mfe_bar,
                "mae_bar": mae_bar,
                "r_at_midpoint": r_at_midpoint,
                "structural_invalidation_count": structural_invalidation_count,
                "momentum_reversal_count": momentum_reversal_count,
            }
            self.enqueue_write("log_exit", data)
        except Exception as e:
            logger.error("Telemetry API call failed (non-fatal)", error=str(e))

    def log_post_exit_point(
        self,
        trade_uuid: str,
        bars_after: int,
        timestamp: str,
        price: float,
        hypothetical_r: float,
    ):
        try:
            data = {
                "trade_uuid": trade_uuid,
                "bars_after": bars_after,
                "timestamp": timestamp,
                "price": price,
                "hypothetical_r": hypothetical_r,
            }
            self.enqueue_write("log_post_exit_point", data)
        except Exception as e:
            logger.error("Telemetry API call failed (non-fatal)", error=str(e))

    def log_regime(
        self,
        btc_price: float,
        n_cascades: int,
        n_positions: int,
        n_symbols: int,
        equity: float,
    ):
        try:
            data = {
                "btc_price": btc_price,
                "n_cascades": n_cascades,
                "n_positions": n_positions,
                "n_symbols": n_symbols,
                "equity": equity,
            }
            self.enqueue_write("log_regime", data)
        except Exception as e:
            logger.error("Telemetry API call failed (non-fatal)", error=str(e))

    def log_shadow_filters(self, trade_uuid: str, filters: dict[str, bool]):
        try:
            self.enqueue_write("log_shadow_filters", {
                "trade_uuid": trade_uuid,
                "filters": filters,
            })
        except Exception as e:
            logger.error("Telemetry API call failed (non-fatal)", error=str(e))

    def commit(self):
        """No-op wrapper (handled implicitly on the background worker)."""
        pass


# ── Helpers ──

def _parse_hour(timestamp_str: str) -> int:
    try:
        if isinstance(timestamp_str, datetime):
            return timestamp_str.hour
        dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        return dt.hour
    except (ValueError, TypeError):
        return -1


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

"""
Bitana SQLite Persistence Layer

Architecture decision AD-2: Single async writer queue.
All writes go through an asyncio.Queue consumed by a single writer task.
Reads are concurrent (WAL mode). No transaction retry needed.

Tables: positions, trades, signals, orders, risk_state, brake_state, system_state
All linked by trade_uuid (AD-8).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from core.logging_setup import get_logger

logger = get_logger("storage")

# SQL for table creation
_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_uuid TEXT NOT NULL,
    engine TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    signal_time TEXT NOT NULL,
    entry_price REAL NOT NULL,
    stop_price REAL NOT NULL,
    signal_data TEXT DEFAULT '{}',
    confidence REAL DEFAULT 1.0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_uuid TEXT NOT NULL,
    client_order_id TEXT NOT NULL UNIQUE,
    exchange_order_id TEXT DEFAULT '',
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    order_type TEXT DEFAULT 'MARKET',
    status TEXT NOT NULL,
    requested_qty REAL NOT NULL,
    filled_qty REAL DEFAULT 0.0,
    avg_fill_price REAL DEFAULT 0.0,
    commission REAL DEFAULT 0.0,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    raw TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS positions (
    trade_uuid TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    engine TEXT NOT NULL,
    state TEXT NOT NULL,
    entry_price REAL DEFAULT 0.0,
    entry_time TEXT,
    quantity REAL DEFAULT 0.0,
    leverage INTEGER DEFAULT 1,
    stop_price REAL DEFAULT 0.0,
    initial_stop REAL DEFAULT 0.0,
    risk_r REAL DEFAULT 0.0,
    tp1_price REAL DEFAULT 0.0,
    tp1_hit INTEGER DEFAULT 0,
    trailing_stop REAL DEFAULT 0.0,
    trailing_active INTEGER DEFAULT 0,
    realized_pnl REAL DEFAULT 0.0,
    unrealized_pnl REAL DEFAULT 0.0,
    commission_total REAL DEFAULT 0.0,
    funding_fees REAL DEFAULT 0.0,
    candles_held INTEGER DEFAULT 0,
    externally_managed INTEGER DEFAULT 0,
    client_order_ids TEXT DEFAULT '[]',
    signal_data TEXT DEFAULT '{}',
    entry_atr REAL DEFAULT 0.0,
    peak_mfe_atr REAL DEFAULT 0.0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_uuid TEXT NOT NULL UNIQUE,
    timestamp TEXT NOT NULL,
    engine TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    quantity REAL NOT NULL,
    leverage INTEGER NOT NULL,
    initial_stop REAL NOT NULL,
    commission REAL DEFAULT 0.0,
    slippage_est REAL DEFAULT 0.0,
    funding_fees REAL DEFAULT 0.0,
    pnl_usd REAL NOT NULL,
    pnl_r REAL NOT NULL,
    hold_time_s REAL NOT NULL,
    hold_candles INTEGER DEFAULT 0,
    exit_reason TEXT NOT NULL,
    signal_data TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS risk_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    peak_equity REAL DEFAULT 0.0,
    current_equity REAL DEFAULT 0.0,
    current_drawdown_pct REAL DEFAULT 0.0,
    risk_pct_active REAL DEFAULT 1.5,
    consecutive_losses INTEGER DEFAULT 0,
    reduced_risk_trades_remaining INTEGER DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS brake_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    daily_realized_loss REAL DEFAULT 0.0,
    daily_reset_date TEXT DEFAULT '',
    is_paused INTEGER DEFAULT 0,
    pause_reason TEXT DEFAULT '',
    is_shutdown INTEGER DEFAULT 0,
    shutdown_reason TEXT DEFAULT '',
    manual_review_required INTEGER DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS system_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS wallet_transfers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tran_id TEXT,
    asset TEXT NOT NULL,
    amount REAL NOT NULL,
    direction TEXT NOT NULL,
    income_type TEXT NOT NULL,
    event_time_ms INTEGER NOT NULL,
    info TEXT DEFAULT '',
    equity_after REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(tran_id, event_time_ms, amount)
);

CREATE INDEX IF NOT EXISTS idx_signals_trade_uuid ON signals(trade_uuid);
CREATE INDEX IF NOT EXISTS idx_orders_trade_uuid ON orders(trade_uuid);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_engine ON trades(engine);
CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);
CREATE INDEX IF NOT EXISTS idx_positions_state ON positions(state);
"""


class Database:
    """Async SQLite database with single-writer queue (AD-2)."""

    def __init__(self, db_path: str = "data/bitana.db") -> None:
        self._db_path = Path(db_path)
        self._db: Optional[aiosqlite.Connection] = None
        self._write_queue: asyncio.Queue = asyncio.Queue()
        self._writer_task: Optional[asyncio.Task] = None
        self._running = False

    async def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self._db_path))
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA busy_timeout=5000")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.executescript(_SCHEMA)
        await self._migrate_positions()
        await self._db.commit()
        self._running = True
        self._writer_task = asyncio.create_task(self._writer_loop())
        logger.info("Database initialized", path=str(self._db_path))

    async def _migrate_positions(self) -> None:
        assert self._db is not None
        cols = set()
        async with self._db.execute("PRAGMA table_info(positions)") as cursor:
            rows = await cursor.fetchall()
            cols = {row[1] for row in rows}
        for col, typ in (
            ("signal_data", "TEXT DEFAULT '{}'"),
            ("entry_atr", "REAL DEFAULT 0.0"),
            ("peak_mfe_atr", "REAL DEFAULT 0.0"),
        ):
            if col not in cols:
                await self._db.execute(f"ALTER TABLE positions ADD COLUMN {col} {typ}")

    async def close(self) -> None:
        self._running = False
        if self._writer_task:
            await self._write_queue.put(None)  # sentinel
            await self._writer_task
        if self._db:
            await self._db.close()
        logger.info("Database closed")

    async def _writer_loop(self) -> None:
        """Single writer consuming the write queue."""
        while self._running or not self._write_queue.empty():
            item = await self._write_queue.get()
            if item is None:
                break
            sql, params, future = item
            try:
                assert self._db is not None
                await self._db.execute(sql, params)
                await self._db.commit()
                if future and not future.done():
                    future.set_result(True)
            except Exception as e:
                logger.error("DB write error", sql=sql[:100], error=str(e))
                if future and not future.done():
                    future.set_exception(e)

    async def _write(self, sql: str, params: tuple = ()) -> None:
        """Submit a write to the queue and wait for completion."""
        future = asyncio.get_event_loop().create_future()
        await self._write_queue.put((sql, params, future))
        await future

    async def _read(self, sql: str, params: tuple = ()) -> list[dict]:
        """Execute a read query, return list of dicts."""
        assert self._db is not None
        self._db.row_factory = aiosqlite.Row
        async with self._db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def _read_one(self, sql: str, params: tuple = ()) -> Optional[dict]:
        rows = await self._read(sql, params)
        return rows[0] if rows else None

    # ------------------------------------------------------------------
    # Signal CRUD
    # ------------------------------------------------------------------

    async def save_signal(self, signal) -> None:
        from core.models import Signal
        s: Signal = signal
        await self._write(
            """INSERT INTO signals (trade_uuid, engine, symbol, side,
               signal_time, entry_price, stop_price, signal_data, confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (s.trade_uuid, s.engine.value, s.symbol, s.side.value,
             s.timestamp.isoformat(), s.entry_price, s.stop_price,
             json.dumps(s.signal_data, default=str), s.confidence),
        )

    # ------------------------------------------------------------------
    # Order CRUD
    # ------------------------------------------------------------------

    async def save_order(self, order) -> None:
        from core.models import OrderResult
        o: OrderResult = order
        await self._write(
            """INSERT OR REPLACE INTO orders
               (trade_uuid, client_order_id, exchange_order_id, symbol,
                side, status, requested_qty, filled_qty, avg_fill_price,
                commission, timestamp, raw)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (o.trade_uuid, o.client_order_id, o.exchange_order_id,
             o.symbol, o.side.value, o.status.value, o.requested_qty,
             o.filled_qty, o.avg_fill_price, o.commission,
             o.timestamp.isoformat(), json.dumps(o.raw, default=str)),
        )

    # ------------------------------------------------------------------
    # Position CRUD
    # ------------------------------------------------------------------

    async def save_position(self, pos) -> None:
        from core.models import Position
        p: Position = pos
        await self._write(
            """INSERT OR REPLACE INTO positions
               (trade_uuid, symbol, side, engine, state, entry_price,
                entry_time, quantity, leverage, stop_price, initial_stop,
                risk_r, tp1_price, tp1_hit, trailing_stop, trailing_active,
                realized_pnl, unrealized_pnl, commission_total,
                funding_fees, candles_held, externally_managed,
                client_order_ids, signal_data, entry_atr, peak_mfe_atr,
                created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (p.trade_uuid, p.symbol, p.side.value, p.engine.value,
             p.state.value, p.entry_price,
             p.entry_time.isoformat() if p.entry_time else None,
             p.quantity, p.leverage, p.stop_price, p.initial_stop,
             p.risk_r, p.tp1_price, int(p.tp1_hit), p.trailing_stop,
             int(p.trailing_active), p.realized_pnl, p.unrealized_pnl,
             p.commission_total, p.funding_fees, p.candles_held,
             int(p.externally_managed),
             json.dumps(p.client_order_ids),
             json.dumps(getattr(p, "signal_data", {}) or {}, default=str),
             float(getattr(p, "entry_atr", 0.0) or 0.0),
             float(getattr(p, "peak_mfe_atr", 0.0) or 0.0),
             p.created_at.isoformat(),
             p.updated_at.isoformat()),
        )

    async def get_open_positions(self) -> list[dict]:
        return await self._read(
            "SELECT * FROM positions WHERE state NOT IN ('CLOSED','CANCELLED')"
        )

    async def delete_position(self, trade_uuid: str) -> None:
        await self._write(
            "DELETE FROM positions WHERE trade_uuid = ?", (trade_uuid,)
        )

    # ------------------------------------------------------------------
    # Trade record CRUD
    # ------------------------------------------------------------------

    async def save_trade(self, trade) -> None:
        from core.models import TradeRecord
        t: TradeRecord = trade
        await self._write(
            """INSERT OR REPLACE INTO trades
               (trade_uuid, timestamp, engine, symbol, side, entry_price,
                exit_price, quantity, leverage, initial_stop, commission,
                slippage_est, funding_fees, pnl_usd, pnl_r, hold_time_s,
                hold_candles, exit_reason, signal_data)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (t.trade_uuid, t.timestamp.isoformat(), t.engine.value,
             t.symbol, t.side.value, t.entry_price, t.exit_price,
             t.quantity, t.leverage, t.initial_stop, t.commission,
             t.slippage_est, t.funding_fees, t.pnl_usd, t.pnl_r,
             t.hold_time_s, t.hold_candles, t.exit_reason,
             json.dumps(t.signal_data, default=str)),
        )

    async def get_trades(
        self, symbol: str | None = None, engine: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        sql = "SELECT * FROM trades WHERE 1=1"
        params: list = []
        if symbol:
            sql += " AND symbol = ?"
            params.append(symbol)
        if engine:
            sql += " AND engine = ?"
            params.append(engine)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        return await self._read(sql, tuple(params))

    async def get_recent_trades(self, n: int = 20) -> list[dict]:
        return await self._read(
            "SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?", (n,)
        )

    # ------------------------------------------------------------------
    # Risk state
    # ------------------------------------------------------------------

    async def save_risk_state(self, state) -> None:
        from core.models import RiskState
        import json
        s: RiskState = state
        await self._write(
            """INSERT OR REPLACE INTO risk_state
               (id, peak_equity, current_equity, current_drawdown_pct,
                risk_pct_active, consecutive_losses,
                reduced_risk_trades_remaining, updated_at)
               VALUES (1, ?, ?, ?, ?, ?, ?, ?)""",
            (s.peak_equity, s.current_equity, s.current_drawdown_pct,
             s.risk_pct_active, json.dumps(s.consecutive_losses),
             s.reduced_risk_trades_remaining,
             datetime.utcnow().isoformat()),
        )

    async def get_risk_state(self) -> Optional[dict]:
        import json
        row = await self._read_one("SELECT * FROM risk_state WHERE id = 1")
        if row:
            row = dict(row)
            if isinstance(row.get("consecutive_losses"), str):
                row["consecutive_losses"] = json.loads(row["consecutive_losses"])
        return row

    # ------------------------------------------------------------------
    # Brake state
    # ------------------------------------------------------------------

    async def save_brake_state(self, state) -> None:
        from core.models import BrakeState
        s: BrakeState = state
        await self._write(
            """INSERT OR REPLACE INTO brake_state
               (id, daily_realized_loss, daily_reset_date,
                is_paused, pause_reason,
                is_shutdown, shutdown_reason, manual_review_required,
                updated_at)
               VALUES (1,?,?,?,?,?,?,?,?)""",
            (s.daily_realized_loss, s.daily_reset_date,
             int(s.is_paused), s.pause_reason,
             int(s.is_shutdown), s.shutdown_reason,
             int(s.manual_review_required),
             datetime.utcnow().isoformat()),
        )

    async def get_brake_state(self) -> Optional[dict]:
        return await self._read_one("SELECT * FROM brake_state WHERE id = 1")

    # ------------------------------------------------------------------
    # System state (key-value)
    # ------------------------------------------------------------------

    async def set_system_state(self, key: str, value: str) -> None:
        await self._write(
            """INSERT OR REPLACE INTO system_state (key, value, updated_at)
               VALUES (?, ?, ?)""",
            (key, value, datetime.utcnow().isoformat()),
        )

    async def get_system_state(self, key: str) -> Optional[str]:
        row = await self._read_one(
            "SELECT value FROM system_state WHERE key = ?", (key,)
        )
        return row["value"] if row else None

    # ------------------------------------------------------------------
    # Futures wallet transfers (spot ↔ USDT-M)
    # ------------------------------------------------------------------

    async def save_wallet_transfer(
        self,
        *,
        tran_id: str | None,
        asset: str,
        amount: float,
        direction: str,
        income_type: str,
        event_time_ms: int,
        info: str = "",
        equity_after: float | None = None,
    ) -> bool:
        """Persist a futures wallet transfer. Returns False if duplicate."""
        tid = (tran_id or "").strip() or f"{event_time_ms}:{amount}:{asset}"
        existing = await self._read_one(
            """SELECT id FROM wallet_transfers
               WHERE tran_id = ? AND event_time_ms = ? AND amount = ?""",
            (tid, event_time_ms, amount),
        )
        if existing:
            return False
        await self._write(
            """INSERT INTO wallet_transfers
               (tran_id, asset, amount, direction, income_type,
                event_time_ms, info, equity_after, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                tid,
                asset,
                amount,
                direction,
                income_type,
                event_time_ms,
                info,
                equity_after,
                datetime.utcnow().isoformat(),
            ),
        )
        return True

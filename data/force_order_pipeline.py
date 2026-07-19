"""Force-order WebSocket ingestion + intraday burst stats for burst-follow entries."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import websockets

from core.logging_setup import get_logger
from core.models import Candle
from engines.liq_burst_follow_engine import BurstFollowState
from engines.liq_cluster_engine_v5 import CascadeTracker, LiqClusterEngineV5

logger = get_logger("force_order_pipeline")

LIQ_CACHE_MAX_DAYS = 7
WS_FLUSH_INTERVAL_S = 5.0
FORCE_ORDER_WS_URL = "wss://fstream.binance.com/market/ws/!forceOrder@arr"


class ForceOrderPipeline:
    """Captures !forceOrder@arr, aggregates daily liq cache, feeds cascade engine."""

    def __init__(
        self,
        db_path: Path = Path("storage/force_orders.db"),
        symbols: list[str] | None = None,
        *,
        read_only: bool = False,
        liq_cache_db_path: Path | None = None,
    ) -> None:
        self.symbols = set(symbols or [])
        self.read_only = read_only
        db_path = Path(db_path)
        liq_path = Path(liq_cache_db_path or db_path)
        if not read_only:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(str(db_path), timeout=30.0)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA busy_timeout=30000")
            self._init_db()
            self.liq_conn = self.conn
        else:
            uri = f"file:{db_path.resolve()}?mode=ro"
            self.conn = sqlite3.connect(uri, uri=True, timeout=30.0)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA query_only=ON")
            liq_uri = f"file:{liq_path.resolve()}?mode=ro"
            self.liq_conn = sqlite3.connect(liq_uri, uri=True, timeout=30.0)
            self.liq_conn.row_factory = sqlite3.Row
            self.liq_conn.execute("PRAGMA query_only=ON")
        self.cascade_engine = LiqClusterEngineV5()
        self._dirty_symbols: set[str] = set()
        self._last_flush = time.monotonic()
        self._last_prices: dict[str, float] = {}
        self._running = False
        self.refresh_cascades(list(self.symbols))

    def _init_db(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS force_order_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_time_ms INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                qty REAL NOT NULL,
                price REAL NOT NULL,
                volume_usd REAL NOT NULL,
                received_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_foe_symbol_time
                ON force_order_events(symbol, event_time_ms);

            CREATE TABLE IF NOT EXISTS liq_cache (
                symbol TEXT NOT NULL,
                date TEXT NOT NULL,
                total_liq REAL NOT NULL,
                long_liq REAL NOT NULL,
                short_liq REAL NOT NULL,
                close REAL NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (symbol, date)
            );
            """
        )
        self.conn.commit()

    def close(self) -> None:
        try:
            if not self.read_only:
                self.conn.commit()
            self.conn.close()
            if self.liq_conn is not self.conn:
                self.liq_conn.close()
        except Exception:
            pass

    def set_price(self, symbol: str, price: float) -> None:
        if price > 0:
            self._last_prices[symbol] = price

    def intraday_burst_stats(self, symbol: str, candle: Candle) -> dict:
        """Rolling force-order stats ending at the candle close."""
        bar_time = getattr(candle, "close_time", None) or datetime.now(timezone.utc)
        if bar_time.tzinfo is None:
            bar_time = bar_time.replace(tzinfo=timezone.utc)
        end_ms = int(bar_time.timestamp() * 1000)
        stats: dict[str, float | int] = {}
        for minutes in (15, 30, 60):
            start_ms = end_ms - minutes * 60 * 1000
            row = self.conn.execute(
                "SELECT COALESCE(SUM(volume_usd),0), COUNT(*), COALESCE(MAX(volume_usd),0) "
                "FROM force_order_events WHERE symbol=? AND event_time_ms>? AND event_time_ms<=?",
                (symbol, start_ms, end_ms),
            ).fetchone()
            stats[f"volume_{minutes}m"] = float(row[0] or 0.0)
            stats[f"events_{minutes}m"] = int(row[1] or 0)
            stats[f"max_order_usd_{minutes}m"] = float(row[2] or 0.0)

        start_30m = end_ms - 30 * 60 * 1000
        long_liq = self.conn.execute(
            "SELECT COALESCE(SUM(volume_usd),0) FROM force_order_events "
            "WHERE symbol=? AND event_time_ms>? AND event_time_ms<=? AND side='SELL'",
            (symbol, start_30m, end_ms),
        ).fetchone()[0]
        short_liq = self.conn.execute(
            "SELECT COALESCE(SUM(volume_usd),0) FROM force_order_events "
            "WHERE symbol=? AND event_time_ms>? AND event_time_ms<=? AND side='BUY'",
            (symbol, start_30m, end_ms),
        ).fetchone()[0]
        total_30m = float(long_liq or 0.0) + float(short_liq or 0.0)
        stats["long_liq_30m"] = float(long_liq or 0.0)
        stats["short_liq_30m"] = float(short_liq or 0.0)
        stats["imbalance_30m"] = (
            (stats["long_liq_30m"] - stats["short_liq_30m"]) / total_30m
            if total_30m > 0 else 0.0
        )
        return stats

    @staticmethod
    def sync_burst_state(state: BurstFollowState, burst: dict, symbol: str, engine: LiqClusterEngineV5) -> None:
        state.burst_volume_30m = float(burst.get("volume_30m", 0.0))
        state.burst_events_30m = int(burst.get("events_30m", 0))
        state.liq_imbalance_30m = float(burst.get("imbalance_30m", 0.0))
        v5 = engine._get_state(symbol)
        state.cascade_strength = float(v5.cascade_strength)
        state.liq_direction_imb = float(v5.liq_direction_imb)
        state.ret_5d = float(v5.ret_5d)
        state.cascade_active = bool(v5.cascade_active)

    def refresh_cascades(self, symbols: list[str] | None = None) -> None:
        """Reload cascade state from shared liq_cache (read-only VPS mode)."""
        for symbol in symbols or list(self.symbols):
            if symbol in self.symbols:
                self._feed_engine_liq(symbol)

    def _feed_engine_liq(self, symbol: str) -> None:
        min_date = (datetime.now(timezone.utc) - timedelta(days=LIQ_CACHE_MAX_DAYS)).strftime("%Y-%m-%d")
        rows = self.liq_conn.execute(
            "SELECT date, total_liq, long_liq, short_liq, close FROM liq_cache "
            "WHERE symbol=? AND date>=? ORDER BY date",
            (symbol, min_date),
        ).fetchall()
        if not rows:
            return
        cached = [
            {
                "date": r["date"],
                "total_liq": r["total_liq"],
                "long_liq": r["long_liq"],
                "short_liq": r["short_liq"],
                "close": r["close"],
            }
            for r in rows
        ]
        self.cascade_engine._cascades[symbol] = CascadeTracker()
        self.cascade_engine.update_daily_liq(symbol, cached)

    def _flush_dirty(self) -> None:
        if not self._dirty_symbols:
            return
        dirty = list(self._dirty_symbols)
        self._dirty_symbols.clear()
        self.conn.commit()
        for symbol in dirty:
            self._feed_engine_liq(symbol)
        logger.debug("Cascade flush", symbols=len(dirty))

    async def run_ws_loop(self, shutdown: asyncio.Event) -> None:
        if self.read_only:
            logger.info("Force-order pipeline read-only; using shared DB feed")
            while not shutdown.is_set():
                await asyncio.sleep(5)
            return

        self._running = True
        logger.info("Force-order WebSocket starting", url=FORCE_ORDER_WS_URL)
        while not shutdown.is_set():
            try:
                async with websockets.connect(
                    FORCE_ORDER_WS_URL, ping_interval=20, ping_timeout=20,
                ) as ws:
                    logger.info("Force-order WebSocket connected")
                    async for message in ws:
                        if shutdown.is_set():
                            break
                        try:
                            msg = json.loads(message)
                        except Exception:
                            continue
                        if not isinstance(msg, dict) or msg.get("e") != "forceOrder":
                            continue
                        order = msg.get("o", {})
                        symbol = order.get("s")
                        if symbol not in self.symbols:
                            continue
                        side = order.get("S")
                        qty = float(order.get("q", 0))
                        price = float(order.get("p", 0))
                        volume = qty * price
                        time_ms = int(msg.get("E", 0))
                        if volume <= 0:
                            continue

                        self.conn.execute(
                            """INSERT INTO force_order_events
                               (event_time_ms, symbol, side, qty, price, volume_usd, received_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?)""",
                            (
                                time_ms, symbol, side, qty, price, volume,
                                datetime.now(timezone.utc).isoformat(),
                            ),
                        )

                        dt = datetime.fromtimestamp(time_ms / 1000, tz=timezone.utc)
                        date_str = dt.strftime("%Y-%m-%d")
                        existing = self.conn.execute(
                            "SELECT total_liq, long_liq, short_liq FROM liq_cache WHERE symbol=? AND date=?",
                            (symbol, date_str),
                        ).fetchone()
                        if existing:
                            long_liq = existing["long_liq"] + (volume if side == "SELL" else 0.0)
                            short_liq = existing["short_liq"] + (volume if side == "BUY" else 0.0)
                        else:
                            long_liq = volume if side == "SELL" else 0.0
                            short_liq = volume if side == "BUY" else 0.0
                        total_liq = long_liq + short_liq
                        close = self._last_prices.get(symbol, price)
                        self.conn.execute(
                            """INSERT OR REPLACE INTO liq_cache
                               (symbol, date, total_liq, long_liq, short_liq, close, updated_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?)""",
                            (
                                symbol, date_str, total_liq, long_liq, short_liq, close,
                                datetime.now(timezone.utc).isoformat(),
                            ),
                        )
                        self._dirty_symbols.add(symbol)

                        now_mono = time.monotonic()
                        if now_mono - self._last_flush >= WS_FLUSH_INTERVAL_S:
                            self._flush_dirty()
                            self._last_flush = now_mono
            except asyncio.CancelledError:
                break
            except Exception as e:
                if shutdown.is_set():
                    break
                logger.error("Force-order WebSocket error, reconnecting", error=str(e))
                await asyncio.sleep(5)
        self._flush_dirty()
        self._running = False
        logger.info("Force-order WebSocket stopped")

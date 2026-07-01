"""
Bitana Forward Test — Liq-Cluster Multi-Symbol Paper Trading.

V6: All audit fixes applied.
- Fixed double bars_held increment (winners were exiting at 2x speed)
- Fixed imb_z confirmation (now uses real taker buy imbalance z-score)
- Widened aggression mapping (prevents D10 saturation on strong signals)
- Added cascade-deactivation exit tightening (1.0 ATR trail)
- Debounced WebSocket engine updates (60s intervals, not per-event)
- Batch SQLite commits + WAL mode (reduced IO churn)
- Added asyncio.Lock for engine/DB state safety
- Time-based stop_cooldown fallback (288 bars = 24h)
- Fixed equity snapshot count, self-test, duplicate key
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import shutil
import sqlite3
import sys
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import websockets
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.loader import load_config
from core.logging_setup import setup_logging, get_logger
from core.models import AlertTier, Candle, EngineType, Side, Signal
from data.binance_rest import BinanceRestClient
from data.rate_limiter import RateLimiterGroup
from engines.liq_cluster_engine_v5 import LiqClusterEngineV5, BASE_RISK_PCT, TRADE_DECILES
import engines.liq_cluster_engine_v5 as eng_mod
from research.v65_monitoring import (
    STRATEGY_VERSION,
    ASIA_SHADOW_HOURS,
    build_asia_shadow_report,
    build_session_report,
    evaluate_entry_shadow_filters,
    evaluate_promotion_status,
    parse_entry_hour,
)
from tg_bot.alerts import TelegramAlerts

# Research telemetry — purely observational, never affects trading
try:
    from research.v6_telemetry import TelemetryDB
    from research.shadow_exits import evaluate_shadows
    _HAS_TELEMETRY = True
except ImportError:
    _HAS_TELEMETRY = False

# Signal shadow — logging-only candidate-signal + forward-path collector
try:
    from research.signal_shadow import ShadowPortfolioConfig, SignalShadow
    _HAS_SIGNAL_SHADOW = True
except ImportError:
    _HAS_SIGNAL_SHADOW = False

logger = get_logger("v5_forward_test")

# ═══════════════════════════════════════════════════
# Config Loading
# ═══════════════════════════════════════════════════

CONFIG_PATH = Path(__file__).parent.parent / "config" / "v5_forward_test.yaml"

def load_v5_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)

# ═══════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════

CANDLE_HISTORY_5M = 200
POLL_INTERVAL_S = 15
TAKER_BPS = 4.5
SLIP_BPS = 2.0
DB_PATH = Path("storage/v5_forward_test.db")
FORCE_ORDER_DB_PATH = Path("storage/force_orders.db")
DAILY_REPORT_HOUR = 8
DAILY_REPORT_MINUTE = 5
NY_SESSION_REPORT_HOUR = 22
NY_SESSION_REPORT_MINUTE = 0
PAPER_EQUITY_TARGET = 9861.0

# Binance liq fetch config
LIQ_FETCH_DAYS = 7          # Binance allForceOrders default window
LIQ_CACHE_MAX_DAYS = 120    # Max days to keep in local cache
FORCE_ORDER_RETENTION_DAYS = 120  # Raw WS events kept for backtest replay
WS_ENGINE_FLUSH_INTERVAL = 60  # V6: debounce WS engine updates (seconds)

# DB column whitelist for positions
_POS_COLS = {
    "trade_uuid", "symbol", "side", "entry_price", "quantity",
    "orig_quantity", "leverage", "stop_price", "init_stop",
    "tp1_hit", "trail_active", "candles_held", "entry_time",
    "rpnl", "fees", "confirmations", "mae", "mfe",
    "aggression", "decile",
}


# ═══════════════════════════════════════════════════
# Database
# ═══════════════════════════════════════════════════

class V5Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        # V6: WAL mode for better concurrent read/write performance
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init()

    def _init(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_uuid TEXT UNIQUE,
                symbol TEXT,
                side TEXT,
                entry_time TEXT,
                exit_time TEXT,
                entry_price REAL,
                exit_price REAL,
                quantity REAL,
                leverage INTEGER,
                stop_dist REAL,
                pnl_usd REAL,
                pnl_r REAL,
                fees REAL,
                hold_candles INTEGER,
                exit_reason TEXT,
                tp1_hit INTEGER,
                equity_after REAL,
                confirmations TEXT,
                mae REAL,
                mfe REAL,
                aggression REAL,
                decile INTEGER,
                duplicate_key TEXT
            );

            CREATE TABLE IF NOT EXISTS open_positions (
                trade_uuid TEXT PRIMARY KEY,
                symbol TEXT,
                side TEXT,
                entry_price REAL,
                quantity REAL,
                orig_quantity REAL,
                leverage INTEGER,
                stop_price REAL,
                init_stop REAL,
                tp1_hit INTEGER DEFAULT 0,
                trail_active INTEGER DEFAULT 0,
                candles_held INTEGER DEFAULT 0,
                entry_time TEXT,
                rpnl REAL DEFAULT 0,
                fees REAL DEFAULT 0,
                confirmations TEXT DEFAULT '',
                mae REAL DEFAULT 0,
                mfe REAL DEFAULT 0,
                aggression REAL DEFAULT 0,
                decile INTEGER DEFAULT 5
            );

            CREATE TABLE IF NOT EXISTS state (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS duplicate_keys (
                dup_key TEXT PRIMARY KEY,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS equity_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                equity REAL,
                open_positions INTEGER,
                unrealized_r REAL
            );

            CREATE TABLE IF NOT EXISTS liq_cache (
                symbol TEXT,
                date TEXT,
                total_liq REAL,
                long_liq REAL,
                short_liq REAL,
                close REAL,
                updated_at TEXT,
                PRIMARY KEY (symbol, date)
            );

            CREATE TABLE IF NOT EXISTS shadow_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_uuid TEXT UNIQUE,
                session_tag TEXT NOT NULL DEFAULT 'asia',
                symbol TEXT,
                side TEXT,
                entry_time TEXT,
                exit_time TEXT,
                entry_price REAL,
                exit_price REAL,
                pnl_r REAL,
                exit_reason TEXT,
                hold_candles INTEGER,
                decile INTEGER,
                aggression REAL,
                mae REAL,
                mfe REAL,
                strategy_version TEXT
            );

            CREATE TABLE IF NOT EXISTS shadow_positions (
                trade_uuid TEXT PRIMARY KEY,
                symbol TEXT,
                side TEXT,
                entry_price REAL,
                init_stop REAL,
                candles_held INTEGER DEFAULT 0,
                entry_time TEXT,
                decile INTEGER,
                aggression REAL,
                mae REAL DEFAULT 0,
                mfe REAL DEFAULT 0
            );
        """)
        for col, typ in [("strategy_version", "TEXT DEFAULT ''")]:
            try:
                self.conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:
                pass
        self.conn.commit()

        # Surgical schema upgrade safety check: Ensure the is_experimental column exists in trades
        try:
            self.conn.execute("ALTER TABLE trades ADD COLUMN is_experimental INTEGER DEFAULT 0")
            self.conn.commit()
        except sqlite3.OperationalError:
            pass  # Ignored if column is already present or table doesn't exist yet


    def get_state(self, key, default=""):
        row = self.conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_state(self, key, value):
        self.conn.execute("INSERT OR REPLACE INTO state(key,value) VALUES(?,?)", (key, str(value)))
        self.conn.commit()

    def has_dup(self, key):
        return self.conn.execute("SELECT 1 FROM duplicate_keys WHERE dup_key=?", (key,)).fetchone() is not None

    def mark_dup(self, key):
        self.conn.execute("INSERT OR IGNORE INTO duplicate_keys(dup_key,created_at) VALUES(?,?)",
                          (key, datetime.now(timezone.utc).isoformat()))
        self.conn.commit()

    def save_position(self, p):
        safe = {k: v for k, v in p.items() if k in _POS_COLS}
        cols = list(safe.keys())
        vals = [safe[c] for c in cols]
        ph = ",".join(["?"] * len(cols))
        cs = ",".join(cols)
        self.conn.execute(f"INSERT OR REPLACE INTO open_positions({cs}) VALUES({ph})", vals)
        self.conn.commit()

    def get_open_positions(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM open_positions").fetchall()]

    def remove_position(self, trade_uuid):
        self.conn.execute("DELETE FROM open_positions WHERE trade_uuid=?", (trade_uuid,))
        self.conn.commit()

    def save_trade(self, t):
        cols = list(t.keys())
        vals = [t[c] for c in cols]
        ph = ",".join(["?"] * len(cols))
        cs = ",".join(cols)
        self.conn.execute(f"INSERT OR REPLACE INTO trades({cs}) VALUES({ph})", vals)
        self.conn.commit()

    def get_all_trades(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM trades ORDER BY id").fetchall()]

    def get_trades_on_date(self, date_prefix: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM trades WHERE entry_time LIKE ? ORDER BY id",
            (f"{date_prefix}%",),
        ).fetchall()]

    def get_trades_since(self, since):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM trades WHERE entry_time >= ? ORDER BY id", (since,)
        ).fetchall()]

    def sum_closed_pnl_r_since(self, since_iso: str) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(pnl_r), 0) FROM trades "
            "WHERE exit_time IS NOT NULL AND exit_time >= ?",
            (since_iso,),
        ).fetchone()
        return float(row[0]) if row else 0.0

    def save_shadow_trade(self, t: dict):
        cols = list(t.keys())
        vals = [t[c] for c in cols]
        ph = ",".join(["?"] * len(cols))
        cs = ",".join(cols)
        self.conn.execute(f"INSERT OR REPLACE INTO shadow_trades({cs}) VALUES({ph})", vals)
        self.conn.commit()

    def save_shadow_position(self, p: dict):
        self.conn.execute(
            """INSERT OR REPLACE INTO shadow_positions
               (trade_uuid, symbol, side, entry_price, init_stop, candles_held,
                entry_time, decile, aggression, mae, mfe)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                p["trade_uuid"], p["symbol"], p["side"], p["entry_price"], p["init_stop"],
                p.get("candles_held", 0), p["entry_time"], p.get("decile", 5),
                p.get("aggression", 0), p.get("mae", 0), p.get("mfe", 0),
            ),
        )
        self.conn.commit()

    def get_shadow_positions(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM shadow_positions").fetchall()]

    def remove_shadow_position(self, trade_uuid: str):
        self.conn.execute("DELETE FROM shadow_positions WHERE trade_uuid=?", (trade_uuid,))
        self.conn.commit()

    def get_all_shadow_trades(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM shadow_trades ORDER BY id"
        ).fetchall()]

    def sum_shadow_pnl_r(self) -> float:
        row = self.conn.execute("SELECT COALESCE(SUM(pnl_r), 0) FROM shadow_trades").fetchone()
        return float(row[0]) if row else 0.0

    def has_shadow_dup(self, key: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM duplicate_keys WHERE dup_key=?", (f"asia_{key}",)
        ).fetchone() is not None

    def mark_shadow_dup(self, key: str):
        self.conn.execute(
            "INSERT OR IGNORE INTO duplicate_keys(dup_key,created_at) VALUES(?,?)",
            (f"asia_{key}", datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()

    def save_equity_snapshot(self, equity, n_open, unrealized_r):
        self.conn.execute(
            "INSERT INTO equity_snapshots(timestamp,equity,open_positions,unrealized_r) VALUES(?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), equity, n_open, unrealized_r),
        )
        self.conn.commit()

    # ── Liq Cache ────────────────────────────────────────────────

    def upsert_liq_cache(self, symbol: str, date: str, total_liq: float,
                         long_liq: float, short_liq: float, close: float):
        """Insert or update a daily liq cache entry."""
        self.conn.execute(
            """INSERT OR REPLACE INTO liq_cache
               (symbol, date, total_liq, long_liq, short_liq, close, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (symbol, date, total_liq, long_liq, short_liq, close,
             datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()

    def get_liq_cache(self, symbol: str, min_date: str) -> list[dict]:
        """Get cached liq data for a symbol from min_date onwards, ordered by date."""
        rows = self.conn.execute(
            "SELECT date, total_liq, long_liq, short_liq, close "
            "FROM liq_cache WHERE symbol=? AND date>=? ORDER BY date",
            (symbol, min_date),
        ).fetchall()
        return [dict(r) for r in rows]

    def prune_liq_cache(self, before_date: str):
        """Remove liq cache entries older than before_date."""
        self.conn.execute("DELETE FROM liq_cache WHERE date < ?", (before_date,))
        self.conn.commit()

    def close(self):
        self.conn.close()


class ForceOrderDatabase:
    """Persistent log of raw Binance !forceOrder@arr events for live-aligned backtests."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init()

    def _init(self):
        self.conn.executescript("""
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
        """)
        self.conn.commit()

    def insert_event(self, event_time_ms: int, symbol: str, side: str,
                     qty: float, price: float, volume_usd: float):
        self.conn.execute(
            """INSERT INTO force_order_events
               (event_time_ms, symbol, side, qty, price, volume_usd, received_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (event_time_ms, symbol, side, qty, price, volume_usd,
             datetime.now(timezone.utc).isoformat()),
        )

    def prune_before_ms(self, before_ms: int):
        self.conn.execute(
            "DELETE FROM force_order_events WHERE event_time_ms < ?",
            (before_ms,),
        )
        self.conn.commit()

    def close(self):
        self.conn.close()


# ═══════════════════════════════════════════════════
# Paper Fill
# ═══════════════════════════════════════════════════

class PaperFill:
    def __init__(self, eq):
        self.equity = eq
        self.peak = eq
        self.initial = eq

    def fill_entry(self, price, qty, side):
        slip = price * (SLIP_BPS / 10000)
        fill = price + slip if side == Side.LONG else price - slip
        fee = qty * fill * (TAKER_BPS / 10000)
        self.equity -= fee
        return fill, fee

    def fill_exit(self, entry, price, qty, side):
        slip = price * (SLIP_BPS / 10000)
        fill = price - slip if side == Side.LONG else price + slip
        fee = qty * fill * (TAKER_BPS / 10000)
        pnl = (fill - entry) * qty if side == Side.LONG else (entry - fill) * qty
        self.equity += pnl - fee
        if self.equity > self.peak:
            self.peak = self.equity
        return fill, fee, pnl


# ═══════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════

class V5ForwardTest:
    def __init__(self):
        self.app_cfg = load_config()
        self.v5_cfg = load_v5_config()
        self.ca_api_key = self.v5_cfg.get("coinalyze", {}).get("api_key", "")
        self.db = V5Database(DB_PATH)
        self.force_order_db = ForceOrderDatabase(FORCE_ORDER_DB_PATH)
        self.engine = LiqClusterEngineV5()
        self.alerts = TelegramAlerts(
            self.app_cfg.secrets.telegram_bot_token,
            self.app_cfg.secrets.telegram_chat_id,
        )
        self.rl = RateLimiterGroup()
        self.rest = BinanceRestClient(testnet=False, rate_limiter=self.rl)

        # Symbols — NO BTC
        self.symbols = self.v5_cfg["symbols"]["tier_a"] + self.v5_cfg["symbols"]["tier_b"]
        tier_c = self.v5_cfg["symbols"].get("tier_c_experimental", [])
        self.experimental_symbols = set(tier_c)
        self.symbols += tier_c
        self.all_symbols = list(self.symbols)  # no BTC

        # Risk config — per-decile half-Kelly
        risk_cfg = self.v5_cfg["risk"]
        self.max_leverage = risk_cfg["max_leverage"]
        self.max_positions = risk_cfg["max_positions"]
        self.max_per_symbol = risk_cfg["max_per_symbol"]

        gates = self.v5_cfg.get("gates", {})
        self.blocked_symbols = set(gates.get("blocked_symbols", []))
        self.max_risk_pct = float(gates.get("max_risk_pct", 0.005))
        self.daily_loss_stop_r = float(gates.get("daily_loss_stop_r", -2.0))
        self.weekly_loss_stop_r = float(gates.get("weekly_loss_stop_r", -5.0))
        promotion_halt = self.db.get_state("promotion_halt", "0") == "1"
        self._entries_halted = promotion_halt
        self._promotion_alert_sent = self.db.get_state("promotion_alert_status", "")

        asia_cfg = self.v5_cfg.get("asia_shadow", {})
        self.asia_shadow_enabled = bool(asia_cfg.get("enabled", False))
        cfg_hours = asia_cfg.get("hours")
        self.asia_shadow_hours = frozenset(cfg_hours) if cfg_hours else ASIA_SHADOW_HOURS
        self.asia_shadow_max_positions = int(asia_cfg.get("max_positions", self.max_positions))
        self.asia_report_hour = int(asia_cfg.get("report_hour_utc", 8))
        self.shadow_engine = LiqClusterEngineV5() if self.asia_shadow_enabled else None
        self.shadow_positions: list[dict] = []

        # Logging-only signal shadow (no equity impact; pure research collector)
        sig_cfg = self.v5_cfg.get("signal_shadow", {})
        burst_cfg = sig_cfg.get("intraday_burst_shadow", {})
        self.intraday_burst_shadow_enabled = bool(burst_cfg.get("enabled", False))
        self.intraday_burst_min_volume_usd = float(burst_cfg.get("min_volume_usd_30m", 20_000.0))
        self.intraday_burst_min_events = int(burst_cfg.get("min_events_30m", 3))
        self.intraday_burst_dedup_bars = int(burst_cfg.get("dedup_bars", 3))
        port_cfg = sig_cfg.get("portfolio", {})
        shadow_portfolio = ShadowPortfolioConfig(
            max_concurrent=port_cfg.get("max_concurrent"),
            max_per_symbol_session=int(port_cfg.get("max_per_symbol_session", 1)),
            max_net_delta=port_cfg.get("max_net_delta"),
        )
        self.signal_shadow = None
        if _HAS_SIGNAL_SHADOW and bool(sig_cfg.get("enabled", False)):
            try:
                self.signal_shadow = SignalShadow(portfolio=shadow_portfolio)
                logger.info(
                    "Signal shadow enabled (logging-only)",
                    intraday_burst=self.intraday_burst_shadow_enabled,
                    burst_min_volume_30m=self.intraday_burst_min_volume_usd,
                    burst_min_events_30m=self.intraday_burst_min_events,
                    shadow_max_concurrent=shadow_portfolio.max_concurrent,
                    shadow_max_net_delta=shadow_portfolio.max_net_delta,
                )
            except Exception as e:
                logger.error("Signal shadow init failed (non-fatal)", error=str(e))
                self.intraday_burst_shadow_enabled = False

        # State
        self._started_at = datetime.now(timezone.utc)
        saved_eq = self.db.get_state("equity")
        eq = float(saved_eq) if saved_eq else self.v5_cfg.get("initial_equity", 10000.0)
        self.executor = PaperFill(eq)
        self.executor.peak = float(self.db.get_state("peak_equity", str(eq)))

        self.candle_buffers: dict[str, deque] = defaultdict(lambda: deque(maxlen=CANDLE_HISTORY_5M))
        self.last_prices: dict[str, float] = {}
        self.open_positions: list[dict] = self.db.get_open_positions()
        self._running = False
        self._shutdown = asyncio.Event()
        self._engine_lock = asyncio.Lock()  # V6: protects engine state between WS and candle loops
        self._last_report_date = self.db.get_state("last_report_date", "")
        self._last_session_report_date = self.db.get_state("last_session_report_date", "")
        self._last_asia_report_date = self.db.get_state("last_asia_report_date", "")
        self._last_liq_date = self.db.get_state("last_liq_date", "")
        self._ws_dirty_symbols: set = set()  # V6: symbols with pending WS liq updates
        self._ws_last_flush: float = 0.0     # V6: last time WS flushed to engine
        self._ws_last_message_mono: float = time.monotonic()  # WS liveness heartbeat

        # V6.2 Research telemetry — purely observational
        self._telemetry = None
        self._post_exit_tracking: dict[str, dict] = {}  # trade_uuid -> {entry_price, risk_per_unit, exit_bar_time, bars_tracked}
        if _HAS_TELEMETRY:
            try:
                self._telemetry = TelemetryDB()
                logger.info("Research telemetry initialized")
            except Exception as e:
                logger.warning("Telemetry init failed (non-fatal)", error=str(e))

    async def start(self):
        self._running = True
        await self.rest.start()
        await self.alerts.initialize()

        # Signal handlers
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: self._shutdown.set())

        # Load history
        await self._load_history()

        # Load liq context (Binance + local cache)
        await self._update_liq_context()

        # Recover positions
        self.open_positions: list[dict] = self.db.get_open_positions()
        if self.open_positions:
            logger.info("Recovered positions", count=len(self.open_positions))
            for p in self.open_positions:
                sym = p["symbol"]
                st = self.engine._get_state(sym)
                st.in_trade = True
                st.entry_price = p["entry_price"]
                st.risk_per_unit = abs(p["entry_price"] - p["init_stop"]) if p.get("init_stop") else 0
                st.bars_held = p.get("candles_held", 0)
                st.aggression_score = p.get("aggression", 0)
                st.decile = p.get("decile", 5)
                st.mfe = float(p.get("mfe") or 0.0)
                st.mae = float(p.get("mae") or 0.0)
                st.best_price = max(float(p["entry_price"]), float(p["entry_price"]) + st.mfe * st.risk_per_unit)
                st.vol_trail = 0.0
                st.struct_trail = 0.0
                st.consecutive_red = 0

        if self.asia_shadow_enabled:
            self.shadow_positions = self.db.get_shadow_positions()
            if self.shadow_positions:
                logger.info("Recovered shadow positions", count=len(self.shadow_positions))
                for p in self.shadow_positions:
                    sym = p["symbol"]
                    st = self.shadow_engine._get_state(sym)
                    st.in_trade = True
                    st.entry_price = p["entry_price"]
                    st.risk_per_unit = abs(p["entry_price"] - p["init_stop"]) if p.get("init_stop") else 0
                    st.bars_held = p.get("candles_held", 0)
                    st.aggression_score = p.get("aggression", 0)
                    st.decile = p.get("decile", 5)
                    st.mfe = float(p.get("mfe") or 0.0)
                    st.mae = float(p.get("mae") or 0.0)
                    st.best_price = max(float(p["entry_price"]), float(p["entry_price"]) + st.mfe * st.risk_per_unit)
                    st.vol_trail = 0.0
                    st.struct_trail = 0.0
                    st.consecutive_red = 0

        # Startup self-test
        self._self_test()

        n_cascade = sum(1 for s in self.symbols if self.engine._get_state(s).cascade_active)

        # Build recovered positions detail
        recovered_lines = []
        for p in self.open_positions:
            recovered_lines.append(
                f"  {p['side']} {p['symbol']} D{p.get('decile','?')} @ {p['entry_price']:.6f} "
                f"(held {p.get('candles_held', 0)} candles)"
            )
        recovered_block = ""
        if recovered_lines:
            recovered_block = "\n🔄 Recovered positions:\n" + "\n".join(recovered_lines)

        shadow_r = self.db.sum_shadow_pnl_r()
        asia_line = ""
        if self.asia_shadow_enabled:
            asia_line = (
                f"\n🌏 Asia shadow: hours {sorted(self.asia_shadow_hours)} UTC | "
                f"open {len(self.shadow_positions)} | cumR {shadow_r:+.2f}"
            )

        data_health = self._build_data_health_block()
        await self.alerts.send(
            f"🧪 Liq-Cluster v6.5-revert (V5 entry + vol_trail exits)\n"
            f"Symbols: {len(self.symbols)} proven | Live session: NY 14–22 UTC\n"
            f"Risk cap: {self.max_risk_pct*100:.2f}%/trade | Stops: {self.daily_loss_stop_r:+.0f}R day / {self.weekly_loss_stop_r:+.0f}R week\n"
            f"Cascades active: {n_cascade}\n"
            f"Equity: {self.executor.equity:.2f}\n"
            f"Open positions: {len(self.open_positions)}{asia_line}{recovered_block}\n\n"
            f"{data_health}",
            AlertTier.INFO,
        )

        self.db.set_state("last_startup", datetime.now(timezone.utc).isoformat())

        try:
            results = await asyncio.gather(
                self._candle_loop(),
                self._daily_report_loop(),
                self._ny_session_report_loop(),
                self._asia_session_report_loop(),
                self._liq_refresh_loop(),
                self._liq_websocket_loop(),
                self._watchdog_loop(),
                return_exceptions=True,
            )
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    names = ["candle_loop", "daily_report", "ny_session_report", "asia_session_report", "liq_refresh", "liq_websocket", "watchdog"]
                    name = names[i] if i < len(names) else f"task_{i}"
                    logger.critical("Task died", task=name, error=str(r))
                    await self.alerts.critical(f"V5.1 task crashed: {name}: {r}")
        finally:
            await self._cleanup()

    def _self_test(self):
        checks = []
        for sym in self.symbols:
            if len(self.candle_buffers[sym]) < 50:
                checks.append(f"{sym} 5m history too short: {len(self.candle_buffers[sym])}")
        try:
            self.db.set_state("_selftest", "ok")
        except Exception as e:
            checks.append(f"DB write failed: {e}")
        if checks:
            msg = "Self-test FAILED:\n" + "\n".join(f"  - {c}" for c in checks)
            logger.critical(msg)
            raise RuntimeError(msg)
        n_exp = len(self.experimental_symbols)
        logger.info("Self-test passed", symbols=len(self.symbols),
                    proven=len(self.symbols) - n_exp, experimental=n_exp,
                     total_candles=sum(len(v) for v in self.candle_buffers.values()))

    @staticmethod
    def _fmt_age(seconds: float | None) -> str:
        if seconds is None:
            return "n/a"
        seconds = max(0, int(seconds))
        if seconds < 120:
            return f"{seconds}s"
        minutes = seconds // 60
        if minutes < 120:
            return f"{minutes}m"
        hours = minutes // 60
        if hours < 72:
            return f"{hours}h"
        return f"{hours // 24}d"

    @staticmethod
    def _age_from_iso(ts: str | None) -> float | None:
        if not ts:
            return None
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - dt).total_seconds()
        except Exception:
            return None

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        return row is not None

    def _force_order_window_count(self, since_ms: int) -> int:
        row = self.force_order_db.conn.execute(
            "SELECT COUNT(*) FROM force_order_events WHERE event_time_ms > ?",
            (since_ms,),
        ).fetchone()
        return int(row[0] or 0)

    def _build_data_health_block(self) -> str:
        """Short Telegram block proving live data is fresh and research samples advance."""
        now = datetime.now(timezone.utc)
        now_ms = int(now.timestamp() * 1000)
        lines = ["🩺 DATA HEALTH"]

        try:
            row = self.force_order_db.conn.execute(
                "SELECT COUNT(*), MAX(event_time_ms) FROM force_order_events"
            ).fetchone()
            total = int(row[0] or 0)
            last_ms = row[1]
            last_age = ((now_ms - int(last_ms)) / 1000) if last_ms else None
            last_10m = self._force_order_window_count(now_ms - 10 * 60 * 1000)
            last_1h = self._force_order_window_count(now_ms - 60 * 60 * 1000)
            last_24h = self._force_order_window_count(now_ms - 24 * 60 * 60 * 1000)
            distinct_24h = self.force_order_db.conn.execute(
                "SELECT COUNT(DISTINCT symbol) FROM force_order_events WHERE event_time_ms > ?",
                (now_ms - 24 * 60 * 60 * 1000,),
            ).fetchone()[0]
            lines.append(
                f"Force orders: {last_10m}/10m {last_1h}/1h {last_24h}/24h "
                f"| symbols24h {int(distinct_24h or 0)} | last {self._fmt_age(last_age)} | total {total}"
            )
        except Exception as e:
            lines.append(f"Force orders: ERR {e}")

        try:
            heartbeat_age = self._age_from_iso(self.db.get_state("heartbeat"))
            ws_age = time.monotonic() - self._ws_last_message_mono
            uptime = (now - self._started_at).total_seconds()
            lines.append(
                f"Liveness: heartbeat {self._fmt_age(heartbeat_age)} | "
                f"ws {self._fmt_age(ws_age)} | uptime {self._fmt_age(uptime)}"
            )
        except Exception as e:
            lines.append(f"Liveness: ERR {e}")

        if self.signal_shadow is not None:
            try:
                conn = self.signal_shadow.conn
                since_ts = now.timestamp() - 24 * 60 * 60
                snap_total = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
                snap_24h = conn.execute(
                    "SELECT COUNT(*) FROM snapshots WHERE created_at > ?",
                    (since_ts,),
                ).fetchone()[0]
                snap_open = conn.execute(
                    "SELECT COUNT(*) FROM snapshots WHERE status='open'"
                ).fetchone()[0]
                snap_symbols = conn.execute(
                    "SELECT COUNT(DISTINCT symbol) FROM snapshots WHERE created_at > ?",
                    (since_ts,),
                ).fetchone()[0]
                bad = conn.execute(
                    "SELECT COUNT(*) FROM snapshots "
                    "WHERE atr IS NULL OR atr<=0 OR close IS NULL OR close<=0"
                ).fetchone()[0]
                lines.append(
                    f"Cascade shadow: {int(snap_24h)}/24h | open {int(snap_open)} "
                    f"| symbols24h {int(snap_symbols)} | bad {int(bad)} | total {int(snap_total)}"
                )

                if self._table_exists(conn, "burst_snapshots"):
                    burst_total = conn.execute("SELECT COUNT(*) FROM burst_snapshots").fetchone()[0]
                    burst_24h = conn.execute(
                        "SELECT COUNT(*) FROM burst_snapshots WHERE created_at > ?",
                        (since_ts,),
                    ).fetchone()[0]
                    burst_open = conn.execute(
                        "SELECT COUNT(*) FROM burst_snapshots WHERE status='open'"
                    ).fetchone()[0]
                    burst_symbols = conn.execute(
                        "SELECT COUNT(DISTINCT symbol) FROM burst_snapshots WHERE created_at > ?",
                        (since_ts,),
                    ).fetchone()[0]
                    burst_bad = conn.execute(
                        "SELECT COUNT(*) FROM burst_snapshots "
                        "WHERE atr IS NULL OR atr<=0 OR close IS NULL OR close<=0"
                    ).fetchone()[0]
                    lines.append(
                        f"Burst shadow: {int(burst_24h)}/24h | open {int(burst_open)} "
                        f"| symbols24h {int(burst_symbols)} | bad {int(burst_bad)} | total {int(burst_total)}"
                    )
            except Exception as e:
                lines.append(f"Signal shadow: ERR {e}")
        else:
            lines.append("Signal shadow: disabled")

        try:
            disk = shutil.disk_usage(Path.cwd())
            free_pct = disk.free / disk.total * 100 if disk.total else 0.0
            lines.append(f"Disk: {free_pct:.1f}% free ({disk.free / 1e9:.1f}GB)")
        except Exception as e:
            lines.append(f"Disk: ERR {e}")

        return "\n".join(lines)

    def _intraday_burst_stats(self, symbol: str, candle: Candle) -> dict:
        """Rolling force-order stats ending at the candle close."""
        bar_time = getattr(candle, "close_time", None) or datetime.now(timezone.utc)
        if bar_time.tzinfo is None:
            bar_time = bar_time.replace(tzinfo=timezone.utc)
        end_ms = int(bar_time.timestamp() * 1000)
        stats: dict[str, float | int] = {}
        for minutes in (15, 30, 60):
            start_ms = end_ms - minutes * 60 * 1000
            row = self.force_order_db.conn.execute(
                "SELECT COALESCE(SUM(volume_usd),0), COUNT(*), COALESCE(MAX(volume_usd),0) "
                "FROM force_order_events WHERE symbol=? AND event_time_ms>? AND event_time_ms<=?",
                (symbol, start_ms, end_ms),
            ).fetchone()
            stats[f"volume_{minutes}m"] = float(row[0] or 0.0)
            stats[f"events_{minutes}m"] = int(row[1] or 0)
            stats[f"max_order_usd_{minutes}m"] = float(row[2] or 0.0)

        start_30m = end_ms - 30 * 60 * 1000
        long_liq = self.force_order_db.conn.execute(
            "SELECT COALESCE(SUM(volume_usd),0) FROM force_order_events "
            "WHERE symbol=? AND event_time_ms>? AND event_time_ms<=? AND side='SELL'",
            (symbol, start_30m, end_ms),
        ).fetchone()[0]
        short_liq = self.force_order_db.conn.execute(
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
        stats["max_order_usd_30m"] = stats.get("max_order_usd_30m", 0.0)
        return stats

    async def _cleanup(self):
        logger.info("Shutting down...")
        self.db.set_state("equity", str(round(self.executor.equity, 4)))
        self.db.set_state("peak_equity", str(round(self.executor.peak, 4)))
        if self.signal_shadow is not None:
            try:
                self.signal_shadow.close()
            except Exception:
                pass
        try:
            self.force_order_db.conn.commit()
            self.force_order_db.close()
        except Exception:
            pass
        try:
            self.db.close()
        except Exception:
            pass
        try:
            await self.rest.close()
        except Exception:
            pass
        logger.info("Shutdown complete")

    # ── History Loading ──────────────────────────────────────────

    async def _load_history(self):
        logger.info("Loading 5m history for all symbols...")
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=18)

        for sym in self.all_symbols:
            candles = await self._fetch_klines(sym, "5m", start, end)
            self.candle_buffers[sym].extend(candles)
            if candles:
                self.last_prices[sym] = candles[-1].close
            await asyncio.sleep(0.05)

        total = sum(len(v) for v in self.candle_buffers.values())
        logger.info(f"History loaded: {total} candles across {len(self.all_symbols)} symbols")

    async def _fetch_klines(self, symbol, interval, start, end):
        out = []
        ms_s = int(start.timestamp() * 1000)
        ms_e = int(end.timestamp() * 1000)
        while ms_s < ms_e:
            raw = await self.rest.get_klines(symbol=symbol, interval=interval,
                                             start_time=ms_s, limit=1500)
            if not raw:
                break
            for k in raw:
                if k[6] > ms_e:
                    break
                out.append(Candle(
                    symbol=symbol, timeframe=interval,
                    open_time=datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc),
                    close_time=datetime.fromtimestamp(k[6] / 1000, tz=timezone.utc),
                    open=float(k[1]), high=float(k[2]),
                    low=float(k[3]), close=float(k[4]),
                    volume=float(k[5]),
                    taker_buy_volume=float(k[9]) if len(k) > 9 else 0.0,
                    is_closed=True,
                ))
            ms_s = int(raw[-1][6]) + 1
            if len(raw) < 1500:
                break
            await asyncio.sleep(0.1)
        seen = set()
        deduped = []
        for c in out:
            if c.open_time not in seen:
                seen.add(c.open_time)
                deduped.append(c)
        return sorted(deduped, key=lambda c: c.open_time)

    # ── Liquidation Context (Hybrid & WebSocket Aggregated) ──────

    async def _update_liq_context(self):
        """Fetch liquidation data and merge with local cache using the hybrid model.

        Strategy:
        1. Prune old cache entries from SQLite.
        2. Check if the database cache for each symbol has >= 30 days of data.
        3. If cached_count < 30 (cold start), seed the last 90 days of history from Coinalyze.
           - Query Coinalyze history endpoint.
           - Stagger Coinalyze REST calls by 3.0s to avoid 429s.
           - Fetch daily closes from Binance klines.
           - Upsert results into local SQLite cache.
        4. Otherwise, skip Coinalyze queries completely on restart/daily refresh.
        5. In either case, query complete cache history from SQLite and feed it to the trading engine.
        """
        logger.info("Updating liquidation context...")

        # Prune old cache entries
        prune_date = (datetime.now(timezone.utc) - timedelta(days=LIQ_CACHE_MAX_DAYS)).strftime("%Y-%m-%d")
        self.db.prune_liq_cache(prune_date)
        prune_ms = int((datetime.now(timezone.utc) - timedelta(days=FORCE_ORDER_RETENTION_DAYS)).timestamp() * 1000)
        self.force_order_db.prune_before_ms(prune_ms)

        min_date = (datetime.now(timezone.utc) - timedelta(days=LIQ_CACHE_MAX_DAYS)).strftime("%Y-%m-%d")

        from engines.liq_cluster_engine_v5 import CascadeTracker

        for sym in self.symbols:
            try:
                # Check cache count in the last 120 days
                cached = self.db.get_liq_cache(sym, min_date)
                cached_count = len(cached)

                if cached_count < 30:
                    logger.info("Cold start detected: seeding history from Coinalyze", symbol=sym, cached_days=cached_count)
                    if not self.ca_api_key:
                        logger.warning("No Coinalyze API key available for seeding!", symbol=sym)
                        continue

                    # Cold start seeding from Coinalyze
                    now_ts = int(time.time())
                    fr_ts = now_ts - 90 * 86400
                    ca_sym = f"{sym}_PERP.A"
                    data = None

                    for attempt in range(5):
                        try:
                            resp = requests.get(
                                "https://api.coinalyze.net/v1/liquidation-history",
                                params={
                                    "symbols": ca_sym,
                                    "interval": "daily",
                                    "from": fr_ts,
                                    "to": now_ts,
                                    "api_key": self.ca_api_key,
                                },
                                timeout=20,
                            )
                            if resp.status_code == 429:
                                wait = (attempt + 1) * 15
                                logger.warning("Coinalyze rate limit during seeding", symbol=sym, attempt=attempt+1, wait_s=wait)
                                await asyncio.sleep(wait)
                                continue
                            if resp.status_code != 200:
                                logger.warning("Coinalyze error during seeding", symbol=sym, status=resp.status_code)
                                await asyncio.sleep(5)
                                continue
                            data = resp.json()
                            break
                        except Exception as e:
                            logger.error("Coinalyze fetch error during seeding", symbol=sym, error=str(e), attempt=attempt+1)
                            await asyncio.sleep(5)

                    if data is not None and isinstance(data, list) and data:
                        history = data[0].get("history", [])
                        if history:
                            # Fetch daily closes from Binance
                            daily_closes = await self._get_daily_closes(sym)

                            for h in history:
                                dt_str = datetime.fromtimestamp(h["t"], tz=timezone.utc).strftime("%Y-%m-%d")
                                long_liq = float(h.get("l", 0))
                                short_liq = float(h.get("s", 0))
                                total_liq = long_liq + short_liq
                                close = daily_closes.get(dt_str, 0.0)

                                self.db.upsert_liq_cache(sym, dt_str, total_liq, long_liq, short_liq, close)

                            logger.info("Successfully seeded from Coinalyze", symbol=sym, records=len(history))
                    
                    # Polite pacing to respect free-tier rate limits (3.0s stagger)
                    await asyncio.sleep(3.0)

                else:
                    logger.debug("Skipping Coinalyze seeding; cache has sufficient history", symbol=sym, cached_days=cached_count)

                # Fetch full history from local cache and feed it to the trading engine
                cached = self.db.get_liq_cache(sym, min_date)
                if cached:
                    self._feed_engine_liq(self.engine, sym)
                    if self.shadow_engine:
                        self._feed_engine_liq(self.shadow_engine, sym)

                logger.debug("Liq data updated from local cache", symbol=sym, cached_days=len(cached))

            except Exception as e:
                logger.error("Liq update error", symbol=sym, error=str(e), exc_info=True)

        self._last_liq_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.db.set_state("last_liq_date", self._last_liq_date)

        n_active = sum(1 for s in self.symbols if self.engine._get_state(s).cascade_active)
        logger.info(f"Liq context updated: {n_active}/{len(self.symbols)} cascades active (hybrid model)")

    async def _get_daily_closes(self, symbol):
        closes = {}
        try:
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=130)
            raw = await self.rest.get_klines(
                symbol=symbol, interval="1d",
                start_time=int(start.timestamp() * 1000), limit=130,
            )
            if raw:
                for k in raw:
                    dt = datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc)
                    closes[dt.strftime("%Y-%m-%d")] = float(k[4])
        except Exception as e:
            logger.error("Daily closes error", symbol=symbol, error=str(e))
        return closes

    async def _watchdog_loop(self):
        """Independent liveness monitor.

        The candle loop writes a 'heartbeat' timestamp each poll cycle and the WS
        loop updates an in-memory message heartbeat on every received frame. If
        either goes stale (deadlock, silent task death, network hang), this loop
        force-exits so systemd (Restart=always) revives a clean process.

        Uses its own short-lived SQLite connection so it can never be blocked by a
        hang elsewhere in the runner.
        """
        STALE_S = 300
        WS_STALE_S = 900
        CHECK_S = 30
        # Grace period for startup (history load + liq seeding can take minutes).
        for _ in range(180):
            if self._shutdown.is_set():
                return
            await asyncio.sleep(1)

        while not self._shutdown.is_set():
            for _ in range(CHECK_S):
                if self._shutdown.is_set():
                    return
                await asyncio.sleep(1)

            ws_age = time.monotonic() - self._ws_last_message_mono
            if ws_age > WS_STALE_S:
                logger.critical("Watchdog: websocket heartbeat stale; exiting for restart",
                                age_s=round(ws_age))
                try:
                    await self.alerts.critical(
                        f"⚠️ v65 watchdog: ws stream stale {ws_age:.0f}s — forcing restart")
                except Exception:
                    pass
                os._exit(1)

            hb = None
            try:
                wconn = sqlite3.connect(str(DB_PATH))
                row = wconn.execute(
                    "SELECT value FROM state WHERE key='heartbeat'").fetchone()
                wconn.close()
                hb = row[0] if row else None
            except Exception as e:
                logger.warning("Watchdog read failed (non-fatal)", error=str(e))
                continue

            if not hb:
                continue
            try:
                last = datetime.fromisoformat(hb)
            except ValueError:
                continue
            age = (datetime.now(timezone.utc) - last).total_seconds()
            if age > STALE_S:
                logger.critical("Watchdog: candle heartbeat stale; exiting for restart",
                                age_s=round(age))
                try:
                    await self.alerts.critical(
                        f"⚠️ v65 watchdog: candle loop stalled {age:.0f}s — forcing restart")
                except Exception:
                    pass
                os._exit(1)

    async def _liq_refresh_loop(self):
        while not self._shutdown.is_set():
            # Sleep in 1-second chunks to exit promptly on shutdown
            for _ in range(3600):
                if self._shutdown.is_set():
                    break
                await asyncio.sleep(1)
            if self._shutdown.is_set():
                break

            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if today != self._last_liq_date:
                logger.info("Daily liq refresh triggered")
                await self._update_liq_context()

    async def _liq_websocket_loop(self):
        """V6: Websocket client with debounced engine updates.
        Aggregates liquidation events into SQLite in real-time but only
        flushes to the trading engine every WS_ENGINE_FLUSH_INTERVAL seconds.
        """
        url = "wss://fstream.binance.com/market/ws/!forceOrder@arr"
        logger.info("Liquidation WebSocket stream starting", url=url)

        self._ws_last_flush = time.monotonic()

        while not self._shutdown.is_set():
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    logger.info("Liquidation WebSocket connected successfully")
                    self._ws_last_message_mono = time.monotonic()

                    async for message in ws:
                        if self._shutdown.is_set():
                            break
                        # Any frame received from the stream counts as WS liveness.
                        self._ws_last_message_mono = time.monotonic()

                        try:
                            msg = json.loads(message)
                        except Exception as e:
                            logger.error("Failed to parse websocket message", error=str(e))
                            continue

                        if not isinstance(msg, dict) or msg.get("e") != "forceOrder":
                            continue

                        order = msg.get("o", {})
                        symbol = order.get("s")

                        if symbol not in self.symbols:
                            continue

                        # Extract details
                        side = order.get("S")
                        qty = float(order.get("q", 0))
                        price = float(order.get("p", 0))
                        volume = qty * price
                        time_ms = msg.get("E", 0)

                        if volume <= 0:
                            continue

                        self.force_order_db.insert_event(
                            time_ms, symbol, side, qty, price, volume,
                        )

                        # Update database cache for the given date
                        dt = datetime.fromtimestamp(time_ms / 1000, tz=timezone.utc)
                        date_str = dt.strftime("%Y-%m-%d")

                        # Get existing entry for this day
                        existing = self.db.conn.execute(
                            "SELECT total_liq, long_liq, short_liq FROM liq_cache WHERE symbol=? AND date=?",
                            (symbol, date_str)
                        ).fetchone()

                        if existing:
                            long_liq = existing["long_liq"] + (volume if side == "SELL" else 0.0)
                            short_liq = existing["short_liq"] + (volume if side == "BUY" else 0.0)
                            total_liq = long_liq + short_liq
                        else:
                            long_liq = volume if side == "SELL" else 0.0
                            short_liq = volume if side == "BUY" else 0.0
                            total_liq = volume

                        close = self.last_prices.get(symbol, price)

                        # V6: Write to SQLite without immediate commit (batched)
                        self.db.conn.execute(
                            """INSERT OR REPLACE INTO liq_cache
                               (symbol, date, total_liq, long_liq, short_liq, close, updated_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?)""",
                            (symbol, date_str, total_liq, long_liq, short_liq, close,
                             datetime.now(timezone.utc).isoformat()),
                        )
                        self._ws_dirty_symbols.add(symbol)

                        # V6: Debounced engine flush — every WS_ENGINE_FLUSH_INTERVAL seconds
                        now_mono = time.monotonic()
                        if now_mono - self._ws_last_flush >= WS_ENGINE_FLUSH_INTERVAL:
                            self.db.conn.commit()
                            self.force_order_db.conn.commit()
                            await self._flush_ws_to_engine()
                            self._ws_last_flush = now_mono

                        logger.debug(
                            "Real-time liquidation aggregated via WebSocket",
                            symbol=symbol,
                            side=side,
                            volume=f"${volume:,.2f}",
                            daily_total=f"${total_liq:,.2f}",
                        )

            except Exception as e:
                if self._shutdown.is_set():
                    break
                logger.error("Liquidation WebSocket error or disconnect, reconnecting in 5s...", error=str(e))
                await asyncio.sleep(5)

        logger.info("Liquidation WebSocket stream stopped")

    async def _flush_ws_to_engine(self):
        """V6: Flush pending WebSocket liq updates to the trading engine."""
        if not self._ws_dirty_symbols:
            return

        from engines.liq_cluster_engine_v5 import CascadeTracker

        min_date = (datetime.now(timezone.utc) - timedelta(days=LIQ_CACHE_MAX_DAYS)).strftime("%Y-%m-%d")
        dirty = list(self._ws_dirty_symbols)
        self._ws_dirty_symbols.clear()

        # Bound the lock wait so WS force-order capture cannot freeze behind a
        # stuck candle-loop holder; re-queue dirty symbols for the next flush.
        try:
            await asyncio.wait_for(self._engine_lock.acquire(), timeout=30)
        except asyncio.TimeoutError:
            logger.error("engine_lock acquire timed out in WS flush; re-queueing", n=len(dirty))
            self._ws_dirty_symbols.update(dirty)
            return
        try:
            for symbol in dirty:
                cached = self.db.get_liq_cache(symbol, min_date)
                if cached:
                    self._feed_engine_liq(self.engine, symbol)
                    if self.shadow_engine:
                        self._feed_engine_liq(self.shadow_engine, symbol)
        finally:
            self._engine_lock.release()

        n_flushed = len(dirty)
        n_active = sum(1 for s in self.symbols if self.engine._get_state(s).cascade_active)
        logger.debug(f"WS engine flush: {n_flushed} symbols updated, {n_active} cascades active")

    # ── Main Candle Loop ─────────────────────────────────────────

    async def _candle_loop(self):
        last_processed: dict[str, datetime] = {}
        for sym in self.all_symbols:
            lp = self.db.get_state(f"last_5m_{sym}", "")
            if lp:
                try:
                    last_processed[sym] = datetime.fromisoformat(lp)
                except Exception:
                    last_processed[sym] = datetime.min.replace(tzinfo=timezone.utc)
            else:
                last_processed[sym] = datetime.min.replace(tzinfo=timezone.utc)

        logger.info("Candle loop started", symbols=len(self.all_symbols))
        # Fresh heartbeat on entry so a restart doesn't read a stale stamp and
        # trip the watchdog before the first poll cycle completes.
        self.db.set_state("heartbeat", datetime.now(timezone.utc).isoformat())

        while not self._shutdown.is_set():
            try:
                for sym in self.all_symbols:
                    # Per-symbol guard: a single bad symbol/API error must not abort
                    # the whole poll cycle (and must never bubble out of the loop).
                    try:
                        raw = await self.rest.get_klines(symbol=sym, interval="5m", limit=3)
                        # Defensive: get_klines may return a non-list error payload
                        # (e.g. {'code': -1122, 'msg': 'Invalid symbol status.'}).
                        if not raw or not isinstance(raw, list):
                            continue

                        for k in raw:
                            if not isinstance(k, (list, tuple)) or len(k) < 7:
                                continue
                            close_time = datetime.fromtimestamp(k[6] / 1000, tz=timezone.utc)
                            now = datetime.now(timezone.utc)
                            if close_time > now:
                                continue

                            if close_time <= last_processed[sym]:
                                continue

                            candle = Candle(
                                symbol=sym, timeframe="5m",
                                open_time=datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc),
                                close_time=close_time,
                                open=float(k[1]), high=float(k[2]),
                                low=float(k[3]), close=float(k[4]),
                                volume=float(k[5]),
                                taker_buy_volume=float(k[9]) if len(k) > 9 else 0.0,
                                is_closed=True,
                            )

                            self.candle_buffers[sym].append(candle)
                            self.last_prices[sym] = candle.close

                            if sym in self.symbols:
                                # Bound the lock wait: if a stuck holder (e.g. WS flush)
                                # never releases, skip rather than freeze forever. The
                                # watchdog escalates to a restart if heartbeats go stale.
                                try:
                                    await asyncio.wait_for(self._engine_lock.acquire(), timeout=30)
                                except asyncio.TimeoutError:
                                    logger.error("engine_lock acquire timed out; skipping candle", symbol=sym)
                                else:
                                    try:
                                        # Bound per-candle processing so a single hung
                                        # network await cannot freeze the loop or hold
                                        # the lock indefinitely. Logs the culprit symbol.
                                        await asyncio.wait_for(
                                            self._on_5m_close(sym, candle), timeout=25)
                                    except asyncio.TimeoutError:
                                        logger.error("on_5m_close timed out; skipping", symbol=sym)
                                    finally:
                                        self._engine_lock.release()

                            last_processed[sym] = close_time
                    except Exception as e:
                        logger.error("Candle per-symbol error", symbol=sym, error=str(e))

                    # Per-symbol heartbeat: reflects real progress so a slow (not hung)
                    # cycle never trips the watchdog; a true wedge still goes stale.
                    self.db.set_state("heartbeat", datetime.now(timezone.utc).isoformat())
                    await asyncio.sleep(0.02)

                # V6: batch commit all state updates per poll cycle
                for sym in self.all_symbols:
                    lp = last_processed.get(sym)
                    if lp:
                        self.db.conn.execute(
                            "INSERT OR REPLACE INTO state(key,value) VALUES(?,?)",
                            (f"last_5m_{sym}", lp.isoformat()),
                        )
                self.db.conn.commit()

                self.db.set_state("heartbeat", datetime.now(timezone.utc).isoformat())

                # V6.2: Log regime snapshot periodically (every 5 minutes / 300s)
                now_mono = time.monotonic()
                if not hasattr(self, "_last_regime_log") or now_mono - self._last_regime_log >= 300:
                    if self._telemetry:
                        try:
                            btc_p = self.last_prices.get("BTCUSDT", 0.0)
                            n_cascades = sum(1 for s in self.symbols if self.engine._get_state(s).cascade_active)
                            n_pos = sum(1 for p in self.open_positions if not p.get("_closed"))
                            self._telemetry.log_regime(
                                btc_price=btc_p,
                                n_cascades=n_cascades,
                                n_positions=n_pos,
                                n_symbols=len(self.symbols),
                                equity=float(self.executor.equity),
                            )
                            self._telemetry.commit()
                            self._last_regime_log = now_mono
                        except Exception as e:
                            logger.debug("Telemetry regime log error (non-fatal)", error=str(e))


            except Exception as e:
                logger.error("Candle loop error", error=str(e), exc_info=True)

            await asyncio.sleep(POLL_INTERVAL_S)

    async def _on_5m_close(self, symbol: str, candle: Candle):
        # ── Telemetry: post-exit continuation tracking ──
        if self._telemetry and self._post_exit_tracking:
            try:
                to_remove = []
                for tuuid, ctx in self._post_exit_tracking.items():
                    if ctx["symbol"] != symbol:
                        continue
                    ctx["bars_tracked"] += 1
                    rpu = ctx["risk_per_unit"]
                    if rpu > 0:
                        hyp_r = (candle.close - ctx["entry_price"]) / rpu
                        self._telemetry.log_post_exit_point(
                            trade_uuid=tuuid,
                            bars_after=ctx["bars_tracked"],
                            timestamp=candle.close_time.isoformat() if hasattr(candle, 'close_time') else "",
                            price=candle.close,
                            hypothetical_r=round(hyp_r, 4),
                        )
                    if ctx["bars_tracked"] >= ctx["max_bars"]:
                        to_remove.append(tuuid)
                for tuuid in to_remove:
                    del self._post_exit_tracking[tuuid]
            except Exception as e:
                logger.debug("Telemetry post-exit error (non-fatal)", error=str(e))

        await self._manage_positions(symbol, candle)
        await self._check_entry(symbol, candle)
        if self.asia_shadow_enabled:
            await self._manage_shadow_positions(symbol, candle)
            await self._check_shadow_entry(symbol, candle)

        # Logging-only research collector — synchronous, exception-isolated, no locks.
        if self.signal_shadow is not None:
            try:
                candles_5m = list(self.candle_buffers.get(symbol, []))
                st = self.engine._get_state(symbol)
                self.signal_shadow.on_bar(
                    symbol, candles_5m,
                    st,
                )
                if self.intraday_burst_shadow_enabled:
                    burst = self._intraday_burst_stats(symbol, candle)
                    self.signal_shadow.on_intraday_burst(
                        symbol, candles_5m,
                        st,
                        burst,
                        min_volume_usd=self.intraday_burst_min_volume_usd,
                        min_events=self.intraday_burst_min_events,
                        dedup_bars=self.intraday_burst_dedup_bars,
                    )
            except Exception as e:
                logger.error("Signal shadow on_bar error (non-fatal)", symbol=symbol,
                             error=str(e), exc_info=True)

    def _feed_engine_liq(self, engine: LiqClusterEngineV5, symbol: str):
        from engines.liq_cluster_engine_v5 import CascadeTracker
        min_date = (datetime.now(timezone.utc) - timedelta(days=LIQ_CACHE_MAX_DAYS)).strftime("%Y-%m-%d")
        cached = self.db.get_liq_cache(symbol, min_date)
        if cached:
            engine._cascades[symbol] = CascadeTracker()
            engine.update_daily_liq(symbol, cached)

    async def _manage_positions(self, symbol: str, candle: Candle):
        sym_positions = [p for p in self.open_positions if p["symbol"] == symbol]
        if not sym_positions:
            return

        candles_5m = list(self.candle_buffers.get(symbol, []))

        for p in sym_positions:
            p["candles_held"] += 1
            side = Side(p["side"])

            # Sync bars_held to engine state so time_stop check works
            self.engine._get_state(symbol).bars_held = p["candles_held"]

            # Skip stop check if candle is older than entry (recovery from restart)
            if candles_5m:
                try:
                    entry_dt = datetime.fromisoformat(p["entry_time"])
                    if candles_5m[-1].close_time < entry_dt:
                        self.db.save_position(p)
                        continue
                except (ValueError, KeyError):
                    pass

            # ── Telemetry: log R-path point + evaluate shadow exits ──
            if self._telemetry and candles_5m:
                try:
                    st = self.engine._get_state(symbol)
                    bar = candles_5m[-1]
                    rpu = abs(p["entry_price"] - p["init_stop"])
                    if rpu > 0:
                        cur_r = (bar.close - p["entry_price"]) / rpu
                        self._telemetry.log_r_point(
                            trade_uuid=p["trade_uuid"],
                            bar_index=p["candles_held"],
                            timestamp=bar.close_time.isoformat() if hasattr(bar, 'close_time') else "",
                            price=bar.close,
                            unrealized_r=round(cur_r, 4),
                            mae_so_far=round(float(st.mae), 4),
                            mfe_so_far=round(float(st.mfe), 4),
                            atr=0,  # computed inside shadow_exits
                            consecutive_red=st.consecutive_red,
                            above_ema=False,  # populated by shadow evaluator
                            above_range_high=False,
                            vol_trail_level=st.vol_trail,
                            struct_trail_level=st.struct_trail,
                        )

                        # Shadow exit evaluation
                        entry_ctx = json.loads(p.get("_entry_context", "{}")) if isinstance(p.get("_entry_context"), str) else p.get("_entry_context", {})
                        shadows = evaluate_shadows(
                            candles_5m=candles_5m,
                            entry_price=p["entry_price"],
                            risk_per_unit=rpu,
                            best_price=st.best_price,
                            bars_held=p["candles_held"],
                            consecutive_red=st.consecutive_red,
                            entry_context=entry_ctx,
                        )
                        for sname, sprice, sr in shadows:
                            self._telemetry.log_shadow_trigger(
                                trade_uuid=p["trade_uuid"],
                                shadow_name=sname,
                                trigger_bar=p["candles_held"],
                                trigger_time=bar.close_time.isoformat() if hasattr(bar, 'close_time') else "",
                                trigger_price=round(sprice, 6),
                                shadow_r=round(sr, 4),
                            )
                except Exception as e:
                    logger.debug("Telemetry r_path/shadow error (non-fatal)", error=str(e))

            try:
                result = self.engine.manage_position(symbol, candles_5m)
            except Exception as e:
                logger.error("manage_position error", symbol=symbol, error=str(e), exc_info=True)
                self.db.save_position(p)
                continue

            if result and result["action"] == "close":
                p["mae"] = result.get("mae", p.get("mae", 0))
                p["mfe"] = result.get("mfe", p.get("mfe", 0))
                await self._close_position(p, result.get("exit_price", candle.close),
                                           result["reason"], candle.close_time)
                continue

            self.db.save_position(p)

        self.open_positions = [p for p in self.open_positions if not p.get("_closed")]

    async def _close_position(self, p, price, reason, ct):
        side = Side(p["side"])
        fill, fee, pnl = self.executor.fill_exit(p["entry_price"], price, p["quantity"], side)
        p["rpnl"] += pnl
        p["fees"] += fee
        p["_closed"] = True

        sd = abs(p["entry_price"] - p["init_stop"])
        pnl_r = p["rpnl"] / (sd * p["orig_quantity"]) if sd > 0 and p["orig_quantity"] > 0 else 0
        net_pnl = p["rpnl"] - p["fees"]

        # ct is the triggering candle's close_time, already correct from caller
        exit_time_str = ct.isoformat() if isinstance(ct, datetime) else str(ct)

        trade = {
            "trade_uuid": p["trade_uuid"],
            "symbol": p["symbol"],
            "side": p["side"],
            "entry_time": p["entry_time"],
            "exit_time": exit_time_str,
            "entry_price": round(p["entry_price"], 6),
            "exit_price": round(fill, 6),
            "quantity": round(p["orig_quantity"], 6),
            "leverage": p["leverage"],
            "stop_dist": round(sd, 6),
                    "is_experimental": 1 if p["symbol"] in self.experimental_symbols else 0,
            "pnl_usd": round(net_pnl, 4),
            "pnl_r": round(float(pnl_r), 4),
            "fees": round(float(p["fees"]), 4),
            "hold_candles": p["candles_held"],
            "exit_reason": reason,
            "tp1_hit": p["tp1_hit"],
            "equity_after": round(float(self.executor.equity), 2),
            "confirmations": p.get("confirmations", ""),
            "mae": round(float(p.get("mae", 0)), 4),
            "mfe": round(float(p.get("mfe", 0)), 4),
            "aggression": float(p.get("aggression", 0)),
            "decile": p.get("decile", 0),
            "duplicate_key": "",
            "strategy_version": STRATEGY_VERSION,
        }

        self.db.save_trade(trade)
        self.db.remove_position(p["trade_uuid"])

        # Reset engine state for this symbol
        st = self.engine._get_state(p["symbol"])
        st.in_trade = False

        # Persist equity
        self.db.set_state("equity", str(round(float(self.executor.equity), 4)))
        self.db.set_state("peak_equity", str(round(float(self.executor.peak), 4)))
        # V6: count active (non-closed) positions for accurate snapshot
        active_count = sum(1 for p in self.open_positions if not p.get("_closed"))
        self.db.save_equity_snapshot(
            float(self.executor.equity), active_count,
            self._calc_unrealized_r(),
        )

        emoji = "✅" if pnl_r >= 0 else "❌"
        agg = float(p.get("aggression", 0))
        dec = p.get("decile", 0)
        mae = float(p.get('mae', 0))
        mfe = float(p.get('mfe', 0))
        msg = (
            f"{emoji} EXIT {p['side']} {p['symbol']}\n"
            f"Entry: {p['entry_price']:.6f} → Exit: {fill:.6f}\n"
            f"PnL: {net_pnl:+.2f} ({float(pnl_r):+.3f}R)\n"
            f"Reason: {reason} | Candles: {p['candles_held']}\n"
            f"MAE: {mae:.2f}R | MFE: {mfe:.2f}R\n"
            f"Aggression: {agg:.0f} (D{dec}) | Eq: {float(self.executor.equity):.2f}"
        )
        await self.alerts.send(msg, AlertTier.INFO)

        logger.info("Position closed", symbol=p["symbol"], pnl_r=round(float(pnl_r), 4),
                     reason=reason, equity=round(float(self.executor.equity), 2))

        await self._check_promotion_rules()

        # ── Telemetry: exit attribution + shadow finalization ──
        if self._telemetry:
            try:
                st = self.engine._get_state(p["symbol"])
                self._telemetry.log_exit(
                    trade_uuid=p["trade_uuid"],
                    exit_time=ct.isoformat() if isinstance(ct, datetime) else str(ct),
                    exit_price=round(fill, 6),
                    exit_reason=reason,
                    pnl_r=round(float(pnl_r), 4),
                    hold_bars=p["candles_held"],
                    mae=round(float(p.get("mae", 0)), 4),
                    mfe=round(float(p.get("mfe", 0)), 4),
                )
                self._telemetry.finalize_shadows(
                    trade_uuid=p["trade_uuid"],
                    actual_exit_r=round(float(pnl_r), 4),
                    actual_exit_bar=p["candles_held"],
                )
                # Start post-exit tracking (48 bars = 4 hours)
                self._post_exit_tracking[p["trade_uuid"]] = {
                    "symbol": p["symbol"],
                    "entry_price": p["entry_price"],
                    "risk_per_unit": abs(p["entry_price"] - p["init_stop"]),
                    "bars_tracked": 0,
                    "max_bars": 48,
                }
                self._telemetry.commit()
            except Exception as e:
                logger.debug("Telemetry exit logging error (non-fatal)", error=str(e))

    # ── Asia Paper Shadow ────────────────────────────────────────

    async def _manage_shadow_positions(self, symbol: str, candle: Candle):
        sym_positions = [p for p in self.shadow_positions if p["symbol"] == symbol]
        if not sym_positions:
            return

        candles_5m = list(self.candle_buffers.get(symbol, []))
        for p in sym_positions:
            p["candles_held"] += 1
            st = self.shadow_engine._get_state(symbol)
            st.bars_held = p["candles_held"]

            try:
                result = self.shadow_engine.manage_position(symbol, candles_5m)
            except Exception as e:
                logger.error("shadow manage_position error", symbol=symbol, error=str(e))
                self.db.save_shadow_position(p)
                continue

            if result and result.get("action") == "close":
                p["mae"] = result.get("mae", p.get("mae", 0))
                p["mfe"] = result.get("mfe", p.get("mfe", 0))
                await self._close_shadow_position(p, result.get("exit_price", candle.close),
                                                  result["reason"], candle.close_time)
                continue

            self.db.save_shadow_position(p)

        self.shadow_positions = [p for p in self.shadow_positions if not p.get("_closed")]

    async def _close_shadow_position(self, p, price, reason, ct):
        sd = abs(p["entry_price"] - p["init_stop"])
        pnl_r = (price - p["entry_price"]) / sd if sd > 0 else 0.0
        p["_closed"] = True

        trade = {
            "trade_uuid": p["trade_uuid"],
            "session_tag": "asia",
            "symbol": p["symbol"],
            "side": p["side"],
            "entry_time": p["entry_time"],
            "exit_time": ct.isoformat() if isinstance(ct, datetime) else str(ct),
            "entry_price": round(p["entry_price"], 6),
            "exit_price": round(price, 6),
            "pnl_r": round(float(pnl_r), 4),
            "exit_reason": reason,
            "hold_candles": p["candles_held"],
            "decile": p.get("decile", 0),
            "aggression": float(p.get("aggression", 0)),
            "mae": round(float(p.get("mae", 0)), 4),
            "mfe": round(float(p.get("mfe", 0)), 4),
            "strategy_version": STRATEGY_VERSION,
        }
        self.db.save_shadow_trade(trade)
        self.db.remove_shadow_position(p["trade_uuid"])
        st = self.shadow_engine._get_state(p["symbol"])
        st.in_trade = False

        cum_r = self.db.sum_shadow_pnl_r()
        emoji = "🌏✅" if pnl_r >= 0 else "🌏❌"
        await self.alerts.send(
            f"{emoji} SHADOW EXIT {p['symbol']}\n"
            f"R: {pnl_r:+.3f} | Reason: {reason} | Bars: {p['candles_held']}\n"
            f"Shadow cumR: {cum_r:+.2f}",
            AlertTier.INFO,
        )
        logger.info("Shadow closed", symbol=p["symbol"], pnl_r=round(float(pnl_r), 4), reason=reason)

    async def _check_shadow_entry(self, symbol: str, candle: Candle):
        hour = candle.close_time.hour
        if hour not in self.asia_shadow_hours:
            return

        if any(p["symbol"] == symbol and not p.get("_closed") for p in self.shadow_positions):
            return
        if any(p["symbol"] == symbol and not p.get("_closed") for p in self.open_positions):
            return

        active = [p for p in self.shadow_positions if not p.get("_closed")]
        if len(active) >= self.asia_shadow_max_positions:
            return

        dup_key = f"{symbol}_{candle.close_time.isoformat()}"
        if self.db.has_shadow_dup(dup_key):
            return

        candles_5m = list(self.candle_buffers.get(symbol, []))
        # NOTE: caller (_on_5m_close) already holds self._engine_lock. asyncio.Lock
        # is non-reentrant, so re-acquiring here self-deadlocks the candle loop.
        # The SNIPER_ALLOWED_HOURS swap is already protected by the held lock.
        old_hours = eng_mod.SNIPER_ALLOWED_HOURS
        eng_mod.SNIPER_ALLOWED_HOURS = self.asia_shadow_hours
        try:
            sig = self.shadow_engine.evaluate(symbol, candles_5m)
        except Exception as e:
            logger.error("shadow evaluate() error", symbol=symbol, error=str(e))
            return
        finally:
            eng_mod.SNIPER_ALLOWED_HOURS = old_hours

        if sig is None:
            return

        self.db.mark_shadow_dup(dup_key)
        st = self.shadow_engine._get_state(symbol)
        decile = st.decile

        pos = {
            "trade_uuid": sig.trade_uuid,
            "symbol": symbol,
            "side": sig.side.value,
            "entry_price": round(sig.entry_price, 6),
            "init_stop": round(sig.stop_price, 6),
            "candles_held": 0,
            "entry_time": candle.close_time.isoformat(),
            "decile": decile,
            "aggression": float(st.aggression_score),
            "mae": 0.0,
            "mfe": 0.0,
        }
        self.shadow_positions.append(pos)
        self.db.save_shadow_position(pos)

        await self.alerts.send(
            f"🌏 SHADOW ENTRY LONG {symbol}\n"
            f"Price: {sig.entry_price:.6f} | Stop: {sig.stop_price:.6f}\n"
            f"D{decile} | Hour: {hour:02d} UTC | Shadow open: {len(self.shadow_positions)}",
            AlertTier.INFO,
        )
        logger.info("Shadow entry", symbol=symbol, hour=hour, decile=decile)

    async def _asia_session_report_loop(self):
        if not self.asia_shadow_enabled:
            return
        while not self._shutdown.is_set():
            for _ in range(60):
                if self._shutdown.is_set():
                    break
                await asyncio.sleep(1)
            if self._shutdown.is_set():
                break

            now = datetime.now(timezone.utc)
            today = now.strftime("%Y-%m-%d")
            if today == self._last_asia_report_date:
                continue
            if now.hour != self.asia_report_hour or now.minute > 5:
                continue

            await self._send_asia_session_report(today)
            self._last_asia_report_date = today
            self.db.set_state("last_asia_report_date", today)

    async def _send_asia_session_report(self, session_date: str):
        trades = self.db.get_all_shadow_trades()
        open_pos = [p for p in self.shadow_positions if not p.get("_closed")]
        cum_r = self.db.sum_shadow_pnl_r()
        report = build_asia_shadow_report(trades, session_date, cum_r, open_pos)
        report += "\n\n" + self._build_data_health_block()
        await self.alerts.send(report, AlertTier.INFO)

    # ── Entry Logic ──────────────────────────────────────────────

    def _entry_halt_reason(self) -> str | None:
        if self._entries_halted:
            if self.db.get_state("promotion_halt", "0") == "1":
                return "promotion_kill_halt"
            return "entries_halted_manual"
        now = datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        week_start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
        day_r = self.db.sum_closed_pnl_r_since(day_start)
        week_r = self.db.sum_closed_pnl_r_since(week_start)
        if day_r <= self.daily_loss_stop_r:
            return f"daily_loss_stop ({day_r:+.2f}R <= {self.daily_loss_stop_r:+.1f}R)"
        if week_r <= self.weekly_loss_stop_r:
            return f"weekly_loss_stop ({week_r:+.2f}R <= {self.weekly_loss_stop_r:+.1f}R)"
        return None

    async def _check_entry(self, symbol: str, candle: Candle):
        # Position limits
        sym_positions = [p for p in self.open_positions if p["symbol"] == symbol and not p.get("_closed")]
        if len(sym_positions) >= self.max_per_symbol:
            return

        active_positions = [p for p in self.open_positions if not p.get("_closed")]
        if len(active_positions) >= self.max_positions:
            return

        if self.executor.equity <= 0:
            return

        if symbol in self.blocked_symbols:
            return

        halt = self._entry_halt_reason()
        if halt:
            logger.info("Entry blocked", symbol=symbol, reason=halt)
            return

        # Duplicate check — V6: uses signal side, not hardcoded LONG
        dup_key = f"{symbol}_{candle.close_time.isoformat()}"
        if self.db.has_dup(dup_key):
            return

        candles_5m = list(self.candle_buffers.get(symbol, []))
        try:
            sig = self.engine.evaluate(symbol, candles_5m)
        except Exception as e:
            logger.error("evaluate() error", symbol=symbol, error=str(e), exc_info=True)
            return

        if sig is None:
            return

        sig.signal_data["strategy_version"] = STRATEGY_VERSION
        entry_hour = parse_entry_hour(candle.close_time.isoformat())
        confirms = sig.signal_data.get("confirmations", {})

        self.db.mark_dup(dup_key)
        
        st = self.engine._get_state(symbol)
        decile = st.decile # get from engine state after evaluate

        # V5.1 Dynamic Vol-Targeting Risk (capped by gates.max_risk_pct)
        risk_pct = min(float(sig.signal_data.get("risk_pct", BASE_RISK_PCT)), self.max_risk_pct)

        sd = sig.stop_price
        if sd <= 0:
            return

        # risk_pct is a fraction (e.g. 0.184 for 18.4%)
        ra = self.executor.equity * risk_pct
        qty = ra / abs(sig.entry_price - sig.stop_price)
        notional = qty * sig.entry_price
        lev = min(int(notional / self.executor.equity) + 1, self.max_leverage)
        lev = max(lev, 1)
        max_notional = self.executor.equity * lev * 0.95
        if notional > max_notional:
            qty = max_notional / sig.entry_price
        if qty <= 0:
            return

        # Execute
        fill, fee = self.executor.fill_entry(sig.entry_price, qty, sig.side)

        pos = {
            "trade_uuid": sig.trade_uuid,
            "symbol": symbol,
            "side": sig.side.value,
            "entry_price": round(fill, 6),
            "quantity": round(qty, 6),
            "orig_quantity": round(qty, 6),
            "leverage": lev,
            "stop_price": round(sig.stop_price, 6),
            "init_stop": round(sig.stop_price, 6),
            "tp1_hit": 0,
            "trail_active": 0,
            "candles_held": 0,
            "entry_time": candle.close_time.isoformat(),
            "rpnl": 0.0,
            "fees": fee,
            "confirmations": json.dumps(sig.signal_data.get("confirmations", {})),
            "mae": 0.0,
            "mfe": 0.0,
            "aggression": float(self.engine._get_state(symbol).aggression_score),
            "decile": decile,
        }

        self.open_positions.append(pos)
        self.db.save_position(pos)

        agg = float(self.engine._get_state(symbol).aggression_score)
        await self.alerts.send(
            f"📈 ENTRY LONG {symbol}\n"
            f"Price: {fill:.6f} | Stop: {sig.stop_price:.6f}\n"
            f"Risk: {float(risk_pct)*100:.1f}% | Lev: {lev}x | Qty: {qty:.4f}\n"
            f"Aggression: {agg:.0f} (D{decile})\n"
            f"Positions: {len(self.open_positions)}/{self.max_positions}",
            AlertTier.INFO,
        )

        logger.info("Entry", symbol=symbol, price=round(fill, 6),
                     risk=float(risk_pct), aggression=round(agg, 1), decile=decile,
                     vol_z=round(float(sig.signal_data.get("vol_z", 0)), 2),
                     imb_z=round(float(sig.signal_data.get("imb_z", 0)), 2),
                     bd_pct=round(float(sig.signal_data.get("breakout_distance_pct", 0)), 2),
                     hour=entry_hour, cascade=round(float(st.cascade_strength), 2))

        # ── Telemetry: log entry snapshot ──
        if self._telemetry:
            try:
                shadow_filters = evaluate_entry_shadow_filters(
                    symbol=symbol,
                    decile=decile,
                    entry_hour=entry_hour,
                    confirmations=confirms,
                )
                self._telemetry.log_shadow_filters(sig.trade_uuid, shadow_filters)
                # Store entry context on position for shadow exits
                entry_ctx = {
                    "range_high": sig.signal_data.get("range_high", 0),
                    "ema_value": sig.signal_data.get("ema_value", 0),
                    "decile": decile,
                }
                pos["_entry_context"] = json.dumps(entry_ctx)
                self.db.save_position(pos)  # re-save with context

                btc_price = self.last_prices.get("BTCUSDT", 0)
                active_count = sum(1 for p in self.open_positions if not p.get("_closed"))
                self._telemetry.log_entry(
                    trade_uuid=sig.trade_uuid,
                    symbol=symbol,
                    side=sig.side.value,
                    entry_time=candle.close_time.isoformat(),
                    entry_price=fill,
                    stop_price=sig.stop_price,
                    signal_data=sig.signal_data,
                    engine_state={
                        "cascade_active": st.cascade_active,
                        "cascade_strength": st.cascade_strength,
                        "liq_direction_imb": st.liq_direction_imb,
                        "ret_5d": st.ret_5d,
                    },
                    equity=float(self.executor.equity),
                    open_count=active_count,
                    btc_price=btc_price,
                    is_experimental=1 if symbol in self.experimental_symbols else 0,
                )
            except Exception as e:
                logger.debug("Telemetry entry logging error (non-fatal)", error=str(e))

    async def _check_promotion_rules(self):
        status = evaluate_promotion_status(self.db.get_all_trades())
        key = f"{status['status']}:{status['n']}:{status.get('total_r', 0)}"
        if status.get("message") and key != self._promotion_alert_sent:
            tier = AlertTier.INFO
            if status.get("alert_tier") == "warning":
                tier = AlertTier.WARNING
            elif status.get("alert_tier") == "critical":
                tier = AlertTier.CRITICAL
            await self.alerts.send(status["message"], tier)
            self._promotion_alert_sent = key
            self.db.set_state("promotion_alert_status", key)
        if status.get("halt_entries"):
            self._entries_halted = True
            self.db.set_state("promotion_halt", "1")

    # ── NY Session Close Report ───────────────────────────────────

    async def _ny_session_report_loop(self):
        while not self._shutdown.is_set():
            for _ in range(60):
                if self._shutdown.is_set():
                    break
                await asyncio.sleep(1)
            if self._shutdown.is_set():
                break

            now = datetime.now(timezone.utc)
            today = now.strftime("%Y-%m-%d")
            if today == self._last_session_report_date:
                continue
            if now.hour != NY_SESSION_REPORT_HOUR or now.minute < NY_SESSION_REPORT_MINUTE:
                continue

            await self._send_ny_session_report(today)
            self._last_session_report_date = today
            self.db.set_state("last_session_report_date", today)

    async def _send_ny_session_report(self, session_date: str):
        trades = self.db.get_trades_on_date(session_date)
        open_pos = [p for p in self.open_positions if not p.get("_closed")]
        report = build_session_report(
            trades, session_date, float(self.executor.equity), open_pos,
        )
        report += "\n\n" + self._build_data_health_block()
        await self.alerts.send(report, AlertTier.INFO)

    # ── Daily Report ─────────────────────────────────────────────

    async def _daily_report_loop(self):
        while not self._shutdown.is_set():
            # Sleep in 1-second chunks to exit promptly on shutdown
            for _ in range(60):
                if self._shutdown.is_set():
                    break
                await asyncio.sleep(1)
            if self._shutdown.is_set():
                break

            now = datetime.now(timezone.utc)
            today = now.strftime("%Y-%m-%d")

            if today == self._last_report_date:
                continue
            if now.hour != DAILY_REPORT_HOUR or now.minute < DAILY_REPORT_MINUTE:
                continue

            await self._send_daily_report()
            self._last_report_date = today
            self.db.set_state("last_report_date", today)

    async def _send_daily_report(self):
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        trades = self.db.get_trades_since(yesterday)
        all_trades = self.db.get_all_trades()

        n = len(trades)
        wins = sum(1 for t in trades if t["pnl_r"] > 0)
        wr = (wins / n * 100) if n > 0 else 0
        total_r = sum(t["pnl_r"] for t in trades)

        # Per-decile breakdown
        decile_stats = defaultdict(lambda: {"n": 0, "r": 0})
        for t in trades:
            d = t.get("decile", 0)
            decile_stats[d]["n"] += 1
            decile_stats[d]["r"] += t["pnl_r"]

        unrealized_r = self._calc_unrealized_r()

        sym_stats = defaultdict(lambda: {"n": 0, "r": 0})
        for t in trades:
            sym_stats[t["symbol"]]["n"] += 1
            sym_stats[t["symbol"]]["r"] += t["pnl_r"]

        dd = 0
        if self.executor.peak > 0:
            dd = ((self.executor.peak - self.executor.equity) / self.executor.peak) * 100

        all_n = len(all_trades)
        all_r = sum(t["pnl_r"] for t in all_trades)
        all_wins = sum(1 for t in all_trades if t["pnl_r"] > 0)
        all_wr = (all_wins / all_n * 100) if all_n > 0 else 0

        n_cascade = sum(1 for s in self.symbols if self.engine._get_state(s).cascade_active)

        report = (
            f"📊 {STRATEGY_VERSION} DAILY REPORT\n\n"
            f"Today: {n} trades | WR: {wr:.0f}% | R: {total_r:+.2f}\n\n"
            f"Open: {len(self.open_positions)} positions\n"
            f"  Unrealized: {unrealized_r:+.2f}R\n\n"
            f"All-time: {all_n} trades | WR: {all_wr:.0f}% | R: {all_r:+.2f}\n"
            f"Equity: {float(self.executor.equity):.2f} (DD: {dd:.1f}%)\n"
            f"Cascades: {n_cascade}/{len(self.symbols)} active\n"
        )

        # Per-decile breakdown
        if decile_stats:
            report += "\nPer-decile:\n"
            for d in sorted(decile_stats.keys()):
                st = decile_stats[d]
                report += f"  D{d}: {st['n']}t {st['r']:+.2f}R\n"

        # Per-symbol breakdown (top 5)
        if sym_stats:
            sorted_syms = sorted(sym_stats.items(), key=lambda x: abs(x[1]["r"]), reverse=True)[:5]
            report += "\nPer-symbol (top 5):\n"
            for sym, st in sorted_syms:
                report += f"  {sym}: {st['n']}t {st['r']:+.2f}R\n"

        # Inline safe filtering for separated reporting
        core_today = [t for t in trades if not t.get("is_experimental")]
        exp_today = [t for t in trades if t.get("is_experimental") == 1]
        core_all = [t for t in all_trades if not t.get("is_experimental")]
        exp_all = [t for t in all_trades if t.get("is_experimental") == 1]

        n_core, n_exp = len(core_today), len(exp_today)
        r_core = sum(t["pnl_r"] for t in core_today)
        r_exp = sum(t["pnl_r"] for t in exp_today)

        all_n_core, all_n_exp = len(core_all), len(exp_all)
        all_r_core = sum(t["pnl_r"] for t in core_all)
        all_r_exp = sum(t["pnl_r"] for t in exp_all)

        unrealized_core, unrealized_exp = self._calc_unrealized_r_by_group()

        # Append clean, isolated stats at the end of the existing report
        report += (
            f"\n🔍 ACCOUNTING ISOLATION (A/B vs C):\n"
            f"  • Today Proven: {n_core}t ({r_core:+.2f}R) | Experimental: {n_exp}t ({r_exp:+.2f}R)\n"
            f"  • All-Time Proven: {all_n_core}t ({all_r_core:+.2f}R) | Experimental: {all_n_exp}t ({all_r_exp:+.2f}R)\n"
            f"  • Unrealized Proven: {unrealized_core:+.2f}R | Experimental: {unrealized_exp:+.2f}R\n"
        )
        report += "\n" + self._build_data_health_block()

        await self.alerts.send(report, AlertTier.INFO)

    def _calc_unrealized_r(self):
        total = 0
        for p in self.open_positions:
            if p.get("_closed"):
                continue
            sym = p["symbol"]
            price = self.last_prices.get(sym, 0)
            if price <= 0:
                continue
            sd = abs(p["entry_price"] - p["init_stop"])
            if sd > 0:
                side = Side(p["side"])
                if side == Side.LONG:
                    total += (price - p["entry_price"]) / sd
                else:
                    total += (p["entry_price"] - price) / sd
        return total

    def _calc_unrealized_r_by_group(self):
        unrealized_core = 0.0
        unrealized_exp = 0.0
        for p in self.open_positions:
            if p.get("_closed"):
                continue
            sym = p["symbol"]
            price = self.last_prices.get(sym, 0)
            if price <= 0:
                continue
            sd = abs(p["entry_price"] - p["init_stop"])
            if sd > 0:
                side = Side(p["side"])
                diff = (price - p["entry_price"]) if side == Side.LONG else (p["entry_price"] - price)
                r_val = diff / sd
                if sym in self.experimental_symbols:
                    unrealized_exp += r_val
                else:
                    unrealized_core += r_val
        return unrealized_core, unrealized_exp


# ═══════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════

async def async_main():
    setup_logging(level="INFO", log_file="logs/v5_forward_test.log")
    runner = V5ForwardTest()
    await runner.start()

def main():
    asyncio.run(async_main())

if __name__ == "__main__":
    main()

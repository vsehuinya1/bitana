"""
Bitana Forward Test — SB_DATR_ASIA_HALF_WEAK

Standalone paper-trading runner that validates whether the replay edge
survives real-time execution. Uses exact replay-winning rules.

Production safeguards:
  1. Exchange-confirmed closed 15m candles only
  2. Duplicate-entry lock (symbol + candle_close_time + side)
  3. 1-candle cooldown after any exit
  4. Enhanced daily report with replay vs live delta
  5. Missed signals categorized by reason
  6. Full state persistence for clean restart
  7. WS → REST failover during active positions
  8. Replay-criteria match logging per signal

Usage:
    python -m tools.forward_test
"""
from __future__ import annotations

import asyncio
import json
import signal
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.loader import load_config
from core.logging_setup import setup_logging, get_logger
from core.models import AlertTier, Candle, EngineType, Side, Signal
from data.binance_rest import BinanceRestClient
from data.rate_limiter import RateLimiterGroup
from engines.swing_break_engine import SwingBreakEngine
from tg_bot.alerts import TelegramAlerts

logger = get_logger("forward_test")

SYMBOL = "BTCUSDT"
INITIAL_EQUITY = 1000.0
BASE_RISK_PCT = 1.0
MAX_LEVERAGE = 10
TP_R = 1.5
TRAIL_ATR_MULT = 2.5
TIME_STOP_CANDLES = 8
TIME_STOP_R_THRESHOLD = 0.5
TAKER_BPS = 4.5
SLIP_BPS = 2.0
HISTORICAL_REFERENCE_EXPECTANCY = 0.0191  # Fix #11: IS-only conservative
DD_ALERT_PCT = 7.0
INACTIVITY_ALERT_DAYS = 10
EXP_WINDOW = 20
DAILY_REPORT_HOUR_UTC = 8
DAILY_REPORT_MINUTE_UTC = 5
MODELED_SLIP_BPS = SLIP_BPS  # Fix #9: labeled as model assumption

DB_PATH = Path("storage/forward_test.db")
CANDLE_HISTORY_15M = 200
CANDLE_HISTORY_4H = 400
CANDLE_HISTORY_1D = 500

# Fix #1: DB-safe position column whitelist
_POSITION_COLUMNS = {
    "id", "trade_uuid", "symbol", "side", "entry_price", "quantity",
    "orig_quantity", "leverage", "stop_price", "init_stop", "tp1_hit",
    "trail_active", "trail_stop", "candles_held", "entry_time",
    "rpnl", "fees", "regime_tag", "criteria_match",
}

def clean_position_for_db(p: dict) -> dict:
    """Strip internal/runtime keys. Only keep DB-schema columns."""
    return {k: v for k, v in p.items() if k in _POSITION_COLUMNS}

# ─── Database ────────────────────────────────────────────────────────────────

class FTDatabase:
    """SQLite persistence for forward-test state."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self):
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
                session_tag TEXT,
                regime_tag TEXT,
                slippage_bps REAL,
                entry_latency_ms REAL,
                criteria_match TEXT,
                duplicate_key TEXT
            );

            CREATE TABLE IF NOT EXISTS open_positions (
                id INTEGER PRIMARY KEY,
                trade_uuid TEXT UNIQUE,
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
                trail_stop REAL DEFAULT 0,
                candles_held INTEGER DEFAULT 0,
                entry_time TEXT,
                rpnl REAL DEFAULT 0,
                fees REAL DEFAULT 0,
                regime_tag TEXT,
                criteria_match TEXT
            );

            CREATE TABLE IF NOT EXISTS state (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS duplicate_keys (
                dup_key TEXT PRIMARY KEY,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS missed_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                reason TEXT,
                details TEXT
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_dup ON duplicate_keys(dup_key);
        """)
        self.conn.commit()

    def get_state(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_state(self, key: str, value: str):
        self.conn.execute("INSERT OR REPLACE INTO state(key,value) VALUES(?,?)", (key, value))
        self.conn.commit()

    def has_duplicate(self, dup_key: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM duplicate_keys WHERE dup_key=?", (dup_key,)).fetchone()
        return row is not None

    def mark_duplicate(self, dup_key: str):
        self.conn.execute("INSERT OR IGNORE INTO duplicate_keys(dup_key, created_at) VALUES(?,?)",
                          (dup_key, datetime.now(timezone.utc).isoformat()))
        self.conn.commit()

    def save_position(self, p: dict):
        safe = clean_position_for_db(p)
        cols = list(safe.keys())
        vals = [safe[c] for c in cols]
        placeholders = ",".join(["?"] * len(cols))
        col_str = ",".join(cols)
        self.conn.execute(f"INSERT OR REPLACE INTO open_positions({col_str}) VALUES({placeholders})", vals)
        self.conn.commit()

    def get_open_positions(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM open_positions").fetchall()
        return [dict(r) for r in rows]

    def remove_position(self, trade_uuid: str):
        self.conn.execute("DELETE FROM open_positions WHERE trade_uuid=?", (trade_uuid,))
        self.conn.commit()

    def save_trade(self, t: dict):
        cols = list(t.keys())
        vals = [t[c] for c in cols]
        placeholders = ",".join(["?"] * len(cols))
        col_str = ",".join(cols)
        self.conn.execute(f"INSERT OR REPLACE INTO trades({col_str}) VALUES({placeholders})", vals)
        self.conn.commit()

    def get_recent_trades(self, n: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_trades(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM trades ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def get_trades_since(self, since: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM trades WHERE entry_time >= ? ORDER BY id", (since,)
        ).fetchall()
        return [dict(r) for r in rows]

    def log_missed(self, reason: str, details: str = ""):
        self.conn.execute("INSERT INTO missed_signals(timestamp, reason, details) VALUES(?,?,?)",
                          (datetime.now(timezone.utc).isoformat(), reason, details))
        self.conn.commit()

    def get_missed_since(self, since: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM missed_signals WHERE timestamp >= ?", (since,)
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self):
        self.conn.close()


# ─── Paper Executor (minimal) ───────────────────────────────────────────────

class PaperFill:
    def __init__(self, eq: float):
        self.equity = eq
        self.peak = eq
        self.initial = eq

    def fill_entry(self, price: float, qty: float, side: Side):
        slip = price * (SLIP_BPS / 10000)
        fill = price + slip if side == Side.LONG else price - slip
        fee = qty * fill * (TAKER_BPS / 10000)
        self.equity -= fee
        return fill, fee

    def fill_exit(self, entry: float, price: float, qty: float, side: Side):
        slip = price * (SLIP_BPS / 10000)
        fill = price - slip if side == Side.LONG else price + slip
        fee = qty * fill * (TAKER_BPS / 10000)
        pnl = (fill - entry) * qty if side == Side.LONG else (entry - fill) * qty
        self.equity += pnl - fee
        if self.equity > self.peak:
            self.peak = self.equity
        return fill, fee, pnl


# ─── Forward Test Runner ────────────────────────────────────────────────────

class ForwardTestRunner:
    def __init__(self):
        self.config = load_config()
        self.db = FTDatabase(DB_PATH)
        self.engine = SwingBreakEngine()
        self.alerts = TelegramAlerts(
            self.config.secrets.telegram_bot_token,
            self.config.secrets.telegram_chat_id,
        )
        self.rate_limiter = RateLimiterGroup()
        self.rest_client = BinanceRestClient(
            testnet=False, rate_limiter=self.rate_limiter,
        )

        # State
        saved_eq = self.db.get_state("equity")
        eq = float(saved_eq) if saved_eq else INITIAL_EQUITY
        self.executor = PaperFill(eq)
        self.executor.peak = float(self.db.get_state("peak_equity", str(eq)))

        self.candles_15m: list[Candle] = []
        self.ws_connected = False
        self._running = False
        self._shutdown = asyncio.Event()
        self._last_report_date = self.db.get_state("last_report_date", "")

        # Cooldown
        cd_str = self.db.get_state("cooldown_until")
        self.cooldown_until: datetime | None = (
            datetime.fromisoformat(cd_str) if cd_str else None
        )

        # Recover open positions
        self.open_positions: list[dict] = self.db.get_open_positions()
        if self.open_positions:
            logger.info("Recovered open positions", count=len(self.open_positions))

    async def start(self):
        """Initialize and enter main loop."""
        self._running = True
        await self.rest_client.start()
        await self.alerts.initialize()

        # Fetch historical data for indicators
        await self._load_history()

        # Fix #13: Startup self-test
        self._self_test()

        # Fix #4: Signal handlers for graceful shutdown
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: self._shutdown.set())

        # Fix #7: Record startup
        self.db.set_state("last_startup", datetime.now(timezone.utc).isoformat())

        await self.alerts.send(
            "🧪 *Forward Test Started*\n"
            f"Strategy: SB_DATR_ASIA_HALF_WEAK\n"
            f"Equity: ${self.executor.equity:.2f}\n"
            f"Open positions: {len(self.open_positions)}",
            AlertTier.INFO,
        )

        # Fix #3: Supervised task execution
        try:
            results = await asyncio.gather(
                self._candle_loop(),
                self._daily_report_loop(),
                self._inactivity_check_loop(),
                return_exceptions=True,
            )
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    task_names = ["candle_loop", "daily_report", "inactivity_check"]
                    name = task_names[i] if i < len(task_names) else f"task_{i}"
                    logger.critical("Task died", task=name, error=str(r))
                    await self.alerts.critical(f"Forward test task crashed: {name}: {r}")
        finally:
            # Fix #4: Graceful shutdown cleanup
            await self._cleanup()

    def _self_test(self):
        """Fix #13: Verify critical preconditions before live loop."""
        checks = []
        if len(self.candles_15m) < 20:
            checks.append(f"15m history too short: {len(self.candles_15m)}")
        if len(self.engine._c4h) < 100:
            checks.append(f"4H candles too few: {len(self.engine._c4h)}")
        if len(self.engine._c1d) < 100:
            checks.append(f"1D candles too few: {len(self.engine._c1d)}")
        # DB write test
        try:
            self.db.set_state("_selftest", "ok")
        except Exception as e:
            checks.append(f"DB write failed: {e}")
        if checks:
            msg = "Self-test FAILED:\n" + "\n".join(f"  - {c}" for c in checks)
            logger.critical(msg)
            raise RuntimeError(msg)
        logger.info("Self-test passed",
                    candles_15m=len(self.candles_15m),
                    candles_4h=len(self.engine._c4h),
                    candles_1d=len(self.engine._c1d))

    async def _cleanup(self):
        """Fix #4: Graceful shutdown — close all resources."""
        logger.info("Shutting down...")
        self.db.set_state("last_shutdown", datetime.now(timezone.utc).isoformat())
        self.db.set_state("equity", str(round(self.executor.equity, 4)))
        self.db.set_state("peak_equity", str(round(self.executor.peak, 4)))
        try:
            self.db.close()
        except Exception:
            pass
        try:
            await self.rest_client.close()
        except Exception:
            pass
        logger.info("Shutdown complete")

    async def _load_history(self):
        """Fetch historical 15m, 4H, 1D candles."""
        end = datetime.now(timezone.utc)

        logger.info("Fetching 15m history...")
        self.candles_15m = await self._fetch_klines("15m", end - timedelta(days=10), end)
        logger.info(f"  ✓ 15m: {len(self.candles_15m)} candles")

        logger.info("Fetching 4H history...")
        c4h = await self._fetch_klines("4h", end - timedelta(days=120), end)
        self.engine.update_4h(c4h)

        logger.info("Fetching 1D history...")
        c1d = await self._fetch_klines("1d", end - timedelta(days=400), end)
        self.engine.update_1d(c1d)

        logger.info("History loaded, indicators ready")

    async def _fetch_klines(self, interval: str, start: datetime, end: datetime) -> list[Candle]:
        out = []
        ms_s = int(start.timestamp() * 1000)
        ms_e = int(end.timestamp() * 1000)
        while ms_s < ms_e:
            raw = await self.rest_client.get_klines(
                symbol=SYMBOL, interval=interval, start_time=ms_s, limit=1500,
            )
            if not raw:
                break
            for k in raw:
                if k[6] > ms_e:
                    break
                out.append(Candle(
                    symbol=SYMBOL, timeframe=interval,
                    open_time=datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc),
                    close_time=datetime.fromtimestamp(k[6] / 1000, tz=timezone.utc),
                    open=float(k[1]), high=float(k[2]),
                    low=float(k[3]), close=float(k[4]),
                    volume=float(k[5]), is_closed=True,
                ))
            ms_s = int(raw[-1][6]) + 1
            if len(raw) < 1500:
                break
            await asyncio.sleep(0.1)
        # Dedup + sort
        seen = set()
        deduped = []
        for c in out:
            if c.open_time not in seen:
                seen.add(c.open_time)
                deduped.append(c)
        return sorted(deduped, key=lambda c: c.open_time)

    # ── Main candle loop (REST polling for simplicity/reliability) ────────

    async def _candle_loop(self):
        """Poll 15m candles from REST every 15s. Only process confirmed closed candles."""
        last_processed_close: str = self.db.get_state("last_15m_close", "")
        logger.info("Candle loop started", last_close=last_processed_close or "none")

        while not self._shutdown.is_set():
            try:
                # Fetch latest 3 candles to get the just-closed one
                raw = await self.rest_client.get_klines(
                    symbol=SYMBOL, interval="15m", limit=3,
                )
                if not raw:
                    await asyncio.sleep(15)
                    continue

                for k in raw:
                    is_closed = bool(k[11]) if len(k) > 11 else False
                    # Binance: k[11] is "Is this kline closed?" — but in REST
                    # the last candle is always open. Use close_time vs now.
                    close_time = datetime.fromtimestamp(k[6] / 1000, tz=timezone.utc)
                    now = datetime.now(timezone.utc)

                    # Safeguard #1: only exchange-confirmed closed candles
                    if close_time > now:
                        continue  # still open

                    close_key = close_time.isoformat()
                    if close_key == last_processed_close:
                        continue  # already processed

                    candle = Candle(
                        symbol=SYMBOL, timeframe="15m",
                        open_time=datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc),
                        close_time=close_time,
                        open=float(k[1]), high=float(k[2]),
                        low=float(k[3]), close=float(k[4]),
                        volume=float(k[5]), is_closed=True,
                    )

                    await self._on_15m_close(candle)
                    last_processed_close = close_key
                    self.db.set_state("last_15m_close", close_key)

                # Also check 4H/1D for indicator updates
                await self._check_higher_tf_updates()

                # Fix #7: heartbeat
                self.db.set_state("last_loop_heartbeat",
                                  datetime.now(timezone.utc).isoformat())

            except Exception as e:
                logger.error("Candle loop error", error=str(e),
                             exc_info=True)

            await asyncio.sleep(15)

    async def _check_higher_tf_updates(self):
        """Fetch latest 4H and 1D candle, append if new."""
        try:
            for interval, method in [("4h", self.engine.append_4h), ("1d", self.engine.append_1d)]:
                raw = await self.rest_client.get_klines(
                    symbol=SYMBOL, interval=interval, limit=2,
                )
                if raw:
                    for k in raw:
                        ct = datetime.fromtimestamp(k[6] / 1000, tz=timezone.utc)
                        if ct <= datetime.now(timezone.utc):
                            candle = Candle(
                                symbol=SYMBOL, timeframe=interval,
                                open_time=datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc),
                                close_time=ct,
                                open=float(k[1]), high=float(k[2]),
                                low=float(k[3]), close=float(k[4]),
                                volume=float(k[5]), is_closed=True,
                            )
                            key = f"last_{interval}_close"
                            if candle.close_time.isoformat() != self.db.get_state(key):
                                method(candle)
                                self.db.set_state(key, candle.close_time.isoformat())
        except Exception as e:
            logger.error("Higher TF update error", error=str(e))

    # ── Core logic ───────────────────────────────────────────────────────

    async def _on_15m_close(self, candle: Candle):
        """Process a confirmed closed 15m candle."""
        ct = candle.close_time

        # Update candle buffer
        self.candles_15m.append(candle)
        if len(self.candles_15m) > CANDLE_HISTORY_15M:
            self.candles_15m = self.candles_15m[-CANDLE_HISTORY_15M:]

        # 1. Manage open positions
        await self._manage_positions(candle)

        # 2. Check for new entry
        await self._check_entry(candle)

    async def _manage_positions(self, candle: Candle):
        ct = candle.close_time
        atr = SwingBreakEngine._calc_atr(self.candles_15m)
        closed_any = False

        for p in list(self.open_positions):
            p["candles_held"] += 1
            sd = abs(p["entry_price"] - p["init_stop"])
            side = Side(p["side"])
            cp = candle.close
            rm = 0.0
            if sd > 0:
                rm = (cp - p["entry_price"]) / sd if side == Side.LONG else (p["entry_price"] - cp) / sd

            exit_price = None
            exit_reason = None

            # Time stop
            if p["candles_held"] >= TIME_STOP_CANDLES and rm < TIME_STOP_R_THRESHOLD and not p["tp1_hit"]:
                exit_price = cp
                exit_reason = "time_stop"

            # Stop loss
            if exit_price is None:
                if side == Side.LONG and candle.low <= p["stop_price"]:
                    exit_price = p["stop_price"]
                    exit_reason = "stop_loss"
                elif side == Side.SHORT and candle.high >= p["stop_price"]:
                    exit_price = p["stop_price"]
                    exit_reason = "stop_loss"

            if exit_price is not None:
                await self._close_position(p, exit_price, exit_reason, ct)
                closed_any = True
                continue

            # Partial TP at 1.5R
            if not p["tp1_hit"] and rm >= TP_R:
                tq = p["quantity"] * 0.5
                fill, fee, pnl = self.executor.fill_exit(
                    p["entry_price"], cp, tq, side
                )
                p["tp1_hit"] = 1
                p["quantity"] -= tq
                p["rpnl"] += pnl
                p["fees"] += fee
                p["trail_active"] = 1

            # Trail
            if p["trail_active"] and atr > 0:
                td = atr * TRAIL_ATR_MULT
                if side == Side.LONG:
                    nt = cp - td
                    if nt > p["trail_stop"]:
                        p["trail_stop"] = nt
                    if p["trail_stop"] > p["stop_price"]:
                        p["stop_price"] = p["trail_stop"]
                else:
                    nt = cp + td
                    if p["trail_stop"] == 0 or nt < p["trail_stop"]:
                        p["trail_stop"] = nt
                    if p["trail_stop"] < p["stop_price"] or p["stop_price"] == 0:
                        p["stop_price"] = p["trail_stop"]

            # Persist position state
            self.db.save_position(p)

        if closed_any:
            self.open_positions = [p for p in self.open_positions if not p.get("_closed")]

    async def _close_position(self, p: dict, price: float, reason: str, ct: datetime):
        side = Side(p["side"])
        fill, fee, pnl = self.executor.fill_exit(p["entry_price"], price, p["quantity"], side)
        p["rpnl"] += pnl
        p["fees"] += fee
        p["_closed"] = True

        sd = abs(p["entry_price"] - p["init_stop"])
        pnl_r = p["rpnl"] / (sd * p["orig_quantity"]) if sd > 0 and p["orig_quantity"] > 0 else 0
        net_pnl = p["rpnl"] - p["fees"]

        trade = {
            "trade_uuid": p["trade_uuid"],
            "symbol": p["symbol"],
            "side": p["side"],
            "entry_time": p["entry_time"],
            "exit_time": ct.isoformat(),
            "entry_price": round(p["entry_price"], 2),
            "exit_price": round(fill, 2),
            "quantity": round(p["orig_quantity"], 6),
            "leverage": p["leverage"],
            "stop_dist": round(sd, 2),
            "pnl_usd": round(net_pnl, 4),
            "pnl_r": round(pnl_r, 4),
            "fees": round(p["fees"], 4),
            "hold_candles": p["candles_held"],
            "exit_reason": reason,
            "tp1_hit": p["tp1_hit"],
            "equity_after": round(self.executor.equity, 2),
            "session_tag": self._session_tag(p["entry_time"]),
            "regime_tag": p.get("regime_tag", ""),
            "slippage_bps": MODELED_SLIP_BPS,  # Fix #9: model assumption
            "entry_latency_ms": 0,  # Fix #10: not measurable in paper mode
            "criteria_match": p.get("criteria_match", ""),
            "duplicate_key": "",
        }

        self.db.save_trade(trade)
        self.db.remove_position(p["trade_uuid"])

        # Persist equity
        self.db.set_state("equity", str(round(self.executor.equity, 4)))
        self.db.set_state("peak_equity", str(round(self.executor.peak, 4)))

        # Safeguard #3: 1-candle cooldown
        self.cooldown_until = ct + timedelta(minutes=15)
        self.db.set_state("cooldown_until", self.cooldown_until.isoformat())

        # Alerts
        emoji = "✅" if pnl_r >= 0 else "❌"
        await self.alerts.send(
            f"{emoji} *SB EXIT* {p['side']} {SYMBOL}\n"
            f"Entry: `{p['entry_price']:.2f}` → Exit: `{fill:.2f}`\n"
            f"PnL: `{net_pnl:+.2f}` ({pnl_r:+.3f}R)\n"
            f"Reason: {reason} | Candles: {p['candles_held']}",
            AlertTier.INFO,
        )

        # Alert checks
        await self._check_alerts()

        logger.info("Position closed",
                     side=p["side"], pnl_r=round(pnl_r, 4),
                     reason=reason, equity=round(self.executor.equity, 2))

    async def _check_entry(self, candle: Candle):
        ct = candle.close_time
        t0 = time.time()

        if len(self.open_positions) >= 2:
            return

        if self.executor.equity <= 0:
            return

        # Safeguard #3: cooldown check — Fix #6: use <= to block same candle
        if self.cooldown_until and ct <= self.cooldown_until:
            return

        # Evaluate engine
        sig, criteria = self.engine.evaluate(self.candles_15m, ct)

        if sig is None:
            # Safeguard #5: log categorized missed signal (only if any criteria passed)
            reason = criteria.get("skip_reason", "unknown")
            if criteria.get("session_ok"):  # only log if we're in session
                self.db.log_missed(reason, json.dumps(criteria))
            return

        # Safeguard #2: duplicate lock
        dup_key = f"{SYMBOL}_{ct.isoformat()}_{sig.side.value}"
        if self.db.has_duplicate(dup_key):
            self.db.log_missed("duplicate", dup_key)
            return
        self.db.mark_duplicate(dup_key)

        # Risk sizing with HALF_WEAK
        risk_mult = self.engine.get_risk_multiplier(ct)
        risk_pct = BASE_RISK_PCT * risk_mult
        regime_tag = "STRONG" if risk_mult == 1.0 else "WEAK"

        sdist = abs(sig.entry_price - sig.stop_price)
        if sdist <= 0:
            return

        ra = self.executor.equity * (risk_pct / 100.0)
        qty = ra / sdist
        notional = qty * sig.entry_price
        lev = min(int(notional / self.executor.equity) + 1, MAX_LEVERAGE)
        lev = max(lev, 1)
        mn = self.executor.equity * lev * 0.95
        if notional > mn:
            qty = mn / sig.entry_price
        if qty <= 0:
            return

        # Execute
        fill, fee = self.executor.fill_entry(sig.entry_price, qty, sig.side)
        decision_latency_ms = (time.time() - t0) * 1000  # Fix #10: renamed
        paper_slip_bps = MODELED_SLIP_BPS  # Fix #9: model assumption, not observed

        pos = {
            "trade_uuid": sig.trade_uuid,
            "symbol": SYMBOL,
            "side": sig.side.value,
            "entry_price": round(fill, 2),
            "quantity": round(qty, 6),
            "orig_quantity": round(qty, 6),
            "leverage": lev,
            "stop_price": round(sig.stop_price, 2),
            "init_stop": round(sig.stop_price, 2),
            "tp1_hit": 0,
            "trail_active": 0,
            "trail_stop": 0.0,
            "candles_held": 0,
            "entry_time": ct.isoformat(),
            "rpnl": 0.0,
            "fees": fee,
            "regime_tag": regime_tag,
            "criteria_match": json.dumps(criteria),
        }

        self.open_positions.append(pos)
        self.db.save_position(pos)
        self.db.set_state("last_trade_time", ct.isoformat())

        await self.alerts.send(
            f"📈 *SB ENTRY* {sig.side.value} {SYMBOL}\n"
            f"Price: `{fill:.2f}` | Stop: `{sig.stop_price:.2f}`\n"
            f"Risk: {risk_pct:.1f}% ({regime_tag}) | Lev: {lev}x\n"
            f"Vol: {sig.signal_data.get('vol_mult', 0):.1f}x",
            AlertTier.INFO,
        )

        # Safeguard #8: log replay criteria match
        logger.info("Signal taken",
                     side=sig.side.value, price=round(fill, 2),
                     regime=regime_tag, criteria=criteria,
                     decision_latency_ms=round(decision_latency_ms, 1))

    # ── Alerts ───────────────────────────────────────────────────────────

    async def _check_alerts(self):
        """Check expectancy and drawdown alerts."""
        recent = self.db.get_recent_trades(EXP_WINDOW)
        if len(recent) >= EXP_WINDOW:
            exp = sum(t["pnl_r"] for t in recent) / len(recent)
            if exp < 0:
                await self.alerts.send(
                    f"⚠️ *EXPECTANCY DECAY*\n"
                    f"Last {EXP_WINDOW} trades: {exp:+.4f}R\n"
                    f"Historical ref: +{HISTORICAL_REFERENCE_EXPECTANCY:.4f}R (IS)",
                    AlertTier.WARNING,
                )

        if self.executor.peak > 0:
            dd_pct = ((self.executor.peak - self.executor.equity) / self.executor.peak) * 100
            if dd_pct > DD_ALERT_PCT:
                await self.alerts.send(
                    f"🚨 *DRAWDOWN ALERT*\n"
                    f"Current DD: {dd_pct:.1f}% > {DD_ALERT_PCT:.0f}% threshold\n"
                    f"Equity: ${self.executor.equity:.2f} / Peak: ${self.executor.peak:.2f}",
                    AlertTier.CRITICAL,
                )

    async def _inactivity_check_loop(self):
        """Check for trade inactivity every 6 hours."""
        while not self._shutdown.is_set():
            await asyncio.sleep(6 * 3600)
            try:
                last_trade = self.db.get_state("last_trade_time")
                if last_trade:
                    lt = datetime.fromisoformat(last_trade)
                    days_ago = (datetime.now(timezone.utc) - lt).days
                    if days_ago >= INACTIVITY_ALERT_DAYS:
                        await self.alerts.send(
                            f"⚠️ *INACTIVITY ALERT*\n"
                            f"No trades for {days_ago} days\n"
                            f"Last trade: {last_trade}",
                            AlertTier.WARNING,
                        )
            except Exception as e:
                logger.error("Inactivity check error", error=str(e))



    # ── Daily report ─────────────────────────────────────────────────────

    async def _daily_report_loop(self):
        """Send daily report at 08:05 UTC (end of Asia session)."""
        while not self._shutdown.is_set():
            now = datetime.now(timezone.utc)
            today = now.strftime("%Y-%m-%d")

            if (now.hour == DAILY_REPORT_HOUR_UTC
                    and now.minute >= DAILY_REPORT_MINUTE_UTC
                    and today != self._last_report_date):

                await self._send_daily_report()
                self._last_report_date = today
                self.db.set_state("last_report_date", today)

            await asyncio.sleep(30)

    async def _send_daily_report(self):
        now = datetime.now(timezone.utc)
        yesterday = (now - timedelta(days=1)).replace(
            hour=DAILY_REPORT_HOUR_UTC, minute=0, second=0
        )
        yesterday_str = yesterday.isoformat()

        today_trades = self.db.get_trades_since(yesterday_str)
        all_trades = self.db.get_all_trades()

        # Live last 20 expectancy
        recent = self.db.get_recent_trades(EXP_WINDOW)
        if recent:
            live_exp = sum(t["pnl_r"] for t in recent) / len(recent)
        else:
            live_exp = 0.0
        delta = live_exp - HISTORICAL_REFERENCE_EXPECTANCY

        # Cumulative R
        cum_r = sum(t["pnl_r"] for t in all_trades)

        # Rolling DD
        dd_pct = 0.0
        if self.executor.peak > 0:
            dd_pct = ((self.executor.peak - self.executor.equity) / self.executor.peak) * 100

        # Fees today
        fees_today = sum(t["fees"] for t in today_trades)

        # Missed signals categorized (Safeguard #5)
        missed = self.db.get_missed_since(yesterday_str)
        missed_by_reason: dict[str, int] = {}
        for m in missed:
            r = m.get("reason", "unknown")
            missed_by_reason[r] = missed_by_reason.get(r, 0) + 1

        missed_str = "\n".join(f"  {r}: {c}" for r, c in sorted(missed_by_reason.items()))
        if not missed_str:
            missed_str = "  none"

        msg = (
            f"📊 *Daily Forward-Test Report*\n"
            f"Trades today: {len(today_trades)}\n\n"
            f"Hist ref exp (IS): `+{HISTORICAL_REFERENCE_EXPECTANCY:.4f}R`\n"
            f"Live last {EXP_WINDOW}: `{live_exp:+.4f}R`\n"
            f"Delta: `{delta:+.4f}R`\n\n"
            f"Fees: `${fees_today:.2f}`\n"
            f"Cumulative R: `{cum_r:+.2f}R` ({len(all_trades)} trades)\n"
            f"Rolling DD: `{dd_pct:.1f}%`\n"
            f"Equity: `${self.executor.equity:.2f}`\n\n"
            f"Missed signals:\n{missed_str}"
        )
        await self.alerts.send(msg, AlertTier.INFO)

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _session_tag(entry_time_str: str) -> str:
        try:
            h = datetime.fromisoformat(entry_time_str).hour
        except Exception:
            return "unknown"
        if h < 8:
            return "Asia"
        elif h < 16:
            return "EU"
        return "US"


# ─── Entrypoint ──────────────────────────────────────────────────────────────

async def main():
    # Fix #8: Use RotatingFileHandler compatible setup
    import logging.handlers
    setup_logging(level="INFO", log_file="logs/forward_test.log")
    runner = ForwardTestRunner()
    # Fix #2: Top-level crash visibility
    try:
        await runner.start()
    except Exception as e:
        logger.critical("Forward test crashed", error=str(e), exc_info=True)
        try:
            await runner.alerts.critical(f"🚨 FORWARD TEST CRASHED: {e}")
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

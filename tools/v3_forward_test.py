"""
Bitana V3 Forward Test — Liq-Cluster Multi-Symbol Paper Trading.

Standalone paper-trading runner for the frozen V3 liq-cluster signal
across 28 approved pairs. Based on forward_test.py pattern.

Data pipeline:
  - 5m OHLCV: Binance REST polling (every 15s)
  - Daily liq context: Coinalyze REST (once per UTC day)
  - BTC 5m: always polled for alignment check

Usage:
    cd /root/bitana
    python3 -u tools/v3_forward_test.py
"""
from __future__ import annotations

import asyncio
import json
import signal
import sqlite3
import sys
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.loader import load_config
from core.logging_setup import setup_logging, get_logger
from core.models import AlertTier, Candle, EngineType, Side, Signal
from data.binance_rest import BinanceRestClient
from data.rate_limiter import RateLimiterGroup
from engines.liq_cluster_engine import LiqClusterEngine
from tg_bot.alerts import TelegramAlerts

logger = get_logger("v3_forward_test")

# ═══════════════════════════════════════════════════
# Config Loading
# ═══════════════════════════════════════════════════

CONFIG_PATH = Path(__file__).parent.parent / "config" / "v3_forward_test.yaml"

def load_v3_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)

# ═══════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════

CANDLE_HISTORY_5M = 200
POLL_INTERVAL_S = 15
TAKER_BPS = 4.5
SLIP_BPS = 2.0
DB_PATH = Path("storage/v3_forward_test.db")
DAILY_REPORT_HOUR = 8
DAILY_REPORT_MINUTE = 5

# DB column whitelist for positions
_POS_COLS = {
    "trade_uuid", "symbol", "side", "entry_price", "quantity",
    "orig_quantity", "leverage", "stop_price", "init_stop",
    "tp1_hit", "trail_active", "candles_held", "entry_time",
    "rpnl", "fees", "btc_aligned", "confirmations", "mae", "mfe",
}

# ═══════════════════════════════════════════════════
# Database
# ═══════════════════════════════════════════════════

class V3Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
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
                btc_aligned INTEGER,
                confirmations TEXT,
                mae REAL,
                mfe REAL,
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
                btc_aligned INTEGER DEFAULT 0,
                confirmations TEXT DEFAULT '',
                mae REAL DEFAULT 0,
                mfe REAL DEFAULT 0
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
        """)
        self.conn.commit()

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

    def get_trades_since(self, since):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM trades WHERE entry_time >= ? ORDER BY id", (since,)
        ).fetchall()]

    def save_equity_snapshot(self, equity, n_open, unrealized_r):
        self.conn.execute(
            "INSERT INTO equity_snapshots(timestamp,equity,open_positions,unrealized_r) VALUES(?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), equity, n_open, unrealized_r),
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

class V3ForwardTest:
    def __init__(self):
        self.app_cfg = load_config()
        self.v3_cfg = load_v3_config()
        self.db = V3Database(DB_PATH)
        self.engine = LiqClusterEngine()
        self.alerts = TelegramAlerts(
            self.app_cfg.secrets.telegram_bot_token,
            self.app_cfg.secrets.telegram_chat_id,
        )
        self.rl = RateLimiterGroup()
        self.rest = BinanceRestClient(testnet=False, rate_limiter=self.rl)

        # Symbols
        self.symbols = self.v3_cfg["symbols"]["tier_a"] + self.v3_cfg["symbols"]["tier_b"]
        self.btc_sym = "BTCUSDT"
        if self.btc_sym not in self.symbols:
            self.all_symbols = [self.btc_sym] + self.symbols
        else:
            self.all_symbols = self.symbols

        # Risk config
        risk_cfg = self.v3_cfg["risk"]
        self.base_risk_pct = risk_cfg["base_pct"]
        self.btc_aligned_pct = risk_cfg["btc_aligned_pct"]
        self.max_leverage = risk_cfg["max_leverage"]
        self.max_positions = risk_cfg["max_positions"]
        self.max_per_symbol = risk_cfg["max_per_symbol"]

        # Coinalyze
        self.ca_api_key = self.v3_cfg["coinalyze"]["api_key"]

        # State
        saved_eq = self.db.get_state("equity")
        eq = float(saved_eq) if saved_eq else self.v3_cfg.get("initial_equity", 10000.0)
        self.executor = PaperFill(eq)
        self.executor.peak = float(self.db.get_state("peak_equity", str(eq)))

        self.candle_buffers: dict[str, list[Candle]] = defaultdict(list)
        self.last_prices: dict[str, float] = {}
        self.open_positions: list[dict] = self.db.get_open_positions()
        self._running = False
        self._shutdown = asyncio.Event()
        self._last_report_date = self.db.get_state("last_report_date", "")
        self._last_liq_date = self.db.get_state("last_liq_date", "")

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

        # Load liq context
        await self._update_liq_context()

        # Recover positions
        if self.open_positions:
            logger.info("Recovered positions", count=len(self.open_positions))

        # Startup self-test
        self._self_test()

        n_cascade = sum(1 for s in self.symbols if self.engine._get_state(s).cascade_active)
        await self.alerts.send(
            f"🧪 *V3 Liq-Cluster Started*\n"
            f"Symbols: {len(self.symbols)}\n"
            f"Cascades active: {n_cascade}\n"
            f"Equity: ${self.executor.equity:.2f}\n"
            f"Open positions: {len(self.open_positions)}",
            AlertTier.INFO,
        )

        self.db.set_state("last_startup", datetime.now(timezone.utc).isoformat())

        try:
            results = await asyncio.gather(
                self._candle_loop(),
                self._daily_report_loop(),
                self._liq_refresh_loop(),
                return_exceptions=True,
            )
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    names = ["candle_loop", "daily_report", "liq_refresh"]
                    name = names[i] if i < len(names) else f"task_{i}"
                    logger.critical("Task died", task=name, error=str(r))
                    await self.alerts.critical(f"V3 task crashed: {name}: {r}")
        finally:
            await self._cleanup()

    def _self_test(self):
        checks = []
        for sym in self.symbols[:3]:
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
        logger.info("Self-test passed", symbols=len(self.symbols),
                     total_candles=sum(len(v) for v in self.candle_buffers.values()))

    async def _cleanup(self):
        logger.info("Shutting down...")
        self.db.set_state("equity", str(round(self.executor.equity, 4)))
        self.db.set_state("peak_equity", str(round(self.executor.peak, 4)))
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
        start = end - timedelta(hours=18)  # ~200 candles

        for sym in self.all_symbols:
            candles = await self._fetch_klines(sym, "5m", start, end)
            self.candle_buffers[sym] = candles
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

    # ── Liquidation Context ──────────────────────────────────────

    async def _update_liq_context(self):
        logger.info("Fetching Coinalyze liq data...")
        now = int(time.time())
        fr = now - 120 * 86400  # 120 days

        for sym in self.symbols:
            ca_sym = f"{sym}_PERP.A"
            try:
                resp = requests.get(
                    "https://api.coinalyze.net/v1/liquidation-history",
                    params={
                        "symbols": ca_sym,
                        "interval": "daily",
                        "from": fr,
                        "to": now,
                        "api_key": self.ca_api_key,
                    },
                    timeout=20,
                )
                if resp.status_code != 200:
                    logger.warning("Coinalyze error", symbol=sym, status=resp.status_code)
                    time.sleep(2)
                    continue

                data = resp.json()
                if not isinstance(data, list) or not data:
                    continue

                history = data[0].get("history", [])
                if not history:
                    continue

                # Also need daily closes for ret_5d
                daily_closes = await self._get_daily_closes(sym)

                daily_rows = []
                for h in history:
                    dt_str = datetime.fromtimestamp(h["t"], tz=timezone.utc).strftime("%Y-%m-%d")
                    daily_rows.append({
                        "date": dt_str,
                        "total_liq": h.get("l", 0) + h.get("s", 0),
                        "long_liq": h.get("l", 0),
                        "short_liq": h.get("s", 0),
                        "close": daily_closes.get(dt_str, 0),
                    })

                self.engine.update_daily_liq(sym, daily_rows)

            except Exception as e:
                logger.error("Liq fetch error", symbol=sym, error=str(e))

            time.sleep(1.5)  # rate limit

        self._last_liq_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.db.set_state("last_liq_date", self._last_liq_date)

        n_active = sum(1 for s in self.symbols if self.engine._get_state(s).cascade_active)
        logger.info(f"Liq context updated: {n_active}/{len(self.symbols)} cascades active")

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

    async def _liq_refresh_loop(self):
        while not self._shutdown.is_set():
            await asyncio.sleep(3600)  # check hourly
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if today != self._last_liq_date:
                logger.info("Daily liq refresh triggered")
                await self._update_liq_context()

    # ── Main Candle Loop ─────────────────────────────────────────

    async def _candle_loop(self):
        last_processed: dict[str, str] = {}
        for sym in self.all_symbols:
            lp = self.db.get_state(f"last_5m_{sym}", "")
            if lp:
                last_processed[sym] = lp

        logger.info("Candle loop started", symbols=len(self.all_symbols))

        while not self._shutdown.is_set():
            try:
                for sym in self.all_symbols:
                    raw = await self.rest.get_klines(symbol=sym, interval="5m", limit=3)
                    if not raw:
                        continue

                    for k in raw:
                        close_time = datetime.fromtimestamp(k[6] / 1000, tz=timezone.utc)
                        now = datetime.now(timezone.utc)
                        if close_time > now:
                            continue

                        close_key = close_time.isoformat()
                        if close_key == last_processed.get(sym):
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

                        # Update buffer
                        self.candle_buffers[sym].append(candle)
                        if len(self.candle_buffers[sym]) > CANDLE_HISTORY_5M:
                            self.candle_buffers[sym] = self.candle_buffers[sym][-CANDLE_HISTORY_5M:]
                        self.last_prices[sym] = candle.close

                        # Process only our trading symbols
                        if sym in self.symbols:
                            await self._on_5m_close(sym, candle)

                        last_processed[sym] = close_key
                        self.db.set_state(f"last_5m_{sym}", close_key)

                    await asyncio.sleep(0.02)

                self.db.set_state("heartbeat", datetime.now(timezone.utc).isoformat())

            except Exception as e:
                logger.error("Candle loop error", error=str(e), exc_info=True)

            await asyncio.sleep(POLL_INTERVAL_S)

    async def _on_5m_close(self, symbol: str, candle: Candle):
        # 1. Manage open positions for this symbol
        await self._manage_positions(symbol, candle)

        # 2. Check for new entry
        await self._check_entry(symbol, candle)

    # ── Position Management ──────────────────────────────────────

    async def _manage_positions(self, symbol: str, candle: Candle):
        sym_positions = [p for p in self.open_positions if p["symbol"] == symbol]
        if not sym_positions:
            return

        candles_5m = self.candle_buffers.get(symbol, [])

        for p in sym_positions:
            p["candles_held"] += 1
            side = Side(p["side"])

            # Ask engine for exit decision (engine owns MAE/MFE and trailing stops)
            try:
                result = self.engine.manage_position(symbol, candle, candles_5m)
            except Exception as e:
                logger.error("manage_position error", symbol=symbol, error=str(e), exc_info=True)
                self.db.save_position(p)
                continue

            if result and result["action"] == "close":
                # Read MAE/MFE from engine exit payload (single source of truth)
                p["mae"] = result.get("mae", p.get("mae", 0))
                p["mfe"] = result.get("mfe", p.get("mfe", 0))
                await self._close_position(p, result.get("exit_price", candle.close),
                                           result["reason"], candle.close_time)
                continue

            if result and result["action"] == "partial" and not p["tp1_hit"]:
                # True partial: reduce qty, book PnL, keep position open
                frac = result["fraction"]
                tq = p["quantity"] * frac
                fill, fee, pnl = self.executor.fill_exit(
                    p["entry_price"], candle.close, tq, side)
                p["tp1_hit"] = 1
                p["quantity"] -= tq
                p["rpnl"] += pnl
                p["fees"] += fee
                p["trail_active"] = 1
                # Read MAE/MFE from engine payload
                p["mae"] = result.get("mae", p.get("mae", 0))
                p["mfe"] = result.get("mfe", p.get("mfe", 0))

                r_at = result.get("r", 0)
                await self.alerts.send(
                    f"💰 *V3 PARTIAL* {symbol}\n"
                    f"50% off at {r_at:.1f}R\n"
                    f"PnL: ${pnl:+.2f}",
                    AlertTier.INFO,
                )

            # Persist
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

        trade = {
            "trade_uuid": p["trade_uuid"],
            "symbol": p["symbol"],
            "side": p["side"],
            "entry_time": p["entry_time"],
            "exit_time": ct.isoformat() if isinstance(ct, datetime) else str(ct),
            "entry_price": round(p["entry_price"], 6),
            "exit_price": round(fill, 6),
            "quantity": round(p["orig_quantity"], 6),
            "leverage": p["leverage"],
            "stop_dist": round(sd, 6),
            "pnl_usd": round(net_pnl, 4),
            "pnl_r": round(pnl_r, 4),
            "fees": round(p["fees"], 4),
            "hold_candles": p["candles_held"],
            "exit_reason": reason,
            "tp1_hit": p["tp1_hit"],
            "equity_after": round(self.executor.equity, 2),
            "btc_aligned": p.get("btc_aligned", 0),
            "confirmations": p.get("confirmations", ""),
            "mae": round(p.get("mae", 0), 4),
            "mfe": round(p.get("mfe", 0), 4),
            "duplicate_key": "",
        }

        self.db.save_trade(trade)
        self.db.remove_position(p["trade_uuid"])
        self.engine.reset_symbol(p["symbol"])

        # Persist equity
        self.db.set_state("equity", str(round(self.executor.equity, 4)))
        self.db.set_state("peak_equity", str(round(self.executor.peak, 4)))
        self.db.save_equity_snapshot(
            self.executor.equity, len(self.open_positions),
            self._calc_unrealized_r(),
        )

        emoji = "✅" if pnl_r >= 0 else "❌"
        aligned = "🟢" if p.get("btc_aligned") else "⚪"
        await self.alerts.send(
            f"{emoji} *V3 EXIT* {p['side']} {p['symbol']}\n"
            f"Entry: `{p['entry_price']:.6f}` → Exit: `{fill:.6f}`\n"
            f"PnL: `${net_pnl:+.2f}` ({pnl_r:+.3f}R)\n"
            f"Reason: {reason} | Candles: {p['candles_held']}\n"
            f"MAE: {p.get('mae', 0):.2f}R | MFE: {p.get('mfe', 0):.2f}R\n"
            f"BTC: {aligned} | Eq: ${self.executor.equity:.2f}",
            AlertTier.INFO,
        )

        logger.info("Position closed", symbol=p["symbol"], pnl_r=round(pnl_r, 4),
                     reason=reason, equity=round(self.executor.equity, 2))

    # ── Entry Logic ──────────────────────────────────────────────

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

        # Duplicate check
        dup_key = f"{symbol}_{candle.close_time.isoformat()}_{Side.LONG.value}"
        if self.db.has_dup(dup_key):
            return

        # Evaluate signal (isolated per-symbol exception handling)
        candles_5m = self.candle_buffers.get(symbol, [])
        try:
            sig = self.engine.evaluate(symbol, candles_5m)
        except Exception as e:
            logger.error("evaluate() error", symbol=symbol, error=str(e), exc_info=True)
            return

        if sig is None:
            return

        self.db.mark_dup(dup_key)

        # BTC alignment
        btc_candles = self.candle_buffers.get(self.btc_sym, [])
        btc_aligned = self.engine.get_btc_aligned(btc_candles)

        # Signal detected alert
        confirms = sig.signal_data.get("confirmations", {})
        active_confirms = [k for k, v in confirms.items() if v]
        await self.alerts.send(
            f"🔍 *V3 SIGNAL* {symbol}\n"
            f"Confirms: {sig.signal_data.get('confirm_count', 0)}/6 "
            f"({', '.join(active_confirms)})\n"
            f"Vol Z: {sig.signal_data.get('vol_z', 0)}\n"
            f"Cascade: {sig.signal_data.get('cascade_strength', 0):.2f}\n"
            f"BTC: {'🟢 aligned' if btc_aligned else '⚪ neutral'}",
            AlertTier.INFO,
        )

        # Risk sizing
        risk_pct = self.btc_aligned_pct if btc_aligned else self.base_risk_pct
        sd = sig.risk_distance
        if sd <= 0:
            return

        ra = self.executor.equity * (risk_pct / 100.0)
        qty = ra / sd
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
            "btc_aligned": 1 if btc_aligned else 0,
            "confirmations": json.dumps({k: str(v) for k, v in sig.signal_data.get("confirmations", {}).items()}),
            "mae": 0.0,
            "mfe": 0.0,
        }

        self.open_positions.append(pos)
        self.db.save_position(pos)

        aligned_tag = "🟢" if btc_aligned else "⚪"
        await self.alerts.send(
            f"📈 *V3 ENTRY* LONG {symbol}\n"
            f"Price: `{fill:.6f}` | Stop: `{sig.stop_price:.6f}`\n"
            f"Risk: {risk_pct:.1f}% | Lev: {lev}x | Qty: {qty:.4f}\n"
            f"BTC: {aligned_tag} | Positions: {len(self.open_positions)}/{self.max_positions}",
            AlertTier.INFO,
        )

        logger.info("Entry", symbol=symbol, price=round(fill, 6),
                     risk=risk_pct, btc_aligned=btc_aligned)

    # ── Daily Report ─────────────────────────────────────────────

    async def _daily_report_loop(self):
        while not self._shutdown.is_set():
            await asyncio.sleep(60)
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

        # Today's stats
        n = len(trades)
        wins = sum(1 for t in trades if t["pnl_r"] > 0)
        wr = (wins / n * 100) if n > 0 else 0
        total_r = sum(t["pnl_r"] for t in trades)

        # BTC aligned breakdown
        aligned = [t for t in trades if t.get("btc_aligned")]
        non_aligned = [t for t in trades if not t.get("btc_aligned")]
        a_r = sum(t["pnl_r"] for t in aligned)
        na_r = sum(t["pnl_r"] for t in non_aligned)

        # Unrealized
        unrealized_r = self._calc_unrealized_r()

        # Per-symbol
        sym_stats = defaultdict(lambda: {"n": 0, "r": 0})
        for t in trades:
            sym_stats[t["symbol"]]["n"] += 1
            sym_stats[t["symbol"]]["r"] += t["pnl_r"]

        # Drawdown
        dd = 0
        if self.executor.peak > 0:
            dd = ((self.executor.peak - self.executor.equity) / self.executor.peak) * 100

        # All-time
        all_n = len(all_trades)
        all_r = sum(t["pnl_r"] for t in all_trades)
        all_wins = sum(1 for t in all_trades if t["pnl_r"] > 0)
        all_wr = (all_wins / all_n * 100) if all_n > 0 else 0

        # Cascade status
        n_cascade = sum(1 for s in self.symbols if self.engine._get_state(s).cascade_active)

        report = (
            f"📊 *V3 DAILY REPORT*\n\n"
            f"*Today:* {n} trades | WR: {wr:.0f}% | R: {total_r:+.2f}\n"
            f"  BTC🟢: {len(aligned)}t {a_r:+.2f}R | ⚪: {len(non_aligned)}t {na_r:+.2f}R\n\n"
            f"*Open:* {len(self.open_positions)} positions\n"
            f"  Unrealized: {unrealized_r:+.2f}R\n\n"
            f"*All-time:* {all_n} trades | WR: {all_wr:.0f}% | R: {all_r:+.2f}\n"
            f"*Equity:* ${self.executor.equity:.2f} (DD: {dd:.1f}%)\n"
            f"*Cascades:* {n_cascade}/{len(self.symbols)} active\n"
        )

        # Per-symbol breakdown (top 5)
        if sym_stats:
            sorted_syms = sorted(sym_stats.items(), key=lambda x: abs(x[1]["r"]), reverse=True)[:5]
            report += "\n*Per-symbol (top 5):*\n"
            for sym, st in sorted_syms:
                report += f"  {sym}: {st['n']}t {st['r']:+.2f}R\n"

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


# ═══════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════

async def async_main():
    setup_logging(level="INFO", log_file="logs/v3_forward_test.log")
    runner = V3ForwardTest()
    await runner.start()

def main():
    asyncio.run(async_main())

if __name__ == "__main__":
    main()

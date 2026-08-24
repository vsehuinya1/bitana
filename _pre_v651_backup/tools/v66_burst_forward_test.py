"""v66 Liq-Burst Continuation — paper forward test (parallel to v65-revert).

Uses WS force-order accumulation for hourly buckets (live-computable, no daily look-ahead).
Thesis: short_dom burst >= 35% of trailing 24h -> LONG, 3 ATR stop, 8h hold.
"""
from __future__ import annotations

import asyncio
import json
import signal
import sqlite3
import sys
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import websockets
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.loader import load_config
from core.logging_setup import setup_logging, get_logger
from core.models import AlertTier, Candle, Side
from data.binance_rest import BinanceRestClient
from data.rate_limiter import RateLimiterGroup
from engines.liq_burst_engine import BurstConfig, LiqBurstEngine, STRATEGY_VERSION
from tg_bot.alerts import TelegramAlerts

setup_logging()
logger = get_logger("v66_burst")

CONFIG_PATH = Path(__file__).parent.parent / "config" / "v66_burst_forward_test.yaml"
DB_PATH = Path("storage/v66_burst_forward_test.db")
FORCE_ORDER_DB = Path("storage/force_orders.db")
CANDLE_HISTORY = 200
POLL_S = 15
TAKER_BPS = 4.5
SLIP_BPS = 2.0


def load_cfg() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


class V66DB:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY, trade_uuid TEXT UNIQUE, symbol TEXT,
                side TEXT, entry_time TEXT, exit_time TEXT, entry_price REAL,
                exit_price REAL, pnl_r REAL, exit_reason TEXT, burst_share REAL,
                strategy_version TEXT);
            CREATE TABLE IF NOT EXISTS open_positions (
                trade_uuid TEXT PRIMARY KEY, symbol TEXT, side TEXT,
                entry_price REAL, init_stop REAL, entry_time TEXT,
                candles_held INTEGER, burst_share REAL);
        """)
        self.conn.commit()

    def get_state(self, k: str, default: str = "") -> str:
        r = self.conn.execute("SELECT value FROM state WHERE key=?", (k,)).fetchone()
        return r[0] if r else default

    def set_state(self, k: str, v: str):
        self.conn.execute("INSERT OR REPLACE INTO state(key,value) VALUES(?,?)", (k, v))
        self.conn.commit()

    def save_trade(self, t: dict):
        self.conn.execute(
            """INSERT OR REPLACE INTO trades(trade_uuid,symbol,side,entry_time,exit_time,
               entry_price,exit_price,pnl_r,exit_reason,burst_share,strategy_version)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (t["trade_uuid"], t["symbol"], t["side"], t["entry_time"], t["exit_time"],
             t["entry_price"], t["exit_price"], t["pnl_r"], t["exit_reason"],
             t.get("burst_share", 0), STRATEGY_VERSION))
        self.conn.commit()

    def save_position(self, p: dict):
        self.conn.execute(
            """INSERT OR REPLACE INTO open_positions
               (trade_uuid,symbol,side,entry_price,init_stop,entry_time,candles_held,burst_share)
               VALUES(?,?,?,?,?,?,?,?)""",
            (p["trade_uuid"], p["symbol"], p["side"], p["entry_price"], p["init_stop"],
             p["entry_time"], p["candles_held"], p.get("burst_share", 0)))
        self.conn.commit()

    def remove_position(self, uid: str):
        self.conn.execute("DELETE FROM open_positions WHERE trade_uuid=?", (uid,))
        self.conn.commit()

    def get_positions(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM open_positions").fetchall()]

    def sum_r(self) -> float:
        r = self.conn.execute("SELECT COALESCE(SUM(pnl_r),0) FROM trades").fetchone()
        return float(r[0])

    def close(self):
        self.conn.close()


class PaperFill:
    def __init__(self, eq: float):
        self.equity = eq
        self.peak = eq

    def entry(self, price: float, qty: float):
        slip = price * SLIP_BPS / 10000
        fill = price + slip
        fee = qty * fill * TAKER_BPS / 10000
        self.equity -= fee
        return fill

    def exit(self, entry: float, price: float, qty: float):
        slip = price * SLIP_BPS / 10000
        fill = price - slip
        fee = qty * fill * TAKER_BPS / 10000
        pnl = (fill - entry) * qty
        self.equity += pnl - fee
        self.peak = max(self.peak, self.equity)
        return fill


def seed_hourly_from_force_orders(engine: LiqBurstEngine, symbols: list[str]):
    if not FORCE_ORDER_DB.exists():
        return
    conn = sqlite3.connect(FORCE_ORDER_DB)
    since_ms = int((datetime.now(timezone.utc) - timedelta(hours=48)).timestamp() * 1000)
    for sym in symbols:
        rows = conn.execute(
            "SELECT event_time_ms, side, volume_usd FROM force_order_events "
            "WHERE symbol=? AND event_time_ms>=? ORDER BY event_time_ms",
            (sym, since_ms)).fetchall()
        buckets: dict[int, list[float]] = {}
        for t_ms, side, vol in rows:
            h = (t_ms // 1000 // 3600) * 3600
            b = buckets.setdefault(h, [0.0, 0.0])
            if side == "SELL":
                b[0] += vol
            elif side == "BUY":
                b[1] += vol
        if buckets:
            engine.seed_hourly(sym, [(h, ll, sl) for h, (ll, sl) in sorted(buckets.items())])
    conn.close()


class V66BurstRunner:
    def __init__(self):
        self.cfg = load_cfg()
        self.app = load_config()
        self.db = V66DB(DB_PATH)
        b = self.cfg["burst"]
        self.engine = LiqBurstEngine(BurstConfig(
            share_min=float(b["share_min"]),
            dir_dom=float(b["dir_dom"]),
            min_trail_usd=float(b["min_trail_usd"]),
            stop_atr=float(b["stop_atr"]),
            hold_bars=int(b["hold_bars"]),
            dedup_hours=int(b["dedup_hours"]),
        ))
        self.symbols = self.cfg["symbols"]["tier_a"]
        self.max_positions = int(self.cfg["risk"]["max_positions"])
        self.max_risk_pct = float(self.cfg["risk"]["max_risk_pct"])
        eq = float(self.db.get_state("equity") or self.cfg.get("initial_equity", 10000))
        self.fill = PaperFill(eq)
        self.rest = BinanceRestClient(testnet=False, rate_limiter=RateLimiterGroup())
        self.alerts = TelegramAlerts(
            self.app.secrets.telegram_bot_token,
            self.app.secrets.telegram_chat_id,
        )
        self.buffers: dict[str, deque] = defaultdict(lambda: deque(maxlen=CANDLE_HISTORY))
        self.positions: list[dict] = self.db.get_positions()
        self._shutdown = asyncio.Event()
        self._last_eval_hour: dict[str, int] = {}

    async def start(self):
        await self.rest.start()
        await self.alerts.initialize()
        seed_hourly_from_force_orders(self.engine, self.symbols)
        await self._load_history()
        for p in self.positions:
            st = self.engine._st(p["symbol"])
            st.in_trade = True
            st.entry_price = p["entry_price"]
            st.stop_price = p["init_stop"]
            st.risk_per_unit = abs(p["entry_price"] - p["init_stop"])
            st.bars_held = p.get("candles_held", 0)

        await self.alerts.send(
            f"🌊 v66 Liq-Burst EXPERIMENTAL (paper — OOS not validated)\n"
            f"Thesis: short-liq burst >=35% trail24h -> LONG 8h\n"
            f"Symbols: {len(self.symbols)} | Equity: {self.fill.equity:.2f}\n"
            f"Open: {len(self.positions)} | CumR: {self.db.sum_r():+.2f}",
            AlertTier.INFO,
        )
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._shutdown.set)

        await asyncio.gather(
            self._candle_loop(),
            self._ws_loop(),
            self._report_loop(),
        )

    async def _load_history(self):
        for sym in self.symbols:
            kl = await self.rest.get_klines(sym, "5m", limit=CANDLE_HISTORY)
            for k in kl:
                self.buffers[sym].append(k)

    async def _candle_loop(self):
        seen: set[tuple[str, int]] = set()
        while not self._shutdown.is_set():
            for sym in self.symbols:
                try:
                    kl = await self.rest.get_klines(sym, "5m", limit=2)
                    if not kl:
                        continue
                    c = kl[-1]
                    if not c.is_closed:
                        continue
                    key = (sym, int(c.close_time.timestamp()))
                    if key in seen:
                        continue
                    seen.add(key)
                    self.buffers[sym].append(c)
                    await self._on_candle(sym, c)
                except Exception as e:
                    logger.error("candle error", symbol=sym, error=str(e))
            await asyncio.sleep(POLL_S)

    async def _on_candle(self, sym: str, candle: Candle):
        buf = list(self.buffers[sym])
        for p in [x for x in self.positions if x["symbol"] == sym]:
            res = self.engine.manage_position(sym, buf)
            p["candles_held"] = self.engine._st(sym).bars_held
            if res and res.get("action") == "close":
                await self._close(p, res["exit_price"], res["reason"], candle.close_time)
            else:
                self.db.save_position(p)

        # Evaluate burst at hour boundary (bar closes on the hour)
        if candle.close_time.minute != 0:
            return
        closed_hour = int(candle.close_time.timestamp()) // 3600 * 3600
        if self._last_eval_hour.get(sym) == closed_hour:
            return
        self._last_eval_hour[sym] = closed_hour

        if len([p for p in self.positions if p["symbol"] == sym]) >= 1:
            return
        if len(self.positions) >= self.max_positions:
            return

        sig = self.engine.evaluate(sym, buf, closed_hour=closed_hour)
        if sig is None:
            return

        sd = abs(sig.entry_price - sig.stop_price)
        if sd <= 0:
            return
        risk_usd = self.fill.equity * self.max_risk_pct
        qty = risk_usd / sd
        fill = self.fill.entry(sig.entry_price, qty)
        self.engine.on_entry(
            sym, fill, sig.stop_price, candle.close_time.hour,
            sig.signal_data.get("burst_share", 0), "short_dom",
        )
        pos = {
            "trade_uuid": str(uuid.uuid4()),
            "symbol": sym,
            "side": "LONG",
            "entry_price": round(fill, 6),
            "init_stop": round(sig.stop_price, 6),
            "entry_time": candle.close_time.isoformat(),
            "candles_held": 0,
            "burst_share": sig.signal_data.get("burst_share", 0),
        }
        self.positions.append(pos)
        self.db.save_position(pos)
        await self.alerts.send(
            f"🌊 v66 ENTRY LONG {sym}\n"
            f"Share: {sig.signal_data.get('burst_share', 0):.1%} of trail24h\n"
            f"Entry: {fill:.6f} | Stop: {sig.stop_price:.6f}\n"
            f"Open: {len(self.positions)} | CumR: {self.db.sum_r():+.2f}",
            AlertTier.INFO,
        )

    async def _close(self, p, price, reason, ct):
        sd = abs(p["entry_price"] - p["init_stop"])
        qty = (self.fill.equity * self.max_risk_pct) / sd if sd > 0 else 0
        fill = self.fill.exit(p["entry_price"], price, qty)
        pnl_r = (fill - p["entry_price"]) / sd if sd > 0 else 0.0
        trade = {
            "trade_uuid": p["trade_uuid"], "symbol": p["symbol"], "side": p["side"],
            "entry_time": p["entry_time"],
            "exit_time": ct.isoformat() if isinstance(ct, datetime) else str(ct),
            "entry_price": p["entry_price"], "exit_price": round(fill, 6),
            "pnl_r": round(pnl_r, 4), "exit_reason": reason,
            "burst_share": p.get("burst_share", 0),
        }
        self.db.save_trade(trade)
        self.db.remove_position(p["trade_uuid"])
        self.positions = [x for x in self.positions if x["trade_uuid"] != p["trade_uuid"]]
        self.db.set_state("equity", str(round(self.fill.equity, 4)))
        emoji = "✅" if pnl_r >= 0 else "❌"
        await self.alerts.send(
            f"{emoji} v66 EXIT {p['symbol']} R: {pnl_r:+.3f} ({reason})\n"
            f"CumR: {self.db.sum_r():+.2f} | Equity: {self.fill.equity:.2f}",
            AlertTier.INFO,
        )

    async def _ws_loop(self):
        url = "wss://fstream.binance.com/market/ws/!forceOrder@arr"
        sym_set = set(self.symbols)
        while not self._shutdown.is_set():
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    logger.info("WS connected")
                    async for raw in ws:
                        if self._shutdown.is_set():
                            break
                        msg = json.loads(raw)
                        o = msg.get("o", {})
                        sym = o.get("s", "")
                        if sym not in sym_set:
                            continue
                        side = o.get("S", "")
                        qty = float(o.get("q", 0))
                        price = float(o.get("p", 0))
                        vol = qty * price
                        t_ms = int(o.get("T", 0))
                        if vol > 0:
                            self.engine.add_liq_usd(sym, t_ms, side, vol)
            except Exception as e:
                logger.warning("WS reconnect", error=str(e))
                await asyncio.sleep(5)

    async def _report_loop(self):
        tg = self.cfg.get("telegram", {})
        rh = int(tg.get("session_report_hour_utc", 22))
        rm = int(tg.get("session_report_minute_utc", 5))
        last = self.db.get_state("last_report_date", "")
        while not self._shutdown.is_set():
            await asyncio.sleep(30)
            now = datetime.now(timezone.utc)
            today = now.strftime("%Y-%m-%d")
            if today == last or now.hour != rh or now.minute < rm:
                continue
            n = self.conn_count_trades_today(today)
            await self.alerts.send(
                f"🌊 v66 DAILY — {today}\n"
                f"Trades: {n} | CumR: {self.db.sum_r():+.2f}\n"
                f"Equity: {self.fill.equity:.2f} | Open: {len(self.positions)}",
                AlertTier.INFO,
            )
            self.db.set_state("last_report_date", today)
            last = today

    def conn_count_trades_today(self, day: str) -> int:
        r = self.db.conn.execute(
            "SELECT COUNT(*) FROM trades WHERE entry_time LIKE ?", (day + "%",)).fetchone()
        return int(r[0])


async def main():
    runner = V66BurstRunner()
    try:
        await runner.start()
    finally:
        runner.db.set_state("equity", str(round(runner.fill.equity, 4)))
        runner.db.close()


if __name__ == "__main__":
    asyncio.run(main())

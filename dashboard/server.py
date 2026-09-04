"""
Bitana Trading Dashboard Server

Standalone aiohttp web server that reads the bot's SQLite database
in read-only mode and serves a mobile-friendly live dashboard.

IMPORTANT: Uses .env.dashboard for its own config — NEVER the bot's
.env (which is guarded by pydantic extra=forbid).

Usage:
    python dashboard/server.py
    python dashboard/server.py --db data/bitana-live-burst.db --port 8080

.env.dashboard:
    DASHBOARD_TOKEN=your_secret_token
    DASHBOARD_PORT=8080          (optional, default 8080)
    DASHBOARD_DB=data/bitana-live-burst.db  (optional)
    BOT_HEALTH_URL=http://localhost:8082     (optional)
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from aiohttp import web

# Load dashboard-specific env BEFORE anything reads os.getenv.
# Intentionally NOT loading .env — that belongs to the bot.
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env.dashboard")


# ---------------------------------------------------------------------------
# Configuration defaults
# ---------------------------------------------------------------------------

DEFAULT_DB = "data/bitana-live-burst.db"
DEFAULT_PORT = 8080
DEFAULT_BOT_URL = "http://localhost:8082"

TEMPLATE_DIR = Path(__file__).parent / "templates"


# ---------------------------------------------------------------------------
# Read-only database reader
# ---------------------------------------------------------------------------

class DashboardDB:
    """Read-only SQLite reader for the bot's database."""

    def __init__(self, db_path: str) -> None:
        self._path = db_path
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        if not Path(self._path).exists():
            raise FileNotFoundError(f"Database not found: {self._path}")
        self._conn = sqlite3.connect(
            f"file:{self._path}?mode=ro",
            uri=True,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def _q(self, sql: str, params: tuple = ()) -> list[dict]:
        if not self._conn:
            return []
        try:
            cur = self._conn.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
        except sqlite3.OperationalError:
            return []

    def _q1(self, sql: str, params: tuple = ()) -> dict | None:
        rows = self._q(sql, params)
        return rows[0] if rows else None

    # --- Queries --------------------------------------------------------

    def risk_state(self) -> dict:
        return self._q1("SELECT * FROM risk_state WHERE id = 1") or {}

    def brake_state(self) -> dict:
        return self._q1("SELECT * FROM brake_state WHERE id = 1") or {}

    def open_positions(self) -> list[dict]:
        return self._q(
            """SELECT * FROM positions
               WHERE state NOT IN ('CLOSED', 'CANCELLED')
               ORDER BY created_at DESC"""
        )

    def recent_trades(self, limit: int = 50) -> list[dict]:
        return self._q(
            "SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )

    def transfers(self, limit: int = 30) -> list[dict]:
        return self._q(
            "SELECT * FROM wallet_transfers ORDER BY event_time_ms DESC LIMIT ?",
            (limit,),
        )

    def trade_stats(self) -> dict:
        """Compute performance statistics from the trades table."""
        trades = self._q("SELECT pnl_usd, pnl_r FROM trades")
        if not trades:
            return {
                "total_trades": 0, "wins": 0, "losses": 0,
                "win_rate": 0, "total_pnl": 0, "expectancy_r": 0,
                "profit_factor": 0, "best_trade": 0, "worst_trade": 0,
            }

        pnls = [t["pnl_usd"] for t in trades]
        rs = [t["pnl_r"] for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gross_win = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 0.001

        return {
            "total_trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(trades) * 100, 1),
            "total_pnl": round(sum(pnls), 2),
            "expectancy_r": round(sum(rs) / len(rs), 3),
            "profit_factor": round(gross_win / gross_loss, 2),
            "best_trade": round(max(pnls), 2),
            "worst_trade": round(min(pnls), 2),
        }

    def pnl_curve(self) -> list[dict]:
        """Cumulative PnL data points for the chart."""
        trades = self._q(
            "SELECT timestamp, pnl_usd FROM trades ORDER BY timestamp ASC"
        )
        cum = 0.0
        points = [{"t": "", "v": 0}]
        for t in trades:
            cum += t["pnl_usd"]
            points.append({"t": t["timestamp"], "v": round(cum, 2)})
        return points

    def system_state(self) -> dict:
        rows = self._q("SELECT key, value FROM system_state")
        return {r["key"]: r["value"] for r in rows}


# ---------------------------------------------------------------------------
# Web application
# ---------------------------------------------------------------------------

def create_app(db: DashboardDB, token: str, bot_url: str) -> web.Application:
    app = web.Application(middlewares=[_auth_middleware(token)])
    app["db"] = db
    app["bot_url"] = bot_url
    app["start_time"] = time.time()

    app.router.add_get("/", _handle_index)
    app.router.add_get("/api/dashboard", _handle_dashboard)
    app.on_shutdown.append(_on_shutdown)
    return app


def _auth_middleware(token: str):
    @web.middleware
    async def middleware(request: web.Request, handler):
        if not token:
            return await handler(request)
        req_token = request.query.get("token", "")
        if not req_token:
            auth = request.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                req_token = auth[7:]
        if req_token != token:
            return web.json_response({"error": "unauthorized"}, status=403)
        return await handler(request)
    return middleware


async def _handle_index(request: web.Request) -> web.Response:
    html_path = TEMPLATE_DIR / "index.html"
    if not html_path.exists():
        return web.Response(text="Dashboard template not found", status=500)
    return web.FileResponse(html_path, headers={"Content-Type": "text/html"})


async def _handle_dashboard(request: web.Request) -> web.Response:
    db: DashboardDB = request.app["db"]
    bot_url: str = request.app["bot_url"]
    start: float = request.app["start_time"]

    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, lambda: {
        "risk": db.risk_state(),
        "brakes": db.brake_state(),
        "positions": db.open_positions(),
        "trades": db.recent_trades(50),
        "transfers": db.transfers(20),
        "stats": db.trade_stats(),
        "pnl_curve": db.pnl_curve(),
        "system": db.system_state(),
    })

    data["bot"] = await _proxy_bot_health(bot_url)
    data["dashboard_uptime_s"] = round(time.time() - start, 1)
    data["timestamp"] = datetime.now(timezone.utc).isoformat()

    return web.json_response(data)


async def _proxy_bot_health(bot_url: str) -> dict:
    """Proxy the bot's health and metrics endpoints."""
    result: dict = {"online": False, "status": "offline"}
    # 2026-09-04: attach the dashboard token so the proxy keeps working if
    # the bot's health server ever enforces auth (harmless while open).
    headers = {}
    tok = os.getenv("DASHBOARD_TOKEN", "")
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    try:
        timeout = aiohttp.ClientTimeout(total=3)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{bot_url}/health", headers=headers) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    result["online"] = True
            # Also grab /metrics for regime + session + arms + oi_gate data
            async with session.get(f"{bot_url}/metrics", headers=headers) as resp:
                if resp.status == 200:
                    metrics = await resp.json()
                    result["metrics"] = metrics
    except Exception:
        pass
    return result


async def _on_shutdown(app: web.Application) -> None:
    app["db"].close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Bitana Trading Dashboard")
    parser.add_argument(
        "--db",
        default=os.getenv("DASHBOARD_DB", DEFAULT_DB),
        help="Path to bot's SQLite database",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("DASHBOARD_PORT", str(DEFAULT_PORT))),
        help="Port to listen on",
    )
    parser.add_argument(
        "--bot-url",
        default=os.getenv("BOT_HEALTH_URL", DEFAULT_BOT_URL),
        help="Bot health endpoint URL",
    )
    args = parser.parse_args()

    token = os.getenv("DASHBOARD_TOKEN", "")
    if not token:
        print("\033[33m⚠  WARNING: DASHBOARD_TOKEN not set — dashboard is unprotected!\033[0m")

    db = DashboardDB(args.db)
    try:
        db.connect()
        print(f"📊 Dashboard connected to {args.db}")
    except FileNotFoundError as e:
        print(f"\033[31m✗ {e}\033[0m")
        print("  The bot must have run at least once to create the database.")
        return

    app = create_app(db, token, args.bot_url)

    url = f"http://0.0.0.0:{args.port}"
    if token:
        url += f"/?token={token}"
    print(f"🚀 Dashboard: {url}")

    web.run_app(app, host="0.0.0.0", port=args.port, print=None)


if __name__ == "__main__":
    main()

"""SQLite state for the wick/discount engine: one row per leg (bid -> open -> closed) and an append-only event log.
Its own file (data/wd_engine.db by default); nothing here touches the live bot's databases."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

LEG_COLS = ["strategy", "symbol", "state", "qty", "atr", "r_usd", "bid", "ref", "stop", "entry", "entry_time", "exit",
            "exit_time", "why", "pnl_usd", "R", "bid_cid", "bid_kind", "tp_cid", "stop_cid", "hour", "expires", "signal",
            "created", "updated", "mode", "rung", "parent", "addon", "addon_at"]
NUM = ("atr", "r_usd", "bid", "ref", "stop", "entry", "exit", "pnl_usd", "R", "rung", "parent")
FINAL = ("CLOSED", "CANCELLED", "ERROR")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(f"""CREATE TABLE IF NOT EXISTS legs (id INTEGER PRIMARY KEY AUTOINCREMENT,
            {', '.join(c + ' ' + ('REAL' if c in NUM else 'TEXT') for c in LEG_COLS)})""")
        have = {r[1] for r in self.db.execute("PRAGMA table_info(legs)")}
        for c in LEG_COLS:                                   # v2 columns on a v1 file
            if c not in have:
                self.db.execute(f"ALTER TABLE legs ADD COLUMN {c} {'REAL' if c in NUM else 'TEXT'}")
        self.db.execute("""CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, t TEXT, leg_id INTEGER,
            kind TEXT, msg TEXT)""")
        self.db.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")

    def new_leg(self, **kw) -> int:
        kw.setdefault("created", now_iso())
        kw["updated"] = kw["created"]
        cols = [c for c in LEG_COLS if c in kw]
        cur = self.db.execute(f"INSERT INTO legs ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                              [json.dumps(kw[c]) if c == "signal" and not isinstance(kw[c], str) else kw[c] for c in cols])
        return int(cur.lastrowid)

    def update(self, leg_id: int, **kw) -> None:
        kw["updated"] = now_iso()
        self.db.execute(f"UPDATE legs SET {', '.join(c + '=?' for c in kw)} WHERE id=?", [*kw.values(), leg_id])

    def leg(self, leg_id: int) -> dict:
        r = self.db.execute("SELECT * FROM legs WHERE id=?", (leg_id,)).fetchone()
        return dict(r) if r else {}

    def live_legs(self) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            f"SELECT * FROM legs WHERE state NOT IN ({','.join('?' * len(FINAL))}) ORDER BY id", FINAL)]

    def closed_since(self, iso: str) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM legs WHERE state='CLOSED' AND exit_time>=?", (iso,))]

    def last_stop_out(self, strategy: str, symbol: str) -> str | None:
        r = self.db.execute("SELECT MAX(exit_time) FROM legs WHERE strategy=? AND symbol=? AND why='stop'",
                            (strategy, symbol)).fetchone()
        return r[0] if r else None

    def event(self, leg_id: int | None, kind: str, msg: str) -> None:
        self.db.execute("INSERT INTO events (t, leg_id, kind, msg) VALUES (?,?,?,?)", (now_iso(), leg_id, kind, msg))

    def get(self, k: str) -> str | None:
        r = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return r[0] if r else None

    def put(self, k: str, v: str) -> None:
        self.db.execute("INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))

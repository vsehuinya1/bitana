"""Candidate-specific cap-3 acceptance for parallel shadow strategies."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from research.signal_shadow import SignalShadow, ShadowPortfolioConfig


def _open_row(
    conn: sqlite3.Connection,
    *,
    strategy: str,
    symbol: str,
    side: str = "LONG",
    session: str = "ny",
    cluster_bucket: str = "2026-07-23T14:00:00+00:00",
    would_live_accept: int = 1,
) -> None:
    now = datetime.now(timezone.utc).timestamp()
    conn.execute(
        """
        INSERT INTO shadow_trades (
            strategy, symbol, side, entry_time, entry_price, stop_price, tp_price, atr,
            status, created_at, session, cluster_bucket, would_live_accept
        ) VALUES (?, ?, ?, ?, 100.0, 90.0, 130.0, 1.0, 'open', ?, ?, ?, ?)
        """,
        (
            strategy, symbol, side,
            "2026-07-23T14:04:59.999000+00:00",
            now, session, cluster_bucket, would_live_accept,
        ),
    )


def test_would_live_accept_is_strategy_scoped(tmp_path):
    db_path = tmp_path / "shadow.db"
    shadow = SignalShadow(str(db_path), portfolio=ShadowPortfolioConfig())
    conn = shadow.conn

    # Fill the live-style book for strategy A only.
    _open_row(conn, strategy="ny_flush_buy_4h", symbol="ETHUSDT")
    _open_row(conn, strategy="ny_flush_buy_4h", symbol="SOLUSDT")
    _open_row(conn, strategy="ny_flush_buy_4h", symbol="BTCUSDT")
    # Parallel variant open positions must not consume A's slots.
    _open_row(conn, strategy="asia_pump_short_4h", symbol="XRPUSDT")
    _open_row(conn, strategy="asia_pump_short_4h", symbol="ADAUSDT")
    _open_row(conn, strategy="asia_pump_short_4h", symbol="NEARUSDT")
    conn.commit()

    assert shadow._would_live_accept(
        "ny_flush_buy_4h", "ZECUSDT", "LONG", "ny", "2026-07-23T14:00:00+00:00",
    ) == 0
    assert shadow._would_live_accept(
        "follow_3h_all", "ZECUSDT", "LONG", "ny", "2026-07-23T14:00:00+00:00",
    ) == 1


def test_rejected_rows_do_not_consume_capacity(tmp_path):
    db_path = tmp_path / "shadow.db"
    shadow = SignalShadow(str(db_path), portfolio=ShadowPortfolioConfig())
    conn = shadow.conn

    _open_row(conn, strategy="ny_flush_buy_4h", symbol="ETHUSDT", would_live_accept=1)
    _open_row(conn, strategy="ny_flush_buy_4h", symbol="SOLUSDT", would_live_accept=0)
    _open_row(conn, strategy="ny_flush_buy_4h", symbol="BTCUSDT", would_live_accept=0)
    _open_row(conn, strategy="ny_flush_buy_4h", symbol="XRPUSDT", would_live_accept=0)
    conn.commit()

    assert shadow._would_live_accept(
        "ny_flush_buy_4h", "ZECUSDT", "LONG", "ny", "2026-07-23T14:00:00+00:00",
    ) == 1

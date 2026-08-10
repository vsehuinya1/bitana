"""Futures wallet transfer noting helpers."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from storage.database import Database
from tg_bot.alerts import TelegramAlerts


def test_save_wallet_transfer_dedupes():
    async def _run():
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(str(Path(tmp) / "t.db"))
            await db.initialize()
            kwargs = dict(
                tran_id="abc",
                asset="USDT",
                amount=-47.4,
                direction="out",
                income_type="TRANSFER",
                event_time_ms=1_700_000_000_000,
                info="spot<-futures",
                equity_after=15.22,
            )
            assert await db.save_wallet_transfer(**kwargs) is True
            assert await db.save_wallet_transfer(**kwargs) is False
            rows = await db._read("SELECT * FROM wallet_transfers")
            assert len(rows) == 1
            assert rows[0]["direction"] == "out"
            await db.close()

    asyncio.run(_run())


def test_futures_transfer_alert_message():
    alerts = TelegramAlerts("", "")
    sent: list[str] = []

    async def fake_send(msg, tier=None):
        sent.append(msg)
        return True

    alerts.send = fake_send  # type: ignore[method-assign]
    asyncio.run(
        alerts.futures_transfer_alert(
            direction="in", amount=53.77, asset="USDT", equity_after=70.94,
        )
    )
    assert "FUTURES TRANSFER IN" in sent[0]
    assert "53.7700" in sent[0]

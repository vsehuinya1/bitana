"""
Telegram Alerts — Tiered notification system.

Info: entries, exits, TP, stop updates
Warning: reconnects, skipped trades, partial fills
Critical: position mismatch, risk brake hit, shutdown, external positions
"""
from __future__ import annotations

import asyncio
from enum import Enum
from typing import Optional

from core.logging_setup import get_logger
from core.models import AlertTier

logger = get_logger("telegram_alerts")


class TelegramAlerts:
    """Sends tiered Telegram alerts."""

    MAX_MESSAGE_LENGTH = 4000

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._token = bot_token
        self._chat_id = chat_id
        self._enabled = bool(bot_token and chat_id)
        self._bot = None

    async def initialize(self) -> bool:
        if not self._enabled:
            logger.warning("Telegram alerts disabled — no token/chat_id")
            return False
        try:
            from telegram import Bot
            self._bot = Bot(token=self._token)
            logger.info("Telegram alerts initialized")
            return True
        except Exception as e:
            logger.error("Failed to init Telegram", error=str(e))
            self._enabled = False
            return False

    async def verify(self) -> bool:
        """Verify chat access and actual message delivery."""
        if not self._enabled or not self._bot:
            return False
        try:
            await self._bot.get_chat(chat_id=self._chat_id)
        except Exception as e:
            logger.error("Telegram delivery preflight failed", error=str(e))
            return False
        delivered = await self.info(
            "<b>Live alert preflight passed</b>\n"
            "Telegram delivery is operational."
        )
        if delivered:
            logger.info("Telegram delivery preflight passed")
        return delivered

    async def send(
        self,
        message: str,
        tier: AlertTier = AlertTier.INFO,
    ) -> bool:
        if not self._enabled or not self._bot:
            return False

        prefix = {
            AlertTier.INFO: "ℹ️",
            AlertTier.WARNING: "⚠️",
            AlertTier.CRITICAL: "🚨",
        }.get(tier, "")

        text = f"{prefix} <b>{tier.value}</b>\n{message}"

        if len(text) > self.MAX_MESSAGE_LENGTH:
            text = text[: self.MAX_MESSAGE_LENGTH - 3] + "..."

        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                await self._bot.send_message(
                    chat_id=self._chat_id,
                    text=text,
                    parse_mode="HTML",
                )
                return True
            except Exception as e:
                last_exc = e
                logger.warning(
                    "Telegram send attempt failed",
                    attempt=attempt + 1,
                    error=str(e),
                    tier=tier.value,
                )
                if attempt < 1:
                    await asyncio.sleep(2)

        logger.error("Telegram send failed after retries", error=str(last_exc), tier=tier.value)
        return False

    # Convenience methods
    async def info(self, msg: str) -> bool:
        return await self.send(msg, AlertTier.INFO)

    async def warning(self, msg: str) -> bool:
        return await self.send(msg, AlertTier.WARNING)

    async def critical(self, msg: str) -> bool:
        return await self.send(msg, AlertTier.CRITICAL)

    async def entry_alert(
        self, symbol: str, side: str, price: float, qty: float,
        engine: str, trade_uuid: str,
    ) -> None:
        await self.info(
            f"📈 <b>ENTRY</b> {side} {symbol}\n"
            f"Price: <code>{price:.4f}</code>\n"
            f"Qty: <code>{qty:.4f}</code>\n"
            f"Engine: {engine}\n"
            f"UUID: <code>{trade_uuid[:8]}</code>"
        )

    async def exit_alert(
        self, symbol: str, side: str, price: float,
        pnl_usd: float, pnl_r: float, reason: str,
    ) -> None:
        emoji = "✅" if pnl_usd >= 0 else "❌"
        await self.info(
            f"{emoji} <b>EXIT</b> {side} {symbol}\n"
            f"Price: <code>{price:.4f}</code>\n"
            f"PnL: <code>${pnl_usd:+.2f}</code> ({pnl_r:+.2f}R)\n"
            f"Reason: {reason}"
        )

    async def brake_alert(self, brake_type: str, details: str) -> None:
        await self.critical(
            f"🛑 <b>BRAKE: {brake_type}</b>\n{details}"
        )

    async def startup_alert(self, mode: str, config_checksum: str) -> None:
        await self.info(
            f"🚀 <b>Bitana Started</b>\n"
            f"Mode: {mode}\n"
            f"Config: <code>{config_checksum[:12]}...</code>"
        )

    async def futures_transfer_alert(
        self,
        *,
        direction: str,
        amount: float,
        asset: str,
        equity_after: float | None = None,
        info: str = "",
    ) -> None:
        """Notify spot ↔ USDT-M futures wallet transfer."""
        if direction == "in":
            title = "FUTURES TRANSFER IN"
            emoji = "📥"
        else:
            title = "FUTURES TRANSFER OUT"
            emoji = "📤"
        lines = [
            f"{emoji} <b>{title}</b>",
            f"Amount: <code>{amount:+.4f} {asset}</code>",
        ]
        if equity_after is not None:
            lines.append(f"Futures wallet after: <code>${equity_after:.2f}</code>")
        if info:
            lines.append(f"Info: <code>{info}</code>")
        await self.warning("\n".join(lines))


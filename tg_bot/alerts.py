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

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._token = bot_token
        self._chat_id = chat_id
        self._enabled = bool(bot_token and chat_id)
        self._bot = None

    async def initialize(self) -> None:
        if not self._enabled:
            logger.warning("Telegram alerts disabled — no token/chat_id")
            return
        try:
            from telegram import Bot
            self._bot = Bot(token=self._token)
            logger.info("Telegram alerts initialized")
        except Exception as e:
            logger.error("Failed to init Telegram", error=str(e))
            self._enabled = False

    async def send(
        self,
        message: str,
        tier: AlertTier = AlertTier.INFO,
    ) -> None:
        if not self._enabled or not self._bot:
            return

        prefix = {
            AlertTier.INFO: "ℹ️",
            AlertTier.WARNING: "⚠️",
            AlertTier.CRITICAL: "🚨",
        }.get(tier, "")

        text = f"{prefix} [{tier.value}]\n{message}"

        async def _do_send():
            try:
                await asyncio.wait_for(
                    self._bot.send_message(
                        chat_id=self._chat_id,
                        text=text,
                        parse_mode="HTML",
                    ),
                    timeout=10,
                )
            except asyncio.TimeoutError:
                logger.warning("Telegram send timed out", tier=tier.value)
            except Exception as e:
                # Fallback: retry without parse_mode
                try:
                    await asyncio.wait_for(
                        self._bot.send_message(
                            chat_id=self._chat_id,
                            text=text,
                        ),
                        timeout=10,
                    )
                except Exception as e2:
                    logger.error("Telegram send failed", error=str(e2), tier=tier.value)

        # Fire-and-forget: never block the caller
        asyncio.ensure_future(_do_send())

    # Convenience methods
    async def info(self, msg: str) -> None:
        await self.send(msg, AlertTier.INFO)

    async def warning(self, msg: str) -> None:
        await self.send(msg, AlertTier.WARNING)

    async def critical(self, msg: str) -> None:
        await self.send(msg, AlertTier.CRITICAL)

    async def entry_alert(
        self, symbol: str, side: str, price: float, qty: float,
        engine: str, trade_uuid: str,
    ) -> None:
        await self.info(
            f"📈 ENTRY {side} {symbol}\n"
            f"Price: {price:.4f}\n"
            f"Qty: {qty:.4f}\n"
            f"Engine: {engine}\n"
            f"UUID: {trade_uuid[:8]}"
        )

    async def exit_alert(
        self, symbol: str, side: str, price: float,
        pnl_usd: float, pnl_r: float, reason: str,
    ) -> None:
        emoji = "✅" if pnl_usd >= 0 else "❌"
        await self.info(
            f"{emoji} EXIT {side} {symbol}\n"
            f"Price: {price:.4f}\n"
            f"PnL: {pnl_usd:+.2f} ({pnl_r:+.2f}R)\n"
            f"Reason: {reason}"
        )

    async def brake_alert(self, brake_type: str, details: str) -> None:
        await self.critical(
            f"🛑 BRAKE: {brake_type}\n{details}"
        )

    async def startup_alert(self, mode: str, config_checksum: str) -> None:
        await self.info(
            f"🚀 Bitana Started\n"
            f"Mode: {mode}\n"
            f"Config: {config_checksum[:12]}..."
        )

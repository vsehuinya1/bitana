"""
Telegram Bot — Command handler.

Commands: /status, /positions, /stats, /pause, /resume,
          /shutdown, /risk, /logs, /flatten (AD-7)
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

from core.logging_setup import get_logger

logger = get_logger("telegram_bot")


class TelegramBotHandler:
    """Handles Telegram bot commands."""

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        flatten_confirm_timeout_s: int = 30,
    ) -> None:
        self._token = bot_token
        self._chat_id = chat_id
        self._flatten_timeout = flatten_confirm_timeout_s
        self._enabled = bool(bot_token and chat_id)
        self._application = None
        self._state_getter = None
        self._flatten_callback = None
        self._pause_callback = None
        self._resume_callback = None
        self._shutdown_callback = None
        self._pending_flatten = False

    def set_callbacks(
        self,
        state_getter=None,
        flatten_callback=None,
        pause_callback=None,
        resume_callback=None,
        shutdown_callback=None,
    ) -> None:
        self._state_getter = state_getter
        self._flatten_callback = flatten_callback
        self._pause_callback = pause_callback
        self._resume_callback = resume_callback
        self._shutdown_callback = shutdown_callback

    async def start(self) -> None:
        if not self._enabled:
            logger.warning("Telegram bot disabled — no token/chat_id")
            return

        try:
            from telegram import Update
            from telegram.ext import (
                ApplicationBuilder, CommandHandler, MessageHandler,
                ContextTypes, filters,
            )

            self._application = (
                ApplicationBuilder().token(self._token).build()
            )

            # Register command handlers
            handlers = [
                ("status", self._cmd_status),
                ("positions", self._cmd_positions),
                ("stats", self._cmd_stats),
                ("pause", self._cmd_pause),
                ("resume", self._cmd_resume),
                ("shutdown", self._cmd_shutdown),
                ("risk", self._cmd_risk),
                ("logs", self._cmd_logs),
                ("flatten", self._cmd_flatten),
            ]
            for name, handler in handlers:
                self._application.add_handler(CommandHandler(name, handler))

            # Text handler for /flatten confirmation
            self._application.add_handler(
                MessageHandler(filters.TEXT & ~filters.COMMAND, self._text_handler)
            )

            await self._application.initialize()
            await self._application.start()
            if self._application.updater:
                await self._application.updater.start_polling(drop_pending_updates=True)
            logger.info("Telegram bot started")

        except Exception as e:
            logger.error("Failed to start Telegram bot", error=str(e))
            self._enabled = False

    async def stop(self) -> None:
        if self._application:
            try:
                if self._application.updater and self._application.updater.running:
                    await self._application.updater.stop()
                await self._application.stop()
                await self._application.shutdown()
            except Exception:
                pass

    def _check_auth(self, chat_id: int) -> bool:
        return str(chat_id) == str(self._chat_id)

    async def _cmd_status(self, update, context) -> None:
        if not self._check_auth(update.effective_chat.id):
            return
        state = self._state_getter() if self._state_getter else {}
        msg = (
            f"📊 *Status*\n"
            f"Mode: {state.get('mode', '?')}\n"
            f"Equity: ${state.get('equity', 0):.2f}\n"
            f"DD: {state.get('drawdown', 0):.1%}\n"
            f"Positions: {state.get('open_positions', 0)}\n"
            f"Paused: {state.get('paused', False)}\n"
            f"Tasks: {state.get('task_health', 'unknown')}\n"
            f"Uptime: {state.get('uptime', '?')}"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    async def _cmd_positions(self, update, context) -> None:
        if not self._check_auth(update.effective_chat.id):
            return
        state = self._state_getter() if self._state_getter else {}
        positions = state.get("positions_detail", [])
        if not positions:
            await update.message.reply_text("No open positions.")
            return
        lines = ["📈 *Positions*"]
        for p in positions:
            lines.append(
                f"\n{p.get('side', '?')} {p.get('symbol', '?')}\n"
                f"Entry: `{p.get('entry', 0):.4f}`\n"
                f"Qty: `{p.get('qty', 0):.4f}`\n"
                f"Stop: `{p.get('stop', 0):.4f}`\n"
                f"R: {p.get('current_r', 0):+.2f}\n"
                f"Candles: {p.get('candles', 0)}"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def _cmd_stats(self, update, context) -> None:
        if not self._check_auth(update.effective_chat.id):
            return
        state = self._state_getter() if self._state_getter else {}
        stats = state.get("stats", {})
        msg = (
            f"📉 *Stats*\n"
            f"Trades: {stats.get('total_trades', 0)}\n"
            f"Win Rate: {stats.get('win_rate', 0):.1%}\n"
            f"Expectancy: {stats.get('expectancy_r', 0):+.2f}R\n"
            f"Profit Factor: {stats.get('profit_factor', 0):.2f}\n"
            f"Total PnL: ${stats.get('total_pnl', 0):+.2f}\n"
            f"Best: ${stats.get('best_trade', 0):+.2f}\n"
            f"Worst: ${stats.get('worst_trade', 0):+.2f}"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    async def _cmd_pause(self, update, context) -> None:
        if not self._check_auth(update.effective_chat.id):
            return
        if self._pause_callback:
            self._pause_callback()
        await update.message.reply_text("⏸️ Trading paused.")

    async def _cmd_resume(self, update, context) -> None:
        if not self._check_auth(update.effective_chat.id):
            return
        if self._resume_callback:
            self._resume_callback()
        await update.message.reply_text("▶️ Trading resumed.")

    async def _cmd_shutdown(self, update, context) -> None:
        if not self._check_auth(update.effective_chat.id):
            return
        if self._shutdown_callback:
            await update.message.reply_text("🔴 Shutting down...")
            self._shutdown_callback()

    async def _cmd_risk(self, update, context) -> None:
        if not self._check_auth(update.effective_chat.id):
            return
        state = self._state_getter() if self._state_getter else {}
        risk = state.get("risk_state", {})
        msg = (
            f"🎯 *Risk*\n"
            f"Active Risk: {risk.get('risk_pct', 0):.2f}%\n"
            f"Peak Equity: ${risk.get('peak_equity', 0):.2f}\n"
            f"DD: {risk.get('drawdown', 0):.1%}\n"
            f"Consec. Losses: {risk.get('consecutive_losses', 0)}\n"
            f"Reduced Trades Left: {risk.get('reduced_trades', 0)}"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    async def _cmd_logs(self, update, context) -> None:
        if not self._check_auth(update.effective_chat.id):
            return
        state = self._state_getter() if self._state_getter else {}
        logs = state.get("recent_logs", "No logs available.")
        await update.message.reply_text(
            f"📋 *Recent Logs*\n```\n{logs}\n```",
            parse_mode="Markdown",
        )

    async def _cmd_flatten(self, update, context) -> None:
        """AD-7: /flatten = emergency full exit."""
        if not self._check_auth(update.effective_chat.id):
            return
        self._pending_flatten = True
        await update.message.reply_text(
            "⚠️ *FLATTEN ALL POSITIONS*\n\n"
            "This will:\n"
            "• Cancel all open orders\n"
            "• Close all positions at market\n"
            "• Pause trading\n\n"
            f"Type `CONFIRM` within {self._flatten_timeout}s to proceed.",
            parse_mode="Markdown",
        )
        # Auto-expire after timeout
        await asyncio.sleep(self._flatten_timeout)
        self._pending_flatten = False

    async def _text_handler(self, update, context) -> None:
        if not self._check_auth(update.effective_chat.id):
            return
        text = update.message.text.strip()
        if text == "CONFIRM" and self._pending_flatten:
            self._pending_flatten = False
            if self._flatten_callback:
                await update.message.reply_text("🔴 Flattening all positions...")
                await self._flatten_callback()
                await update.message.reply_text(
                    "✅ All positions closed, orders cancelled, trading paused.\n"
                    "Use /resume to restart."
                )

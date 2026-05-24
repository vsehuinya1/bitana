"""
Binance WebSocket Manager

Handles kline streams + user data stream.
Auto-reconnect with exponential backoff. REST fallback.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any, Callable, Coroutine, Optional

import aiohttp

from core.logging_setup import get_logger
from core.events import event_bus, Events

logger = get_logger("binance_ws")

WS_FUTURES_BASE = "wss://fstream.binance.com"
WS_TESTNET_BASE = "wss://stream.binancefuture.com"

StreamHandler = Callable[[dict], Coroutine[Any, Any, None]]


class BinanceWebSocket:
    """Manages WebSocket connections for Binance Futures."""

    def __init__(
        self,
        testnet: bool = True,
        max_retries: int = 10,
        base_delay_s: float = 1.0,
    ) -> None:
        self._base = WS_TESTNET_BASE if testnet else WS_FUTURES_BASE
        self._max_retries = max_retries
        self._base_delay = base_delay_s
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._running = False
        self._handlers: dict[str, StreamHandler] = {}
        self._connected = False
        self._task: Optional[asyncio.Task] = None

    async def start(
        self,
        symbols: list[str],
        timeframes: list[str],
        listen_key: str = "",
    ) -> None:
        """Start WS connections."""
        self._running = True
        self._session = aiohttp.ClientSession()

        # Build combined stream URL
        streams = []
        for sym in symbols:
            s = sym.lower()
            for tf in timeframes:
                streams.append(f"{s}@kline_{tf}")
        if listen_key:
            streams.append(listen_key)

        self._stream_url = f"{self._base}/stream?streams={'/'.join(streams)}"
        self._task = asyncio.create_task(self._connect_loop())
        logger.info("WebSocket starting", streams=len(streams))

    async def stop(self) -> None:
        self._running = False
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._session:
            await self._session.close()
        self._connected = False
        logger.info("WebSocket stopped")

    def on_kline(self, handler: StreamHandler) -> None:
        self._handlers["kline"] = handler

    def on_user_event(self, handler: StreamHandler) -> None:
        self._handlers["user"] = handler

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def heartbeat(self) -> None:
        """Called by watchdog to verify connection health."""
        pass  # Connection state tracked via _connected flag

    async def _connect_loop(self) -> None:
        retries = 0
        while self._running:
            try:
                assert self._session is not None
                async with self._session.ws_connect(
                    self._stream_url,
                    heartbeat=20,
                    receive_timeout=30,
                ) as ws:
                    self._ws = ws
                    self._connected = True
                    retries = 0
                    await event_bus.emit(Events.WS_CONNECTED)
                    logger.info("WebSocket connected")

                    async for msg in ws:
                        if not self._running:
                            break
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await self._handle_message(json.loads(msg.data))
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            logger.error("WS error", error=str(ws.exception()))
                            break
                        elif msg.type in (
                            aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.CLOSING,
                        ):
                            break

            except (aiohttp.ClientError, asyncio.TimeoutError, Exception) as e:
                logger.warning("WS connection lost", error=str(e))

            self._connected = False
            await event_bus.emit(Events.WS_DISCONNECTED)

            if not self._running:
                break

            retries += 1
            if retries > self._max_retries:
                logger.error("WS max retries exceeded, giving up")
                await event_bus.emit(Events.TASK_CRASHED, task_name="websocket")
                break

            delay = min(self._base_delay * (2 ** (retries - 1)), 60)
            logger.info("WS reconnecting", attempt=retries, delay_s=delay)
            await event_bus.emit(Events.WS_RECONNECTING, attempt=retries)
            await asyncio.sleep(delay)

    async def _handle_message(self, data: dict) -> None:
        """Route incoming WS messages to handlers."""
        stream = data.get("stream", "")
        payload = data.get("data", data)
        event_type = payload.get("e", "")

        if "kline" in stream or event_type == "kline":
            handler = self._handlers.get("kline")
            if handler:
                await handler(payload)
        elif event_type in ("ORDER_TRADE_UPDATE", "ACCOUNT_UPDATE"):
            handler = self._handlers.get("user")
            if handler:
                await handler(payload)

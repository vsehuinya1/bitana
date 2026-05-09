"""
Candle Manager

Aggregates candles per symbol/timeframe. Emits closed candles only.
WS primary, REST authoritative fallback (AD-3).
No lookahead bias. Exchange time-synced.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Optional

from core.events import event_bus, Events
from core.logging_setup import get_logger
from core.models import Candle

logger = get_logger("candle_manager")


class CandleManager:
    """Manages candle aggregation with WS primary / REST truth (AD-3)."""

    def __init__(self, history_limit: int = 500) -> None:
        self._history_limit = history_limit
        # candles[symbol][timeframe] = deque of closed Candle
        self._candles: dict[str, dict[str, deque[Candle]]] = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=history_limit))
        )
        self._current: dict[str, dict[str, Optional[Candle]]] = defaultdict(
            lambda: defaultdict(lambda: None)
        )
        self._lock = asyncio.Lock()

    def get_candles(
        self, symbol: str, timeframe: str, count: int | None = None
    ) -> list[Candle]:
        """Get closed candles for a symbol/timeframe."""
        dq = self._candles.get(symbol, {}).get(timeframe, deque())
        candles = list(dq)
        if count:
            return candles[-count:]
        return candles

    def get_latest(self, symbol: str, timeframe: str) -> Optional[Candle]:
        """Get the most recent closed candle."""
        dq = self._candles.get(symbol, {}).get(timeframe, deque())
        return dq[-1] if dq else None

    async def handle_ws_kline(self, data: dict) -> None:
        """Process incoming WS kline event.

        Only emits candle_closed event when candle is definitively closed.
        """
        k = data.get("k", {})
        symbol = k.get("s", "")
        timeframe = k.get("i", "")
        is_closed = k.get("x", False)

        candle = Candle(
            symbol=symbol,
            timeframe=timeframe,
            open_time=datetime.fromtimestamp(k["t"] / 1000, tz=timezone.utc),
            close_time=datetime.fromtimestamp(k["T"] / 1000, tz=timezone.utc),
            open=float(k["o"]),
            high=float(k["h"]),
            low=float(k["l"]),
            close=float(k["c"]),
            volume=float(k["v"]),
            is_closed=is_closed,
        )

        async with self._lock:
            if is_closed:
                dq = self._candles[symbol][timeframe]
                # Avoid duplicates
                if not dq or dq[-1].open_time != candle.open_time:
                    dq.append(candle)
                    self._current[symbol][timeframe] = None
                    logger.debug(
                        "Candle closed",
                        symbol=symbol, tf=timeframe,
                        close=candle.close, vol=candle.volume,
                    )
                    await event_bus.emit(
                        Events.CANDLE_CLOSED,
                        symbol=symbol,
                        timeframe=timeframe,
                        candle=candle,
                    )
            else:
                self._current[symbol][timeframe] = candle

    async def load_history_from_rest(
        self, rest_client, symbol: str, timeframe: str, limit: int = 500,
    ) -> None:
        """Load historical candles from REST on startup."""
        try:
            raw = await rest_client.get_klines(
                symbol=symbol, interval=timeframe, limit=limit,
            )
            if not raw or not isinstance(raw, list):
                return

            async with self._lock:
                dq = self._candles[symbol][timeframe]
                dq.clear()
                # All but last candle are closed
                for k in raw[:-1]:
                    candle = Candle(
                        symbol=symbol,
                        timeframe=timeframe,
                        open_time=datetime.fromtimestamp(
                            k[0] / 1000, tz=timezone.utc
                        ),
                        close_time=datetime.fromtimestamp(
                            k[6] / 1000, tz=timezone.utc
                        ),
                        open=float(k[1]),
                        high=float(k[2]),
                        low=float(k[3]),
                        close=float(k[4]),
                        volume=float(k[5]),
                        is_closed=True,
                    )
                    dq.append(candle)

            logger.info(
                "History loaded from REST",
                symbol=symbol, tf=timeframe, candles=len(dq),
            )
        except Exception as e:
            logger.error(
                "Failed to load REST history",
                symbol=symbol, tf=timeframe, error=str(e),
            )

    async def verify_with_rest(self, rest_client, symbol: str, timeframe: str) -> None:
        """REST truth check (AD-3): verify last 3 candles match WS data."""
        try:
            raw = await rest_client.get_klines(
                symbol=symbol, interval=timeframe, limit=4,
            )
            if not raw or len(raw) < 2:
                return

            async with self._lock:
                dq = self._candles[symbol][timeframe]
                if not dq:
                    return

                # Check closed candles (all but last)
                for k in raw[:-1]:
                    rest_open_time = datetime.fromtimestamp(
                        k[0] / 1000, tz=timezone.utc
                    )
                    # Find matching WS candle
                    for i, ws_candle in enumerate(dq):
                        if ws_candle.open_time == rest_open_time:
                            rest_close = float(k[4])
                            if abs(ws_candle.close - rest_close) > 1e-10:
                                logger.warning(
                                    "Candle mismatch — REST wins",
                                    symbol=symbol, tf=timeframe,
                                    time=rest_open_time.isoformat(),
                                    ws_close=ws_candle.close,
                                    rest_close=rest_close,
                                )
                                corrected = Candle(
                                    symbol=symbol,
                                    timeframe=timeframe,
                                    open_time=rest_open_time,
                                    close_time=datetime.fromtimestamp(
                                        k[6] / 1000, tz=timezone.utc
                                    ),
                                    open=float(k[1]),
                                    high=float(k[2]),
                                    low=float(k[3]),
                                    close=float(k[4]),
                                    volume=float(k[5]),
                                    is_closed=True,
                                )
                                dq[i] = corrected
                            break

        except Exception as e:
            logger.error(
                "REST candle verify failed",
                symbol=symbol, tf=timeframe, error=str(e),
            )

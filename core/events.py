"""
Bitana Async Event Bus

Lightweight decoupled pub/sub for system events.
Handlers are async callables registered by event name.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

# Type alias for async event handlers
EventHandler = Callable[..., Coroutine[Any, Any, None]]


class EventBus:
    """Simple async event bus for decoupled module communication."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._lock = asyncio.Lock()

    def subscribe(self, event: str, handler: EventHandler) -> None:
        """Register a handler for an event type."""
        self._handlers[event].append(handler)
        logger.debug("Subscribed %s to event '%s'", handler.__qualname__, event)

    def unsubscribe(self, event: str, handler: EventHandler) -> None:
        """Remove a handler from an event type."""
        handlers = self._handlers.get(event, [])
        if handler in handlers:
            handlers.remove(handler)

    async def emit(self, event: str, **kwargs: Any) -> None:
        """Emit an event, calling all registered handlers.

        Handlers are called concurrently. Exceptions in individual handlers
        are logged but do not prevent other handlers from executing.
        """
        handlers = self._handlers.get(event, [])
        if not handlers:
            return

        tasks = []
        for handler in handlers:
            tasks.append(self._safe_call(event, handler, **kwargs))

        if tasks:
            await asyncio.gather(*tasks)

    async def _safe_call(
        self, event: str, handler: EventHandler, **kwargs: Any
    ) -> None:
        """Call a handler with error isolation."""
        try:
            await handler(**kwargs)
        except Exception:
            logger.exception(
                "Error in event handler %s for event '%s'",
                handler.__qualname__,
                event,
            )


# ---------------------------------------------------------------------------
# Event name constants
# ---------------------------------------------------------------------------

class Events:
    """Canonical event names used across the system."""
    SIGNAL_GENERATED = "signal_generated"
    ORDER_PLACED = "order_placed"
    ORDER_FILLED = "order_filled"
    ORDER_CANCELLED = "order_cancelled"
    POSITION_OPENED = "position_opened"
    POSITION_UPDATED = "position_updated"
    POSITION_CLOSED = "position_closed"
    STOP_UPDATED = "stop_updated"
    TP_HIT = "tp_hit"
    TRAILING_ACTIVATED = "trailing_activated"
    BRAKE_TRIGGERED = "brake_triggered"
    BRAKE_CLEARED = "brake_cleared"
    RECONCILIATION_MISMATCH = "reconciliation_mismatch"
    EXTERNAL_POSITION_DETECTED = "external_position_detected"
    WS_CONNECTED = "ws_connected"
    WS_DISCONNECTED = "ws_disconnected"
    WS_RECONNECTING = "ws_reconnecting"
    CANDLE_CLOSED = "candle_closed"
    SYSTEM_SHUTDOWN = "system_shutdown"
    SYSTEM_PAUSED = "system_paused"
    SYSTEM_RESUMED = "system_resumed"
    HEALTH_CHECK = "health_check"
    TASK_CRASHED = "task_crashed"
    TASK_RESTARTED = "task_restarted"


# Global event bus instance
event_bus = EventBus()

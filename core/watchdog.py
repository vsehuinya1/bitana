"""
Async Task Watchdog

Supervised task manager with per-task heartbeat monitoring,
auto-restart with exponential backoff, crash budgets.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Optional

from core.events import event_bus, Events
from core.logging_setup import get_logger

logger = get_logger("watchdog")

TaskFactory = Callable[[], Coroutine[Any, Any, None]]


@dataclass
class TaskInfo:
    name: str
    factory: TaskFactory
    critical: bool = True
    heartbeat_interval_s: float = 30.0
    max_restarts: int = 5
    restart_backoff_base: float = 2.0
    task: Optional[asyncio.Task] = field(default=None, repr=False)
    last_heartbeat: float = field(default_factory=time.monotonic)
    restart_count: int = 0
    is_healthy: bool = True


class Watchdog:
    """Monitors and restarts async tasks."""

    def __init__(self, heartbeat_interval_s: float = 30.0) -> None:
        self._tasks: dict[str, TaskInfo] = {}
        self._running = False
        self._monitor_task: Optional[asyncio.Task] = None
        self._heartbeat_interval = heartbeat_interval_s

    def register(
        self,
        name: str,
        factory: TaskFactory,
        critical: bool = True,
        heartbeat_interval_s: float | None = None,
    ) -> None:
        self._tasks[name] = TaskInfo(
            name=name,
            factory=factory,
            critical=critical,
            heartbeat_interval_s=heartbeat_interval_s or self._heartbeat_interval,
        )

    def heartbeat(self, task_name: str) -> None:
        info = self._tasks.get(task_name)
        if info:
            info.last_heartbeat = time.monotonic()
            info.is_healthy = True

    async def start_all(self) -> None:
        self._running = True
        for name, info in self._tasks.items():
            info.task = asyncio.create_task(
                self._run_with_supervision(info), name=f"wd_{name}"
            )
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("Watchdog started", tasks=list(self._tasks.keys()))

    async def stop_all(self) -> None:
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
        for info in self._tasks.values():
            if info.task and not info.task.done():
                info.task.cancel()
                try:
                    await info.task
                except asyncio.CancelledError:
                    pass
        logger.info("Watchdog stopped")

    def get_health_summary(self) -> dict:
        return {
            name: {
                "healthy": info.is_healthy,
                "restarts": info.restart_count,
                "critical": info.critical,
                "last_heartbeat_ago_s": round(
                    time.monotonic() - info.last_heartbeat, 1
                ),
            }
            for name, info in self._tasks.items()
        }

    async def _run_with_supervision(self, info: TaskInfo) -> None:
        while self._running:
            try:
                info.last_heartbeat = time.monotonic()
                info.is_healthy = True
                await info.factory()
            except asyncio.CancelledError:
                break
            except Exception as e:
                info.restart_count += 1
                info.is_healthy = False
                logger.error(
                    "Task crashed",
                    task=info.name, error=str(e),
                    restarts=info.restart_count,
                )
                await event_bus.emit(
                    Events.TASK_CRASHED, task_name=info.name, error=str(e),
                )

                if info.restart_count > info.max_restarts:
                    logger.critical(
                        "Task exceeded max restarts",
                        task=info.name,
                        max=info.max_restarts,
                    )
                    if info.critical:
                        await event_bus.emit(Events.SYSTEM_SHUTDOWN, reason=f"Critical task {info.name} failed")
                    break

                delay = info.restart_backoff_base ** min(info.restart_count, 6)
                logger.info("Restarting task", task=info.name, delay_s=delay)
                await event_bus.emit(Events.TASK_RESTARTED, task_name=info.name)
                await asyncio.sleep(delay)

    async def _monitor_loop(self) -> None:
        """Check heartbeats periodically."""
        while self._running:
            await asyncio.sleep(self._heartbeat_interval)
            for name, info in self._tasks.items():
                if not info.is_healthy:
                    continue
                elapsed = time.monotonic() - info.last_heartbeat
                if elapsed > info.heartbeat_interval_s * 3:
                    info.is_healthy = False
                    logger.warning(
                        "Task heartbeat stale",
                        task=name,
                        elapsed_s=round(elapsed, 1),
                    )

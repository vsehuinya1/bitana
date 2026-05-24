"""
Health Metrics Endpoint

Minimal aiohttp server for /health and /metrics.
Used by systemd health check and external monitoring.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Optional

from aiohttp import web

from core.logging_setup import get_logger

logger = get_logger("health")


class HealthServer:
    """Minimal HTTP health/metrics server."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8080,
    ) -> None:
        self._host = host
        self._port = port
        self._app = web.Application()
        self._app.router.add_get("/health", self._health_handler)
        self._app.router.add_get("/metrics", self._metrics_handler)
        self._runner: Optional[web.AppRunner] = None
        self._start_time = time.time()
        self._metrics_getter: Optional[Any] = None  # callable
        self._mode = "unknown"

    def set_mode(self, mode: str) -> None:
        self._mode = mode

    def set_metrics_getter(self, getter) -> None:
        """Set callable that returns current metrics dict."""
        self._metrics_getter = getter

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        logger.info("Health server started", host=self._host, port=self._port)

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
        logger.info("Health server stopped")

    async def _health_handler(self, request: web.Request) -> web.Response:
        return web.json_response({
            "status": "ok",
            "uptime_s": round(time.time() - self._start_time, 1),
            "mode": self._mode,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    async def _metrics_handler(self, request: web.Request) -> web.Response:
        metrics = {}
        if self._metrics_getter:
            try:
                metrics = self._metrics_getter()
            except Exception as e:
                metrics = {"error": str(e)}

        metrics["uptime_s"] = round(time.time() - self._start_time, 1)
        metrics["mode"] = self._mode
        return web.json_response(metrics)

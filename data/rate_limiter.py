"""
Shared Async Rate Limiter

Token bucket algorithm shared across all REST modules.
Configured per Binance weight limits.
"""
from __future__ import annotations

import asyncio
import time

from core.logging_setup import get_logger

logger = get_logger("rate_limiter")


class RateLimiter:
    """Async token bucket rate limiter."""

    def __init__(
        self,
        max_tokens: int = 1200,
        refill_interval_s: float = 60.0,
        warn_threshold_pct: float = 0.80,
        name: str = "default",
    ) -> None:
        self._max_tokens = max_tokens
        self._tokens = float(max_tokens)
        self._refill_rate = max_tokens / refill_interval_s
        self._last_refill = time.monotonic()
        self._warn_threshold = int(max_tokens * warn_threshold_pct)
        self._name = name
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(
            self._max_tokens,
            self._tokens + elapsed * self._refill_rate,
        )
        self._last_refill = now

    async def acquire(self, weight: int = 1) -> None:
        """Acquire tokens, blocking if necessary."""
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= weight:
                    self._tokens -= weight
                    if self._tokens < self._max_tokens - self._warn_threshold:
                        logger.warning(
                            "Rate limiter approaching limit",
                            limiter=self._name,
                            remaining=round(self._tokens, 1),
                            max=self._max_tokens,
                        )
                    return
                # Calculate wait time but DON'T sleep inside the lock
                deficit = weight - self._tokens
                wait_time = deficit / self._refill_rate
            # Sleep OUTSIDE the lock
            logger.warning(
                "Rate limit near capacity, waiting",
                limiter=self._name,
                wait_s=round(wait_time, 2),
                tokens_available=round(self._tokens, 1),
            )
            await asyncio.sleep(min(wait_time + 0.1, 5.0))

    @property
    def available_tokens(self) -> float:
        self._refill()
        return self._tokens


class RateLimiterGroup:
    """Group of rate limiters for different Binance endpoints."""

    def __init__(
        self,
        order_weight_per_min: int = 1200,
        data_weight_per_min: int = 2400,
        warn_threshold_pct: float = 0.80,
    ) -> None:
        self.orders = RateLimiter(
            max_tokens=order_weight_per_min,
            refill_interval_s=60.0,
            warn_threshold_pct=warn_threshold_pct,
            name="orders",
        )
        self.data = RateLimiter(
            max_tokens=data_weight_per_min,
            refill_interval_s=60.0,
            warn_threshold_pct=warn_threshold_pct,
            name="data",
        )

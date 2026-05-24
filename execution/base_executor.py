"""
Base Executor — Unified executor interface (AD-1).

Paper and live executors implement the same ABC.
Order lifecycle code is shared.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from core.models import OrderRequest, OrderResult, Position


class BaseExecutor(ABC):
    """Unified executor interface for paper and live modes."""

    @abstractmethod
    async def place_order(self, request: OrderRequest) -> OrderResult:
        """Place an order. Returns fill result."""

    @abstractmethod
    async def cancel_order(
        self, symbol: str, client_order_id: str
    ) -> bool:
        """Cancel an order. Returns True if cancelled."""

    @abstractmethod
    async def cancel_all_orders(self, symbol: str) -> bool:
        """Cancel all open orders for a symbol."""

    @abstractmethod
    async def close_position(
        self, symbol: str, side: str, quantity: float
    ) -> OrderResult:
        """Close a position at market."""

    @abstractmethod
    async def get_open_orders(self, symbol: str) -> list[dict]:
        """Get open orders for a symbol."""

    @abstractmethod
    async def get_positions(self) -> list[dict]:
        """Get current exchange positions."""

    @abstractmethod
    async def get_balance(self) -> float:
        """Get USDT balance."""

    @abstractmethod
    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        """Set leverage for a symbol."""

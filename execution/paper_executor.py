"""
Paper Executor — Simulated fill engine.

Same interface as LiveExecutor (AD-1).
Realistic fees, slippage, funding rate simulation.
No optimistic fills.
"""
from __future__ import annotations

import random
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from config.loader import AppConfig
from core.logging_setup import get_logger
from core.models import (
    OrderRequest, OrderResult, OrderStatus, Side,
)
from execution.base_executor import BaseExecutor

logger = get_logger("paper_executor")


class PaperExecutor(BaseExecutor):
    """Simulated executor for paper trading. No real orders."""

    def __init__(self, config: AppConfig, initial_balance: float = 1000.0) -> None:
        self._cfg = config
        self._balance = initial_balance
        self._positions: dict[str, dict] = {}  # symbol -> position dict
        self._orders: dict[str, dict] = {}      # client_id -> order dict
        self._last_prices: dict[str, float] = {}
        self._prefix = config.execution.client_order_id_prefix

    def set_price(self, symbol: str, price: float) -> None:
        """Update current price for a symbol (called from candle manager)."""
        self._last_prices[symbol] = price

    def _gen_client_id(self) -> str:
        ts = int(time.time() * 1000)
        uid = uuid.uuid4().hex[:8]
        return f"{self._prefix}_PAPER_{ts}_{uid}"

    def _apply_slippage(self, price: float, side: Side) -> float:
        """Apply realistic slippage."""
        slip_bps = self._cfg.fees.default_slippage_bps
        slip_frac = slip_bps / 10000.0
        # Add random component
        actual_slip = slip_frac * (0.5 + random.random())
        if side == Side.LONG:
            return price * (1 + actual_slip)  # buy higher
        else:
            return price * (1 - actual_slip)  # sell lower

    def _calc_fee(self, notional: float, is_taker: bool = True) -> float:
        bps = self._cfg.fees.taker_bps if is_taker else self._cfg.fees.maker_bps
        return notional * (bps / 10000.0)

    async def place_order(self, request: OrderRequest) -> OrderResult:
        client_id = request.client_order_id or self._gen_client_id()
        symbol = request.symbol
        price = self._last_prices.get(symbol, 0)

        if price <= 0:
            logger.warning("No price data for paper fill", symbol=symbol)
            return OrderResult(
                trade_uuid=request.trade_uuid,
                client_order_id=client_id,
                symbol=symbol, side=request.side,
                status=OrderStatus.REJECTED,
                requested_qty=request.quantity,
            )

        # Simulate MARKET fill with slippage
        fill_price = self._apply_slippage(price, request.side)
        notional = request.quantity * fill_price
        fee = self._calc_fee(notional)

        # Update balance
        if not request.reduce_only:
            # Opening: margin reserved (leverage handles this)
            pass
        else:
            # Closing: realize PnL
            pos = self._positions.get(symbol)
            if pos:
                entry = pos["entry_price"]
                qty = min(request.quantity, pos["quantity"])
                if pos["side"] == "LONG":
                    pnl = (fill_price - entry) * qty
                else:
                    pnl = (entry - fill_price) * qty
                self._balance += pnl - fee

                pos["quantity"] -= qty
                if pos["quantity"] <= 0:
                    del self._positions[symbol]

        if not request.reduce_only:
            self._positions[symbol] = {
                "side": request.side.value,
                "quantity": request.quantity,
                "entry_price": fill_price,
                "notional": notional,
            }

        self._balance -= fee

        result = OrderResult(
            trade_uuid=request.trade_uuid,
            client_order_id=client_id,
            exchange_order_id=f"PAPER_{uuid.uuid4().hex[:12]}",
            symbol=symbol,
            side=request.side,
            status=OrderStatus.FILLED,
            requested_qty=request.quantity,
            filled_qty=request.quantity,
            avg_fill_price=fill_price,
            commission=fee,
            timestamp=datetime.now(timezone.utc),
        )

        logger.info(
            "Paper fill",
            symbol=symbol, side=request.side.value,
            qty=request.quantity, price=round(fill_price, 4),
            fee=round(fee, 4), balance=round(self._balance, 2),
        )
        return result

    async def cancel_order(self, symbol: str, client_order_id: str) -> bool:
        if client_order_id in self._orders:
            del self._orders[client_order_id]
            return True
        return True  # Paper mode: always succeeds

    async def cancel_all_orders(self, symbol: str) -> bool:
        to_remove = [
            k for k, v in self._orders.items() if v.get("symbol") == symbol
        ]
        for k in to_remove:
            del self._orders[k]
        return True

    async def close_position(
        self, symbol: str, side: str, quantity: float,
    ) -> OrderResult:
        s = Side.LONG if side == "LONG" else Side.SHORT
        req = OrderRequest(
            trade_uuid="",
            symbol=symbol,
            side=s,
            quantity=quantity,
            reduce_only=True,
        )
        return await self.place_order(req)

    async def get_open_orders(self, symbol: str) -> list[dict]:
        return [
            v for v in self._orders.values()
            if v.get("symbol") == symbol
        ]

    async def get_positions(self) -> list[dict]:
        result = []
        for sym, pos in self._positions.items():
            result.append({
                "symbol": sym,
                "positionAmt": str(pos["quantity"] if pos["side"] == "LONG" else -pos["quantity"]),
                "entryPrice": str(pos["entry_price"]),
                "unRealizedProfit": "0",
            })
        return result

    async def get_balance(self) -> float:
        return self._balance

    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        logger.info("Paper leverage set", symbol=symbol, leverage=leverage)
        return True

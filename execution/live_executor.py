"""
Live Executor — Binance API order execution.

Implements BaseExecutor using real Binance REST calls.
"""
from __future__ import annotations

import asyncio
import uuid
import time
from datetime import datetime, timezone
from typing import Optional

from config.loader import AppConfig
from core.logging_setup import get_logger
from core.models import (
    OrderRequest, OrderResult, OrderStatus, Side,
)
from data.binance_rest import BinanceRestClient
from data.symbol_info import SymbolInfoManager
from execution.base_executor import BaseExecutor

logger = get_logger("live_executor")


class LiveExecutor(BaseExecutor):
    """Executes orders against real Binance Futures API."""

    def __init__(
        self,
        rest_client: BinanceRestClient,
        symbol_info: SymbolInfoManager,
        config: AppConfig,
    ) -> None:
        self._rest = rest_client
        self._sym_info = symbol_info
        self._cfg = config
        self._prefix = config.execution.client_order_id_prefix

    def _gen_client_id(self) -> str:
        ts = int(time.time() * 1000)
        uid = uuid.uuid4().hex[:8]
        return f"{self._prefix}_{ts}_{uid}"

    async def _resolve_fill(
        self, symbol: str, resp: dict,
    ) -> tuple[float, float, float]:
        """Resolve quantity, weighted fill price, and commission.

        Some Binance Futures accounts omit avgPrice and cumQuote from both the
        RESULT response and GET /order, even when a market order is FILLED.
        The userTrades endpoint is the source of truth for actual executions.
        """
        filled_qty = float(resp.get("executedQty", 0))
        avg_fill_price = float(resp.get("avgPrice", 0))
        cum_quote = float(resp.get("cumQuote", 0))
        if avg_fill_price <= 0 and filled_qty > 0 and cum_quote > 0:
            avg_fill_price = cum_quote / filled_qty

        commission = cum_quote * 0.0004 if cum_quote > 0 else 0.0
        order_id = resp.get("orderId")
        if filled_qty <= 0 or order_id is None:
            return filled_qty, avg_fill_price, commission

        fills: list[dict] = []
        delays = (
            (0.0,)
            if avg_fill_price > 0
            else (0.0, 0.1, 0.25, 0.5, 1.0)
        )
        for delay in delays:
            if delay:
                await asyncio.sleep(delay)
            try:
                fills = await self._rest.get_account_trades(
                    symbol, order_id=int(order_id),
                )
            except Exception as exc:
                logger.warning(
                    "Could not fetch actual order fills",
                    symbol=symbol, order_id=order_id, error=str(exc),
                )
                fills = []
            fill_qty = sum(float(fill.get("qty", 0)) for fill in fills)
            if fill_qty >= filled_qty * 0.999999:
                break

        if fills:
            fill_qty = sum(float(fill.get("qty", 0)) for fill in fills)
            fill_quote = sum(
                float(fill.get("quoteQty", 0))
                or float(fill.get("price", 0)) * float(fill.get("qty", 0))
                for fill in fills
            )
            if fill_qty > 0 and fill_quote > 0:
                filled_qty = fill_qty
                avg_fill_price = fill_quote / fill_qty

            if all(fill.get("commissionAsset") == "USDT" for fill in fills):
                commission = sum(float(fill.get("commission", 0)) for fill in fills)
            elif fill_quote > 0:
                commission = fill_quote * 0.0004

            # Persist the evidence used for accounting in the order's raw JSON.
            resp["_accountTrades"] = fills

        if filled_qty > 0 and avg_fill_price <= 0:
            logger.critical(
                "Filled market order has no resolvable fill price",
                symbol=symbol, order_id=order_id, filled_qty=filled_qty,
            )

        return filled_qty, avg_fill_price, commission

    async def place_order(self, request: OrderRequest) -> OrderResult:
        symbol = request.symbol
        side_str = "BUY" if request.side == Side.LONG else "SELL"

        # Round quantity and price
        qty = self._sym_info.round_quantity(symbol, request.quantity)
        price = self._sym_info.round_price(symbol, request.price or 0)

        # Preflight validation
        check_price = price if price > 0 else (request.stop_price or 0)
        if check_price > 0:
            valid, err = self._sym_info.validate_order(symbol, qty, check_price)
            if not valid:
                logger.error("Preflight failed", symbol=symbol, error=err)
                return OrderResult(
                    trade_uuid=request.trade_uuid,
                    client_order_id=request.client_order_id or self._gen_client_id(),
                    symbol=symbol, side=request.side,
                    status=OrderStatus.REJECTED,
                    requested_qty=request.quantity,
                )

        client_id = request.client_order_id or self._gen_client_id()

        resp = await self._rest.place_order(
            symbol=symbol,
            side=side_str,
            order_type=request.order_type,
            quantity=qty,
            price=price if price > 0 else None,
            stop_price=(
                self._sym_info.round_price(symbol, request.stop_price)
                if request.stop_price else None
            ),
            reduce_only=request.reduce_only,
            client_order_id=client_id,
            new_order_resp_type="RESULT" if request.order_type == "MARKET" else None,
        )

        if not resp or "code" in resp:
            code = resp.get("code", "unknown") if resp else "no_response"
            msg = resp.get("msg", "") if resp else ""
            logger.error("Order rejected by exchange", code=code, msg=msg)
            return OrderResult(
                trade_uuid=request.trade_uuid,
                client_order_id=client_id,
                symbol=symbol, side=request.side,
                status=OrderStatus.REJECTED,
                requested_qty=request.quantity,
                raw=resp or {},
            )

        if (
            request.order_type == "MARKET"
            and float(resp.get("executedQty", 0)) <= 0
            and resp.get("status") in ("NEW", "PENDING")
        ):
            order_id = resp.get("orderId")
            for delay in (0.1, 0.25, 0.5, 1.0):
                await asyncio.sleep(delay)
                refreshed = await self._rest.get_order(
                    symbol,
                    order_id=int(order_id) if order_id is not None else None,
                    client_order_id=None if order_id is not None else client_id,
                )
                if refreshed and "code" not in refreshed:
                    resp = refreshed
                    if (
                        float(resp.get("executedQty", 0)) > 0
                        or resp.get("status") in ("CANCELED", "REJECTED", "EXPIRED")
                    ):
                        break

        status_map = {
            "NEW": OrderStatus.PENDING,
            "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
            "FILLED": OrderStatus.FILLED,
            "CANCELED": OrderStatus.CANCELLED,
            "REJECTED": OrderStatus.REJECTED,
            "EXPIRED": OrderStatus.EXPIRED,
        }

        filled_qty, avg_fill_price, commission = await self._resolve_fill(
            symbol, resp,
        )

        return OrderResult(
            trade_uuid=request.trade_uuid,
            client_order_id=client_id,
            exchange_order_id=str(resp.get("orderId", "")),
            symbol=symbol,
            side=request.side,
            status=status_map.get(resp.get("status", ""), OrderStatus.PENDING),
            requested_qty=request.quantity,
            filled_qty=filled_qty,
            avg_fill_price=avg_fill_price,
            commission=commission,
            timestamp=datetime.now(timezone.utc),
            raw=resp,
        )

    async def cancel_order(self, symbol: str, client_order_id: str) -> bool:
        resp = await self._rest.cancel_order(
            symbol=symbol, client_order_id=client_order_id,
        )
        if resp and "code" not in resp:
            return True
        logger.warning("Cancel failed", symbol=symbol, coid=client_order_id, resp=resp)
        return False

    async def cancel_all_orders(self, symbol: str) -> bool:
        resp = await self._rest.cancel_all_orders(symbol)
        return resp is not None and "code" not in resp

    async def close_position(
        self, symbol: str, side: str, quantity: float,
    ) -> OrderResult:
        close_side = "SELL" if side == "LONG" else "BUY"
        qty = self._sym_info.round_quantity(symbol, quantity)
        client_id = self._gen_client_id()

        resp = await self._rest.place_order(
            symbol=symbol, side=close_side, order_type="MARKET",
            quantity=qty, reduce_only=True, client_order_id=client_id,
            new_order_resp_type="RESULT",
        )

        from core.models import Side as SideEnum
        s = SideEnum.LONG if side == "LONG" else SideEnum.SHORT

        if not resp or "code" in resp:
            return OrderResult(
                trade_uuid="", client_order_id=client_id,
                symbol=symbol, side=s, status=OrderStatus.REJECTED,
                requested_qty=quantity, raw=resp or {},
            )

        if float(resp.get("executedQty", 0)) <= 0 and resp.get("status") in ("NEW", "PENDING"):
            order_id = resp.get("orderId")
            for delay in (0.1, 0.25, 0.5, 1.0):
                await asyncio.sleep(delay)
                refreshed = await self._rest.get_order(
                    symbol,
                    order_id=int(order_id) if order_id is not None else None,
                    client_order_id=None if order_id is not None else client_id,
                )
                if refreshed and "code" not in refreshed:
                    resp = refreshed
                    if (
                        float(resp.get("executedQty", 0)) > 0
                        or resp.get("status") in ("CANCELED", "REJECTED", "EXPIRED")
                    ):
                        break

        filled_qty, avg_fill_price, commission = await self._resolve_fill(
            symbol, resp,
        )

        return OrderResult(
            trade_uuid="", client_order_id=client_id,
            exchange_order_id=str(resp.get("orderId", "")),
            symbol=symbol, side=s, status=OrderStatus.FILLED,
            requested_qty=quantity,
            filled_qty=filled_qty,
            avg_fill_price=avg_fill_price,
            commission=commission,
            timestamp=datetime.now(timezone.utc),
            raw=resp,
        )

    async def get_open_orders(self, symbol: str) -> list[dict]:
        return await self._rest.get_open_orders(symbol) or []

    async def get_positions(self) -> list[dict]:
        positions = await self._rest.get_positions()
        if not positions:
            return []
        return [p for p in positions if float(p.get("positionAmt", 0)) != 0]

    async def get_balance(self) -> float:
        balances = await self._rest.get_balance()
        if not balances:
            return 0.0
        for b in balances:
            if b.get("asset") == "USDT":
                return float(b.get("balance", 0))
        return 0.0

    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        resp = await self._rest.set_leverage(symbol, leverage)
        return resp is not None and "code" not in resp

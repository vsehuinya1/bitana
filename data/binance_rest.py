"""
Binance REST Client

Production REST client with retry, backoff, rate limiting.
Handles: klines, OI, account, positions, orders, income, time sync.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlencode

import aiohttp

from core.logging_setup import get_logger
from data.rate_limiter import RateLimiterGroup

logger = get_logger("binance_rest")

FUTURES_BASE = "https://fapi.binance.com"
TESTNET_BASE = "https://testnet.binancefuture.com"


class BinanceRestClient:
    """Async Binance USDT-M Futures REST client."""

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        testnet: bool = True,
        rate_limiter: Optional[RateLimiterGroup] = None,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._base = TESTNET_BASE if testnet else FUTURES_BASE
        self._session: Optional[aiohttp.ClientSession] = None
        self._rate_limiter = rate_limiter or RateLimiterGroup()
        self._time_offset_ms: int = 0

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(
            headers={"X-MBX-APIKEY": self._api_key},
            timeout=aiohttp.ClientTimeout(total=30),
        )
        await self.sync_time()
        logger.info("Binance REST client started", base=self._base)

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    def _sign(self, params: dict) -> dict:
        params["timestamp"] = int(time.time() * 1000) + self._time_offset_ms
        query = urlencode(params)
        sig = hmac.new(
            self._api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        params["signature"] = sig
        return params

    async def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        signed: bool = False,
        weight: int = 1,
        is_order: bool = False,
    ) -> Any:
        if params is None:
            params = {}

        limiter = self._rate_limiter.orders if is_order else self._rate_limiter.data
        await limiter.acquire(weight)

        if signed:
            params = self._sign(params)

        url = f"{self._base}{path}"

        for attempt in range(3):
            try:
                if self._session is None:
                    raise RuntimeError("BinanceRestClient.start() must be called before making requests")
                async with self._session.request(
                    method, url, params=params if method == "GET" else None,
                    data=params if method != "GET" else None,
                ) as resp:
                    data = await resp.json()
                    if resp.status == 429:
                        wait = int(resp.headers.get("Retry-After", "30"))
                        logger.warning("Rate limited by Binance", wait_s=wait)
                        await asyncio.sleep(wait)
                        await limiter.acquire(weight)
                        continue
                    if resp.status >= 400:
                        logger.error(
                            "Binance API error",
                            status=resp.status, path=path, response=data,
                        )
                        if resp.status in (502, 503) and attempt < 2:
                            await asyncio.sleep(2 ** attempt)
                            continue
                        return data
                    return data
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning(
                    "REST request failed, retrying",
                    path=path, attempt=attempt + 1, error=str(e),
                )
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                else:
                    raise

    # ------------------------------------------------------------------
    # Public endpoints
    # ------------------------------------------------------------------

    async def sync_time(self) -> None:
        data = await self._request("GET", "/fapi/v1/time")
        if data and "serverTime" in data:
            server_ms = data["serverTime"]
            local_ms = int(time.time() * 1000)
            self._time_offset_ms = server_ms - local_ms
            logger.info("Time synced", offset_ms=self._time_offset_ms)

    async def get_exchange_info(self) -> dict:
        return await self._request("GET", "/fapi/v1/exchangeInfo", weight=10)

    async def get_klines(
        self, symbol: str, interval: str, limit: int = 500,
        start_time: int | None = None, end_time: int | None = None,
    ) -> list[list]:
        params: dict[str, Any] = {
            "symbol": symbol, "interval": interval, "limit": limit,
        }
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        return await self._request(
            "GET", "/fapi/v1/klines", params=params, weight=5,
        )

    async def get_open_interest(self, symbol: str) -> dict:
        return await self._request(
            "GET", "/fapi/v1/openInterest",
            params={"symbol": symbol}, weight=1,
        )

    async def get_ticker_price(self, symbol: str) -> dict:
        return await self._request(
            "GET", "/fapi/v2/ticker/price",
            params={"symbol": symbol}, weight=1,
        )

    async def get_book_ticker(self, symbol: str) -> dict:
        return await self._request(
            "GET", "/fapi/v1/ticker/bookTicker",
            params={"symbol": symbol}, weight=2,
        )

    async def get_depth(self, symbol: str, limit: int = 20) -> dict:
        return await self._request(
            "GET", "/fapi/v1/depth",
            params={"symbol": symbol, "limit": limit}, weight=5,
        )

    async def get_mark_price(self, symbol: str) -> dict:
        return await self._request(
            "GET", "/fapi/v1/premiumIndex",
            params={"symbol": symbol}, weight=1,
        )

    async def get_all_force_orders(
        self,
        symbol: str | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int = 1000,
    ) -> list[dict]:
        """Get market-wide forced liquidation orders (public endpoint).

        Returns individual liquidation orders. Aggregate by day for liq volumes.
        Max 7 days without startTime; up to 90 days with startTime.
        """
        params: dict[str, Any] = {"limit": limit}
        if symbol:
            params["symbol"] = symbol
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        weight = 20 if symbol else 50
        return await self._request(
            "GET", "/fapi/v1/allForceOrders",
            params=params, weight=weight,
        )

    async def get_long_short_ratio(
        self,
        symbol: str,
        period: str = "1d",
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int = 30,
    ) -> list[dict]:
        """Get top trader long/short ratio (public endpoint).

        Used as a sentiment proxy when liquidation history is thin.
        period: 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d
        """
        params: dict[str, Any] = {
            "symbol": symbol, "period": period, "limit": limit,
        }
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        return await self._request(
            "GET", "/futures/data/topLongShortAccountRatio",
            params=params, weight=1,
        )

    # ------------------------------------------------------------------
    # Account endpoints (signed)
    # ------------------------------------------------------------------

    async def get_account(self) -> dict:
        return await self._request(
            "GET", "/fapi/v2/account", signed=True, weight=5,
        )

    async def get_balance(self) -> list[dict]:
        return await self._request(
            "GET", "/fapi/v2/balance", signed=True, weight=5,
        )

    async def get_positions(self) -> list[dict]:
        return await self._request(
            "GET", "/fapi/v2/positionRisk", signed=True, weight=5,
        )

    async def get_open_orders(self, symbol: str | None = None) -> list[dict]:
        params = {}
        if symbol:
            params["symbol"] = symbol
        return await self._request(
            "GET", "/fapi/v1/openOrders",
            params=params, signed=True, weight=5, is_order=True,
        )

    async def get_income(
        self,
        income_type: str | None = None,
        symbol: str | None = None,
        start_time: int | None = None,
        limit: int = 100,
    ) -> list[dict]:
        params: dict[str, Any] = {"limit": limit}
        if income_type:
            params["incomeType"] = income_type
        if symbol:
            params["symbol"] = symbol
        if start_time:
            params["startTime"] = start_time
        return await self._request(
            "GET", "/fapi/v1/income",
            params=params, signed=True, weight=30,
        )

    # ------------------------------------------------------------------
    # Trading endpoints (signed)
    # ------------------------------------------------------------------

    async def test_order_permission(
        self,
        symbol: str = "BTCUSDT",
        quantity: float = 0.001,
    ) -> tuple[bool, dict]:
        """Validate signed futures trading permission without placing an order."""
        response = await self._request(
            "POST", "/fapi/v1/order/test",
            params={
                "symbol": symbol,
                "side": "BUY",
                "type": "MARKET",
                "quantity": str(quantity),
            },
            signed=True,
            weight=1,
            is_order=True,
        )
        if not isinstance(response, dict):
            return False, {"code": "invalid_response", "msg": str(response)}
        return "code" not in response, response

    async def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str = "MARKET",
        quantity: float | None = None,
        price: float | None = None,
        stop_price: float | None = None,
        reduce_only: bool = False,
        client_order_id: str | None = None,
        time_in_force: str | None = None,
        new_order_resp_type: str | None = None,
    ) -> dict:
        params: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
        }
        if quantity is not None:
            params["quantity"] = str(quantity)
        if price is not None:
            params["price"] = str(price)
        if stop_price is not None:
            params["stopPrice"] = str(stop_price)
        if reduce_only:
            params["reduceOnly"] = "true"
        if client_order_id:
            params["newClientOrderId"] = client_order_id
        if new_order_resp_type:
            params["newOrderRespType"] = new_order_resp_type
        if time_in_force:
            params["timeInForce"] = time_in_force
        elif order_type == "LIMIT":
            params["timeInForce"] = "GTC"

        return await self._request(
            "POST", "/fapi/v1/order",
            params=params, signed=True, weight=1, is_order=True,
        )

    async def get_order(
        self,
        symbol: str,
        order_id: int | None = None,
        client_order_id: str | None = None,
    ) -> dict:
        params: dict[str, Any] = {"symbol": symbol}
        if order_id is not None:
            params["orderId"] = order_id
        if client_order_id:
            params["origClientOrderId"] = client_order_id
        return await self._request(
            "GET", "/fapi/v1/order",
            params=params, signed=True, weight=1,
        )

    async def cancel_order(
        self, symbol: str,
        order_id: int | None = None,
        client_order_id: str | None = None,
    ) -> dict:
        params: dict[str, Any] = {"symbol": symbol}
        if order_id is not None:
            params["orderId"] = order_id
        if client_order_id:
            params["origClientOrderId"] = client_order_id
        return await self._request(
            "DELETE", "/fapi/v1/order",
            params=params, signed=True, weight=1, is_order=True,
        )

    async def cancel_all_orders(self, symbol: str) -> dict:
        return await self._request(
            "DELETE", "/fapi/v1/allOpenOrders",
            params={"symbol": symbol}, signed=True, weight=1, is_order=True,
        )

    async def set_leverage(self, symbol: str, leverage: int) -> dict:
        return await self._request(
            "POST", "/fapi/v1/leverage",
            params={"symbol": symbol, "leverage": leverage},
            signed=True, weight=1, is_order=True,
        )

    async def set_margin_type(self, symbol: str, margin_type: str = "CROSSED") -> dict:
        return await self._request(
            "POST", "/fapi/v1/marginType",
            params={"symbol": symbol, "marginType": margin_type},
            signed=True, weight=1, is_order=True,
        )

    # ------------------------------------------------------------------
    # User data stream (for WebSocket)
    # ------------------------------------------------------------------

    async def create_listen_key(self) -> str:
        data = await self._request(
            "POST", "/fapi/v1/listenKey", signed=False, weight=1,
        )
        return data.get("listenKey", "")

    async def keepalive_listen_key(self) -> None:
        await self._request(
            "PUT", "/fapi/v1/listenKey", signed=False, weight=1,
        )

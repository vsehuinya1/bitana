"""Exchange side of the wick/discount engine: public market data, a live broker (Binance USDT-M, sub-account keys) and a
dry-run broker that simulates the same orders on 1m bars without any account call.

Order plan (all long-only, one-way mode, every exit reduce-only with the leg's own quantity so two legs on one coin
never close each other):
  wick entry   CONDITIONAL TAKE_PROFIT BUY (fires when last price <= trigger, then a limit at the bid). A plain resting
               limit is not possible: Binance's PERCENT_PRICE band (5-10% below mark) rejects ~1/3 of research bids.
               If price is already below the bid (-2021 "would immediately trigger"), a plain LIMIT at the bid instead
               (fills at market <= bid, like the paper rule's fill = min(open, bid)).
  disc entry   MARKET BUY.
  take-profit  CONDITIONAL TAKE_PROFIT_MARKET SELL reduce-only (wick only, trigger = pre-wick close + 1 tick).
  disaster     CONDITIONAL STOP_MARKET SELL reduce-only, trigger = entry - k R.
  time exit    MARKET SELL reduce-only.
Triggers use CONTRACT_PRICE (last trade), the price the research measured on.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import aiohttp

from core.logging_setup import get_logger
from data.binance_rest import BinanceRestClient

from .rules import fmt_dec

logger = get_logger("wd_engine.exchange")
FAPI = "https://fapi.binance.com"
SPOT = "https://api.binance.com"


def _minute_ms() -> int:
    return int(time.time() // 60 * 60000)


@dataclass
class Filters:
    tick: str
    step: str
    market_step: str
    min_notional: float


@dataclass
class OrderResult:
    """What the engine needs back from any order call or query."""
    ok: bool
    status: str = ""              # NEW / TRIGGERED / PARTIALLY_FILLED / FILLED / CANCELED / EXPIRED / REJECTED
    filled_qty: float = 0.0
    avg_price: float = 0.0
    kind: str = ""                # algo / limit / market
    code: int | None = None
    msg: str = ""
    raw: dict = field(default_factory=dict)


# ----------------------------------------------------------------------------------------------- public market data
class Market:
    def __init__(self) -> None:
        self._s: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        self._s = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))

    async def close(self) -> None:
        if self._s:
            await self._s.close()

    async def _get(self, url: str, params: dict) -> Any:
        assert self._s is not None
        for a in range(3):
            try:
                async with self._s.get(url, params=params) as r:
                    if r.status == 200:
                        return await r.json()
                    logger.warning("public GET failed", url=url, status=r.status)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning("public GET error", url=url, error=str(e))
            await asyncio.sleep(1 + a)
        return None

    async def filters(self, symbols: list[str]) -> dict[str, Filters]:
        info = await self._get(f"{FAPI}/fapi/v1/exchangeInfo", {})
        out = {}
        for s in (info or {}).get("symbols", []):
            if s["symbol"] in symbols:
                f = {x["filterType"]: x for x in s["filters"]}
                out[s["symbol"]] = Filters(tick=f["PRICE_FILTER"]["tickSize"], step=f["LOT_SIZE"]["stepSize"],
                                           market_step=f["MARKET_LOT_SIZE"]["stepSize"],
                                           min_notional=float(f["MIN_NOTIONAL"]["notional"]))
        return out

    async def klines(self, symbol: str, interval: str, limit: int, spot: bool = False) -> list[tuple]:
        """Closed bars only: (open_ms, o, h, l, c), oldest first."""
        url = f"{SPOT}/api/v3/klines" if spot else f"{FAPI}/fapi/v1/klines"
        r = await self._get(url, {"symbol": symbol, "interval": interval, "limit": limit})
        now = int(time.time() * 1000)
        return [(int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4])) for k in (r or []) if int(k[6]) < now]

    async def last_price(self, symbol: str) -> float | None:
        r = await self._get(f"{FAPI}/fapi/v1/ticker/price", {"symbol": symbol})
        return float(r["price"]) if r else None


# ----------------------------------------------------------------------------------------------- live broker
class _Rest(BinanceRestClient):
    """The bot's REST client plus a conditional order that carries its own quantity + reduceOnly (the bot's version
    only knows closePosition, which would close every leg on the coin)."""

    async def algo(self, symbol: str, side: str, otype: str, qty: str, trigger: str, price: str | None,
                   reduce_only: bool, cid: str) -> dict:
        p: dict[str, Any] = {"algoType": "CONDITIONAL", "symbol": symbol, "side": side, "type": otype,
                             "quantity": qty, "triggerPrice": trigger, "workingType": "CONTRACT_PRICE",
                             "clientAlgoId": cid, "newOrderRespType": "ACK"}
        if price is not None:
            p["price"] = price
            p["timeInForce"] = "GTC"
        if reduce_only:
            p["reduceOnly"] = "true"
        return await self._request("POST", "/fapi/v1/algoOrder", params=p, signed=True, weight=1, is_order=True)

    async def position_mode_dual(self) -> bool | None:
        r = await self._request("GET", "/fapi/v1/positionSide/dual", params={}, signed=True, weight=30)
        return r.get("dualSidePosition") if isinstance(r, dict) else None

    async def position_amt(self, symbol: str) -> float | None:
        r = await self._request("GET", "/fapi/v2/positionRisk", params={"symbol": symbol}, signed=True, weight=5)
        if isinstance(r, list):
            return sum(float(x.get("positionAmt", 0)) for x in r)
        return None


def _err(r: Any) -> tuple[int | None, str]:
    if isinstance(r, dict) and "code" in r and str(r.get("code")) not in ("200",):
        try:
            return int(r["code"]), str(r.get("msg", ""))
        except (TypeError, ValueError):
            return None, str(r)
    return None, ""


class LiveBroker:
    """Real orders on the sub-account whose keys are passed in. Never logs the keys."""

    def __init__(self, api_key: str, api_secret: str, testnet: bool) -> None:
        self.rest = _Rest(api_key=api_key, api_secret=api_secret, testnet=testnet)
        self.dry = False

    async def start(self) -> None:
        await self.rest.start()

    async def close(self) -> None:
        await self.rest.close()

    async def preflight(self, symbols: list[str], leverage: int, margin_type: str) -> list[str]:
        """One-way mode required; sets margin type + leverage per symbol. Returns problems (empty = ready)."""
        problems = []
        dual = await self.rest.position_mode_dual()
        if dual is None:
            problems.append("cannot read position mode (keys / permissions?)")
        elif dual:
            problems.append("account is in Hedge Mode; the engine needs One-way Mode")
        for s in symbols:
            r = await self.rest.set_margin_type(s, margin_type)
            c, m = _err(r)
            if c not in (None, -4046):                      # -4046: no need to change margin type
                problems.append(f"{s} margin type: {c} {m}")
            r = await self.rest.set_leverage(s, leverage)
            c, m = _err(r)
            if c is not None:
                problems.append(f"{s} leverage: {c} {m}")
        return problems

    async def entry_bid(self, symbol: str, qty: Decimal, bid: Decimal, cid: str) -> OrderResult:
        r = await self.rest.algo(symbol, "BUY", "TAKE_PROFIT", fmt_dec(qty), fmt_dec(bid), fmt_dec(bid), False, cid)
        c, m = _err(r)
        if c == -2021:                                      # already below the bid: plain limit at the bid
            r = await self.rest.place_order(symbol, "BUY", "LIMIT", quantity=fmt_dec(qty), price=fmt_dec(bid),
                                            client_order_id=cid, time_in_force="GTC", new_order_resp_type="RESULT")
            c, m = _err(r)
            return OrderResult(ok=c is None, status=str(r.get("status", "")) if c is None else "REJECTED", kind="limit",
                               filled_qty=float(r.get("executedQty", 0) or 0) if c is None else 0.0,
                               avg_price=float(r.get("avgPrice", 0) or 0) if c is None else 0.0, code=c, msg=m, raw=r)
        return OrderResult(ok=c is None, status="NEW" if c is None else "REJECTED", kind="algo", code=c, msg=m, raw=r)

    async def market(self, symbol: str, side: str, qty: Decimal, reduce_only: bool, cid: str) -> OrderResult:
        r = await self.rest.place_order(symbol, side, "MARKET", quantity=fmt_dec(qty), reduce_only=reduce_only,
                                        client_order_id=cid, new_order_resp_type="RESULT")
        c, m = _err(r)
        if c is not None:
            return OrderResult(ok=False, status="REJECTED", kind="market", code=c, msg=m, raw=r)
        return OrderResult(ok=True, status=str(r.get("status", "")), kind="market", raw=r,
                           filled_qty=float(r.get("executedQty", 0) or 0), avg_price=float(r.get("avgPrice", 0) or 0))

    async def exit_algo(self, symbol: str, otype: str, qty: Decimal, trigger: Decimal, cid: str) -> OrderResult:
        r = await self.rest.algo(symbol, "SELL", otype, fmt_dec(qty), fmt_dec(trigger), None, True, cid)
        c, m = _err(r)
        return OrderResult(ok=c is None, status="NEW" if c is None else "REJECTED", kind="algo", code=c, msg=m, raw=r)

    async def query(self, symbol: str, kind: str, cid: str) -> OrderResult:
        if kind == "algo":
            r = await self.rest.get_algo_order(cid)
            c, m = _err(r)
            if c is not None:
                return OrderResult(ok=False, code=c, msg=m, kind=kind, raw=r)
            st = str(r.get("algoStatus", ""))
            fq, ap = float(r.get("actualQty", 0) or 0), float(r.get("actualPrice", 0) or 0)
            oid = r.get("actualOrderId")
            if oid and st in ("TRIGGERED", "FILLED", "PARTIALLY_FILLED", "FINISHED"):
                o = await self.rest.get_order(symbol, order_id=int(oid))       # the triggered order: fills + avg price
                if isinstance(o, dict) and "status" in o:
                    fq, ap = float(o.get("executedQty", 0) or 0), float(o.get("avgPrice", 0) or 0)
                    st = {"FILLED": "FILLED", "PARTIALLY_FILLED": "PARTIALLY_FILLED", "NEW": "TRIGGERED"}.get(o["status"], o["status"])
            return OrderResult(ok=True, status=st, filled_qty=fq, avg_price=ap, kind=kind, raw=r)
        r = await self.rest.get_order(symbol, client_order_id=cid)
        c, m = _err(r)
        if c is not None:
            return OrderResult(ok=False, code=c, msg=m, kind=kind, raw=r)
        return OrderResult(ok=True, status=str(r.get("status", "")), filled_qty=float(r.get("executedQty", 0) or 0),
                           avg_price=float(r.get("avgPrice", 0) or 0), kind=kind, raw=r)

    async def cancel(self, symbol: str, kind: str, cid: str) -> OrderResult:
        r = await (self.rest.cancel_algo_order(cid) if kind == "algo" else self.rest.cancel_order(symbol, client_order_id=cid))
        c, m = _err(r)
        return OrderResult(ok=c is None, code=c, msg=m, kind=kind, raw=r)

    async def cancel_bid(self, symbol: str, kind: str, cid: str) -> OrderResult:
        """End of the bid hour: cancel the conditional and, if it already triggered, the limit order it spawned.
        Returns the final state (filled qty / avg price of whatever executed)."""
        if kind == "algo":
            await self.rest.cancel_algo_order(cid)
            r = await self.rest.get_algo_order(cid)
            oid = r.get("actualOrderId") if isinstance(r, dict) else None
            if oid:
                await self.rest.cancel_order(symbol, order_id=int(oid))
        else:
            await self.rest.cancel_order(symbol, client_order_id=cid)
        return await self.query(symbol, kind, cid)

    async def position_amt(self, symbol: str) -> float | None:
        return await self.rest.position_amt(symbol)

    async def equity(self) -> float | None:
        """REALIZED wallet balance (2026-10-05): open losses are excluded so a crash low never pauses entries; the floor
        stops new entries only after real losses."""
        a = await self.rest.get_account()
        try:
            return float(a["totalWalletBalance"])
        except (TypeError, KeyError, ValueError):
            return None

    async def listen_key(self) -> str:
        return await self.rest.create_listen_key()

    async def keepalive(self) -> None:
        await self.rest.keepalive_listen_key()


# ----------------------------------------------------------------------------------------------- dry-run broker
@dataclass
class _DryOrder:
    symbol: str
    kind: str          # algo / limit / market
    otype: str         # TAKE_PROFIT (buy) / LIMIT / TAKE_PROFIT_MARKET / STOP_MARKET
    side: str
    qty: float
    trigger: float
    price: float | None
    status: str = "NEW"
    filled: float = 0.0
    avg: float = 0.0
    min_bar_ms: int = 0  # bars opening before this are ignored (no fills on prices from before the order existed)


class DryBroker:
    """Same interface as LiveBroker; no account calls. Orders fill on 1m bars fed by the engine (on_bar), with the
    order the bar implies: a buy trigger fills at min(bar open, bid) when the low reaches it; a stop at min(open,
    trigger) when the low reaches it; a take-profit at max(open, trigger) when the high reaches it. Market orders fill
    at the last price the engine passes. Fees are not simulated here (the engine books the paper cost model)."""

    def __init__(self, market: Market) -> None:
        self.md = market
        self.orders: dict[str, _DryOrder] = {}
        self.dry = True

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def preflight(self, symbols: list[str], leverage: int, margin_type: str) -> list[str]:
        return []

    async def entry_bid(self, symbol: str, qty: Decimal, bid: Decimal, cid: str) -> OrderResult:
        px = await self.md.last_price(symbol)
        if px is not None and px <= float(bid):            # mirrors the live -2021 fallback: fills now at market
            self.orders[cid] = _DryOrder(symbol, "limit", "LIMIT", "BUY", float(qty), float(bid), float(bid), "FILLED", float(qty), px)
            return OrderResult(ok=True, status="FILLED", filled_qty=float(qty), avg_price=px, kind="limit")
        self.orders[cid] = _DryOrder(symbol, "algo", "TAKE_PROFIT", "BUY", float(qty), float(bid), float(bid),
                                     min_bar_ms=_minute_ms())                  # the placement minute counts (bids go in at :00:05)
        return OrderResult(ok=True, status="NEW", kind="algo")

    async def market(self, symbol: str, side: str, qty: Decimal, reduce_only: bool, cid: str) -> OrderResult:
        px = await self.md.last_price(symbol)
        if px is None:
            return OrderResult(ok=False, status="REJECTED", msg="no price", kind="market")
        self.orders[cid] = _DryOrder(symbol, "market", "MARKET", side, float(qty), px, None, "FILLED", float(qty), px)
        return OrderResult(ok=True, status="FILLED", filled_qty=float(qty), avg_price=px, kind="market")

    async def exit_algo(self, symbol: str, otype: str, qty: Decimal, trigger: Decimal, cid: str) -> OrderResult:
        px = await self.md.last_price(symbol)
        t = float(trigger)
        if px is not None and ((otype == "STOP_MARKET" and px <= t) or (otype == "TAKE_PROFIT_MARKET" and px >= t)):
            return OrderResult(ok=False, status="REJECTED", code=-2021, msg="Order would immediately trigger.", kind="algo")
        self.orders[cid] = _DryOrder(symbol, "algo", otype, "SELL", float(qty), t, None,
                                     min_bar_ms=_minute_ms() + 60000)          # exits: only minutes after the entry minute
        return OrderResult(ok=True, status="NEW", kind="algo")

    async def query(self, symbol: str, kind: str, cid: str) -> OrderResult:
        o = self.orders.get(cid)
        if o is None:
            return OrderResult(ok=False, code=-2013, msg="Order does not exist.", kind=kind)
        return OrderResult(ok=True, status=o.status, filled_qty=o.filled, avg_price=o.avg, kind=o.kind)

    async def cancel(self, symbol: str, kind: str, cid: str) -> OrderResult:
        o = self.orders.get(cid)
        if o and o.status == "NEW":
            o.status = "CANCELED"
        return OrderResult(ok=True, kind=kind)

    async def cancel_bid(self, symbol: str, kind: str, cid: str) -> OrderResult:
        await self.cancel(symbol, kind, cid)
        return await self.query(symbol, kind, cid)

    async def position_amt(self, symbol: str) -> float | None:
        return None

    async def equity(self) -> float | None:
        return None

    def on_bar(self, symbol: str, t_ms: int, o: float, h: float, l: float, c: float) -> list[str]:
        """Feed one closed 1m bar (open time t_ms); returns the client ids that filled on it."""
        done = []
        for cid, x in self.orders.items():
            if x.symbol != symbol or x.status != "NEW" or t_ms < x.min_bar_ms:
                continue
            fill = None
            if x.otype == "TAKE_PROFIT" and l <= x.trigger:
                fill = min(o, x.price or x.trigger)
            elif x.otype == "STOP_MARKET" and l <= x.trigger:
                fill = min(o, x.trigger)
            elif x.otype == "TAKE_PROFIT_MARKET" and h >= x.trigger:
                fill = max(o, x.trigger)
            if fill is not None:
                x.status, x.filled, x.avg = "FILLED", x.qty, fill
                done.append(cid)
        return done

    def symbols_with_orders(self) -> set[str]:
        return {x.symbol for x in self.orders.values() if x.status == "NEW"}

    def dump(self) -> str:
        return json.dumps({k: vars(v) for k, v in self.orders.items() if v.status == "NEW"})

"""
Order Manager

Order lifecycle management: client IDs, preflight, spread/slippage checks,
partial fill handling.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

from config.loader import AppConfig
from core.logging_setup import get_logger
from core.models import (
    OrderRequest, OrderResult, OrderStatus, Position, PositionState, Side, Signal,
)
from data.symbol_info import SymbolInfoManager
from execution.base_executor import BaseExecutor
from storage.database import Database

if TYPE_CHECKING:
    from tg_bot.alerts import TelegramAlerts

logger = get_logger("order_manager")

# Binance rejection codes that mean "not enough funds for this trade right now".
# These are expected when concurrent positions have consumed available margin;
# the correct response is to skip this signal, not to pause the whole bot.
SOFT_REJECT_CODES = {-2019, -2018}


class OrderManager:
    """Manages order lifecycle against the executor interface."""

    def __init__(
        self,
        executor: BaseExecutor,
        symbol_info: SymbolInfoManager,
        config: AppConfig,
        database: Database,
        alerts: TelegramAlerts | None = None,
    ) -> None:
        self._executor = executor
        self._sym_info = symbol_info
        self._cfg = config
        self._db = database
        self._alerts = alerts
        self._prefix = config.execution.client_order_id_prefix
        # Set when the most recent entry was rejected for insufficient
        # margin/balance. Lets the caller skip the signal instead of pausing.
        self.last_soft_reject = False
        self.last_soft_reject_reason: str | None = None
        # catastrophe-stop placement attempts per trade (sync retries at most once per _CS_RETRY_S)
        self._cs_attempts: dict[str, float] = {}

    async def _critical_order_failure(
        self,
        action: str,
        symbol: str,
        result: OrderResult | None = None,
        detail: str = "",
    ) -> None:
        if self._cfg.mode != "live" or self._alerts is None:
            return
        raw = result.raw if result is not None else {}
        code = raw.get("code", "unknown") if isinstance(raw, dict) else "unknown"
        msg = raw.get("msg", detail) if isinstance(raw, dict) else detail
        await self._alerts.critical(
            f"<b>LIVE ORDER FAILURE</b>\n"
            f"Action: {action}\n"
            f"Symbol: {symbol}\n"
            f"Code: <code>{code}</code>\n"
            f"Reason: {msg or detail or 'unknown'}"
        )

    @staticmethod
    def _is_soft_reject(result: OrderResult | None) -> bool:
        """True if the rejection is an insufficient-funds condition."""
        raw = result.raw if result is not None else None
        if not isinstance(raw, dict):
            return False
        try:
            return int(raw.get("code")) in SOFT_REJECT_CODES
        except (TypeError, ValueError):
            return False

    def _gen_client_id(self) -> str:
        ts = int(time.time() * 1000)
        uid = uuid.uuid4().hex[:8]
        return f"{self._prefix}_{ts}_{uid}"

    async def check_spread(self, symbol: str, rest_client=None) -> tuple[bool, float]:
        """Check if spread is acceptable. Returns (ok, spread_bps)."""
        if rest_client is None:
            return True, 0.0
        try:
            ticker = await rest_client.get_book_ticker(symbol)
            if not ticker:
                return True, 0.0
            bid = float(ticker.get("bidPrice", 0))
            ask = float(ticker.get("askPrice", 0))
            if bid <= 0 or ask <= 0:
                return True, 0.0
            mid = (bid + ask) / 2
            spread_bps = ((ask - bid) / mid) * 10000
            max_spread = self._cfg.execution.max_spread_bps
            if spread_bps > max_spread:
                logger.warning(
                    "Spread too wide",
                    symbol=symbol, spread_bps=round(spread_bps, 1),
                    max_bps=max_spread,
                )
                return False, spread_bps
            return True, spread_bps
        except Exception as e:
            logger.error("Spread check failed", error=str(e))
            return True, 0.0  # allow on error

    async def execute_entry(
        self,
        signal: Signal,
        quantity: float,
        leverage: int,
    ) -> Optional[OrderResult]:
        """Execute a new position entry."""
        symbol = signal.symbol
        client_id = self._gen_client_id()
        self.last_soft_reject = False
        self.last_soft_reject_reason: str | None = None  # ENTRY-AUDIT #5 (observability)

        # Round quantity
        quantity = self._sym_info.round_quantity(symbol, quantity)
        if quantity <= 0:
            # Too small for LOT_SIZE/minQty after sizing (tiny equity / high
            # price / margin slot). Soft-skip — do not pause the whole book.
            self.last_soft_reject = True
            self.last_soft_reject_reason = "qty_rounded_zero"
            logger.warning("Quantity rounded to zero — entry skipped", symbol=symbol)
            if self._cfg.mode == "live" and self._alerts is not None:
                await self._alerts.warning(
                    f"Entry skipped ({symbol}): quantity rounded to zero "
                    f"(below exchange min lot)"
                )
            return None

        # Exchange min-notional floor: risk-based sizing can produce
        # notionals below Binance MIN_NOTIONAL on small equity / wide stops
        # (-4164). Skip the trade — never inflate size above intended risk.
        sf = self._sym_info.get_filters(symbol)
        if sf is not None:
            est_notional = quantity * signal.entry_price
            if est_notional < sf.min_notional * 1.02:  # buffer vs fill drift
                self.last_soft_reject = True
                self.last_soft_reject_reason = "min_notional"
                logger.warning(
                    "Entry skipped — notional below exchange minimum",
                    symbol=symbol,
                    est_notional=round(est_notional, 2),
                    min_notional=sf.min_notional,
                )
                if self._cfg.mode == "live" and self._alerts is not None:
                    await self._alerts.warning(
                        f"Entry skipped ({symbol}): notional {est_notional:.2f} "
                        f"< exchange min {sf.min_notional:.2f} "
                        f"(equity too small for stop distance)"
                    )
                return None

        # Set leverage first
        if not await self._executor.set_leverage(symbol, leverage):
            logger.error("Leverage setup rejected", symbol=symbol, leverage=leverage)
            await self._critical_order_failure(
                "SET_LEVERAGE", symbol, detail=f"Leverage {leverage} rejected",
            )
            return None

        # Build order
        request = OrderRequest(
            trade_uuid=signal.trade_uuid,
            client_order_id=client_id,
            symbol=symbol,
            side=signal.side,
            quantity=quantity,
            order_type="MARKET",
        )

        # Execute
        result = await self._executor.place_order(request)
        await self._db.save_order(result)

        if result.status == OrderStatus.REJECTED:
            if self._is_soft_reject(result):
                self.last_soft_reject = True
                self.last_soft_reject_reason = "insufficient_margin"
                raw = result.raw if isinstance(result.raw, dict) else {}
                logger.warning(
                    "Entry skipped — insufficient margin",
                    symbol=symbol, code=raw.get("code"), msg=raw.get("msg"),
                )
                if self._cfg.mode == "live" and self._alerts is not None:
                    await self._alerts.warning(
                        f"Entry skipped ({symbol}): insufficient margin "
                        f"for concurrent positions"
                    )
                return None
            logger.error("Entry order rejected", symbol=symbol)
            await self._critical_order_failure("ENTRY", symbol, result)
            return None

        # Handle partial fills
        if result.status == OrderStatus.PARTIALLY_FILLED:
            result = await self._handle_partial_fill(result)

        if result.filled_qty <= 0:
            logger.warning("Zero fill on entry", symbol=symbol)
            await self._critical_order_failure(
                "ENTRY", symbol, result, detail="Exchange returned zero fill",
            )
            return None
        if result.avg_fill_price <= 0:
            logger.critical("Entry fill price unavailable", symbol=symbol)
            await self._critical_order_failure(
                "ENTRY", symbol, result,
                detail="Order filled but actual fill price could not be resolved",
            )
            return None

        logger.info(
            "Entry filled",
            trade_uuid=signal.trade_uuid,
            symbol=symbol, side=signal.side.value,
            qty=result.filled_qty, price=result.avg_fill_price,
        )
        return result

    async def execute_exit(
        self, position: Position, quantity: float, reason: str = "exit",
    ) -> Optional[OrderResult]:
        """Execute position exit (partial or full)."""
        symbol = position.symbol
        quantity = self._sym_info.round_quantity(symbol, quantity)
        if quantity <= 0:
            return None

        client_id = self._gen_client_id()
        close_side = Side.SHORT if position.side == Side.LONG else Side.LONG

        request = OrderRequest(
            trade_uuid=position.trade_uuid,
            client_order_id=client_id,
            symbol=symbol,
            side=close_side,
            quantity=quantity,
            order_type="MARKET",
            reduce_only=True,
        )

        result = await self._executor.place_order(request)
        await self._db.save_order(result)

        if result.status == OrderStatus.REJECTED:
            logger.error("Exit order rejected", symbol=symbol, reason=reason)
            await self._critical_order_failure("EXIT", symbol, result)
            return None

        if result.status == OrderStatus.PARTIALLY_FILLED:
            result = await self._handle_partial_fill(result)

        if result.filled_qty <= 0 or result.avg_fill_price <= 0:
            logger.critical(
                "Exit fill details unavailable",
                symbol=symbol, qty=result.filled_qty,
                price=result.avg_fill_price,
            )
            await self._critical_order_failure(
                "EXIT", symbol, result,
                detail="Exit filled but quantity/price could not be resolved",
            )
            return None

        logger.info(
            "Exit filled",
            trade_uuid=position.trade_uuid,
            symbol=symbol, reason=reason,
            qty=result.filled_qty, price=result.avg_fill_price,
        )
        return result

    async def place_stop_order(
        self, position: Position, stop_price: float,
    ) -> Optional[OrderResult]:
        """Place a stop-loss order."""
        symbol = position.symbol
        stop_price = self._sym_info.round_price(symbol, stop_price)
        quantity = self._sym_info.round_quantity(symbol, position.quantity)

        close_side = "SELL" if position.side == Side.LONG else "BUY"
        client_id = self._gen_client_id()

        request = OrderRequest(
            trade_uuid=position.trade_uuid,
            client_order_id=client_id,
            symbol=symbol,
            side=Side.SHORT if position.side == Side.LONG else Side.LONG,
            quantity=quantity,
            order_type="STOP_MARKET",
            stop_price=stop_price,
            reduce_only=True,
        )

        result = await self._executor.place_order(request)
        await self._db.save_order(result)
        if result.status == OrderStatus.REJECTED:
            logger.error("Stop order rejected", symbol=symbol)
            await self._critical_order_failure("STOP", symbol, result)
        return result

    # ------------------------------------------------------------------
    # Exchange-resident catastrophe stop (2026-09-25, owner permission "hard stop fix")
    # ------------------------------------------------------------------
    # Backstop for a dead or unresponsive bot, not a replacement for the close-checked stop in PositionManager.
    # STOP_MARKET closePosition on the Algo service at entry -/+ mult x stop distance, MARK_PRICE trigger.
    # closePosition can only flatten, never open or flip. Assumes a one-way-mode account dedicated to the bot:
    # the symbol's algo orders are cleared right before placing.
    _CS_PREFIX = "BITANA_CS_"
    _CS_ALIVE = ("NEW", "TRIGGERING")
    _CS_RETRY_S = 600
    _CS_MIN_AGE_S = 60

    @staticmethod
    def _algo_ok(resp: object) -> bool:
        """Algo endpoints answer success with the order object (place/query) or code "200" (cancel)."""
        if not isinstance(resp, dict) or not resp:
            return False
        code = resp.get("code")
        return code is None or str(code) == "200"

    def _cs_mult(self) -> float:
        return float(getattr(self._cfg.execution, "catastrophe_stop_mult", 0.0) or 0.0)

    def _cs_enabled(self) -> bool:
        return self._cfg.mode == "live" and self._cs_mult() > 0

    def catastrophe_trigger(self, position: Position) -> float | None:
        dist = abs(position.entry_price - position.initial_stop)
        mult = self._cs_mult()
        if mult <= 0 or dist <= 0 or position.entry_price <= 0:
            return None
        if position.side == Side.LONG:
            raw = position.entry_price - mult * dist
        else:
            raw = position.entry_price + mult * dist
        if raw <= 0:
            return None
        return self._sym_info.round_price(position.symbol, raw)

    async def place_catastrophe_stop(self, position: Position) -> bool:
        """Place the backstop for a bot-managed position. Never raises; failures alert and return False."""
        if not self._cs_enabled() or position.externally_managed:
            return False
        trigger = self.catastrophe_trigger(position)
        if trigger is None:
            return False
        self._cs_attempts[position.trade_uuid] = time.time()
        cid = f"{self._CS_PREFIX}{position.trade_uuid.replace('-', '')[:12]}_{int(time.time())}"
        try:
            # a stale closePosition stop from an earlier position on this symbol must never apply to this one
            await self._executor.cancel_all_algo_orders(position.symbol)
            close_side = "SELL" if position.side == Side.LONG else "BUY"
            resp = await self._executor.place_algo_stop(
                position.symbol, close_side, trigger, cid,
                self._cfg.execution.catastrophe_working_type,
            )
        except Exception as e:  # noqa: BLE001
            resp = {"code": "exception", "msg": f"{type(e).__name__}: {e}"}
        if not self._algo_ok(resp):
            msg = resp.get("msg", "no response") if isinstance(resp, dict) else "no response"
            logger.error("Catastrophe stop rejected", symbol=position.symbol, trigger=trigger, response=resp)
            await self._critical_order_failure(
                "CATASTROPHE_STOP", position.symbol,
                detail=f"{msg} (position keeps its bot-side stop)",
            )
            return False
        position.signal_data = {
            **(position.signal_data or {}),
            "catastrophe_stop": {"client_algo_id": cid, "trigger": trigger, "algo_id": resp.get("algoId")},
        }
        await self._db.save_position(position)
        logger.info("Catastrophe stop placed", symbol=position.symbol, trigger=trigger, client_algo_id=cid)
        return True

    async def cancel_catastrophe_stop(self, position: Position) -> None:
        """Cancel the backstop after the bot closed the position. Never raises."""
        cs = (position.signal_data or {}).get("catastrophe_stop") or {}
        cid = cs.get("client_algo_id")
        if not cid or self._cfg.mode != "live":
            return
        try:
            resp = await self._executor.cancel_algo_order(cid)
            if not self._algo_ok(resp):
                # already triggered / finished / unknown: leave nothing of ours on a now-flat symbol
                await self._executor.cancel_all_algo_orders(position.symbol)
                logger.info("Catastrophe stop cancel fell back to symbol sweep", symbol=position.symbol, response=resp)
        except Exception as e:  # noqa: BLE001
            logger.warning("Catastrophe stop cancel failed", symbol=position.symbol, error=str(e))
        finally:
            self._cs_attempts.pop(position.trade_uuid, None)

    async def sync_catastrophe_stops(self, open_positions: list[Position]) -> int:
        """Make sure every bot-managed open position has a live backstop (startup + periodic). Returns placements."""
        if not self._cs_enabled():
            return 0
        placed = 0
        now = datetime.now(timezone.utc)
        for pos in open_positions:
            if pos.externally_managed or pos.state in (PositionState.CLOSED, PositionState.CANCELLED):
                continue
            if pos.entry_time and (now - pos.entry_time).total_seconds() < self._CS_MIN_AGE_S:
                continue  # the entry flow places its own stop
            cid = ((pos.signal_data or {}).get("catastrophe_stop") or {}).get("client_algo_id")
            if cid:
                try:
                    resp = await self._executor.get_algo_order(cid)
                except Exception:  # noqa: BLE001
                    resp = None
                if isinstance(resp, dict) and str(resp.get("algoStatus", "")) in self._CS_ALIVE:
                    continue
            if time.time() - self._cs_attempts.get(pos.trade_uuid, 0.0) < self._CS_RETRY_S:
                continue
            if await self.place_catastrophe_stop(pos):
                placed += 1
        return placed

    async def _handle_partial_fill(self, result: OrderResult) -> OrderResult:
        """Handle partially filled order: wait then cancel remainder."""
        timeout = self._cfg.execution.partial_fill_timeout_s
        logger.info(
            "Partial fill — waiting for completion",
            symbol=result.symbol,
            filled=result.filled_qty,
            requested=result.requested_qty,
            timeout_s=timeout,
        )

        await asyncio.sleep(timeout)

        # Try to cancel remainder
        cancelled = await self._executor.cancel_order(
            result.symbol, result.client_order_id,
        )
        if cancelled:
            logger.info("Remainder cancelled after partial fill")

        return result

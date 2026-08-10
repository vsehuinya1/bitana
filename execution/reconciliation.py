"""
Reconciliation Loop

Compares local state vs Binance positions/orders.
Self-heals mismatches. Detects external positions (AD-7 of original spec).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from config.loader import AppConfig
from core.events import event_bus, Events
from core.logging_setup import get_logger
from core.models import (
    EngineType, Position, PositionState, Side, TradeRecord,
)
from execution.base_executor import BaseExecutor
from execution.position_manager import PositionManager
from storage.database import Database

logger = get_logger("reconciliation")


class ReconciliationManager:
    """Periodic reconciliation between local and exchange state."""

    def __init__(
        self,
        executor: BaseExecutor,
        position_manager: PositionManager,
        config: AppConfig,
        database: Database,
    ) -> None:
        self._executor = executor
        self._pos_mgr = position_manager
        self._cfg = config
        self._db = database

    async def reconcile(self) -> list[TradeRecord]:
        """Run a single reconciliation pass.

        Returns trade records for any local-only ghosts booked as external closes.
        Only symbols missing on the exchange are touched — open exchange positions
        (e.g. SOL/XRP while ETH was manually flat) are left alone.
        """
        closed: list[TradeRecord] = []
        try:
            exchange_positions = await self._executor.get_positions()
            local_positions = self._pos_mgr.get_open_positions()

            # Build lookup maps
            exchange_map: dict[str, dict] = {}
            for ep in exchange_positions:
                sym = ep.get("symbol", "")
                amt = float(ep.get("positionAmt", 0))
                if amt != 0:
                    exchange_map[sym] = ep

            local_map: dict[str, Position] = {}
            for lp in local_positions:
                local_map[lp.symbol] = lp

            # Check for positions on exchange not in local state
            for sym, ep in exchange_map.items():
                if sym not in local_map:
                    await self._handle_external_position(sym, ep)

            # Local open, exchange flat → book external close (manual / liq / external reduce)
            for sym, lp in list(local_map.items()):
                if sym in exchange_map:
                    continue
                trade = await self._handle_local_only_position(lp)
                if trade:
                    closed.append(trade)

            # Check for quantity mismatches on symbols still open both sides
            for sym in set(exchange_map.keys()) & set(local_map.keys()):
                ep = exchange_map[sym]
                lp = local_map[sym]
                if lp.state in (PositionState.CLOSED, PositionState.CANCELLED):
                    continue
                exchange_qty = abs(float(ep.get("positionAmt", 0)))
                local_qty = lp.quantity

                if abs(exchange_qty - local_qty) / max(local_qty, 1e-10) > 0.01:
                    logger.warning(
                        "Quantity mismatch",
                        symbol=sym,
                        exchange_qty=exchange_qty,
                        local_qty=local_qty,
                    )
                    # Update local to match exchange
                    lp.quantity = exchange_qty
                    await self._db.save_position(lp)

            await self._db.set_system_state(
                "last_reconciliation",
                datetime.now(timezone.utc).isoformat(),
            )

        except Exception as e:
            logger.error("Reconciliation failed", error=str(e))

        return closed

    async def _handle_local_only_position(self, pos: Position) -> TradeRecord | None:
        """Clear a ghost local position that is already flat on the exchange.

        No exit order is placed — placing reduce-only against a flat book can
        open a wrong-way position. Only this symbol's local row is closed.
        """
        if pos.state in (PositionState.CLOSED, PositionState.CANCELLED):
            return None

        exit_price, commission, filled_qty = await self._resolve_external_exit(pos)
        logger.warning(
            "Local position not on exchange — booking external close",
            symbol=pos.symbol,
            trade_uuid=pos.trade_uuid,
            exit_price=exit_price,
            filled_qty=filled_qty,
        )
        await event_bus.emit(
            Events.RECONCILIATION_MISMATCH,
            symbol=pos.symbol,
            trade_uuid=pos.trade_uuid,
            mismatch_type="local_only",
        )
        return await self._pos_mgr.record_external_close(
            pos,
            exit_price=exit_price,
            reason="external_close",
            commission=commission,
            filled_qty=filled_qty,
        )

    async def _resolve_external_exit(
        self, pos: Position,
    ) -> tuple[float, float, float]:
        """Best-effort exit price/commission/qty from userTrades, else mark/ticker.

        Returns (exit_price, commission, filled_qty).
        """
        qty = pos.quantity
        rest = getattr(self._executor, "_rest", None)
        if rest is not None:
            filled = await self._exit_from_user_trades(rest, pos)
            if filled is not None:
                return filled

            for getter, key in (
                (rest.get_mark_price, "markPrice"),
                (rest.get_ticker_price, "price"),
            ):
                try:
                    resp = await getter(pos.symbol)
                    price = float(resp.get(key, 0) or 0)
                    if price > 0:
                        return price, 0.0, qty
                except Exception as exc:
                    logger.warning(
                        "Could not fetch fallback exit price",
                        symbol=pos.symbol, source=key, error=str(exc),
                    )

        prices = getattr(self._executor, "_prices", None)
        if isinstance(prices, dict):
            price = float(prices.get(pos.symbol, 0) or 0)
            if price > 0:
                return price, 0.0, qty

        # Last resort: book at entry (0 gross PnL) so the ghost still clears.
        logger.critical(
            "External close falling back to entry price",
            symbol=pos.symbol, trade_uuid=pos.trade_uuid,
        )
        return pos.entry_price, 0.0, qty

    async def _exit_from_user_trades(
        self, rest, pos: Position,
    ) -> tuple[float, float, float] | None:
        """Match recent closing fills for this side/qty after entry."""
        try:
            fills = await rest.get_account_trades(pos.symbol)
        except Exception as exc:
            logger.warning(
                "Could not fetch userTrades for external close",
                symbol=pos.symbol, error=str(exc),
            )
            return None
        if not fills:
            return None

        close_side = "BUY" if pos.side == Side.SHORT else "SELL"
        entry_ms = 0
        if pos.entry_time is not None:
            entry_ms = int(pos.entry_time.timestamp() * 1000)

        candidates = [
            f for f in fills
            if str(f.get("side", "")).upper() == close_side
            and int(f.get("time", 0) or 0) >= entry_ms
        ]
        candidates.sort(key=lambda f: int(f.get("time", 0) or 0), reverse=True)

        need = pos.quantity
        got = 0.0
        quote = 0.0
        commission = 0.0
        commission_ok = True
        used: list[dict] = []
        for fill in candidates:
            fq = float(fill.get("qty", 0) or 0)
            if fq <= 0:
                continue
            take = min(fq, need - got)
            px = float(fill.get("price", 0) or 0)
            qq = float(fill.get("quoteQty", 0) or 0)
            if qq <= 0 and px > 0:
                qq = px * fq
            # Scale quote/commission if we only take part of a fill row.
            scale = take / fq if fq > 0 else 0.0
            got += take
            quote += qq * scale
            if fill.get("commissionAsset") == "USDT":
                commission += float(fill.get("commission", 0) or 0) * scale
            else:
                commission_ok = False
            used.append(fill)
            if got >= need * 0.999999:
                break

        if got < need * 0.99 or quote <= 0:
            return None

        exit_price = quote / got
        if not commission_ok:
            commission = quote * 0.0004
        return exit_price, commission, got

    async def _handle_external_position(self, symbol: str, ep: dict) -> None:
        """Handle position detected on exchange but not in local state."""
        amt = float(ep.get("positionAmt", 0))
        side = Side.LONG if amt > 0 else Side.SHORT
        entry = float(ep.get("entryPrice", 0))
        qty = abs(amt)

        logger.critical(
            "EXTERNAL POSITION DETECTED",
            symbol=symbol,
            side=side.value,
            qty=qty,
            entry=entry,
        )

        # Create tracking position with conservative stop
        trade_uuid = f"EXT_{uuid.uuid4().hex[:12]}"
        # Conservative stop = entry ± 2x estimated ATR (use 2% as fallback)
        buffer = entry * 0.02
        stop = entry - buffer if side == Side.LONG else entry + buffer

        pos = Position(
            trade_uuid=trade_uuid,
            symbol=symbol,
            side=side,
            engine=EngineType.COMPRESSION,  # placeholder
            state=PositionState.DETECTED,
            entry_price=entry,
            entry_time=datetime.now(timezone.utc),
            quantity=qty,
            stop_price=stop,
            initial_stop=stop,
            externally_managed=True,
        )
        pos.transition_to(PositionState.TRACKING)

        await self._pos_mgr.add_position(pos)

        await event_bus.emit(
            Events.EXTERNAL_POSITION_DETECTED,
            symbol=symbol,
            side=side.value,
            quantity=qty,
            entry_price=entry,
        )

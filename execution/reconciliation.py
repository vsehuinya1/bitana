"""
Reconciliation Loop

Compares local state vs Binance positions/orders.
Self-heals mismatches. Detects external positions (AD-7 of original spec).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from config.loader import AppConfig
from core.events import event_bus, Events
from core.logging_setup import get_logger
from core.models import (
    EngineType, Position, PositionState, Side,
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

    async def reconcile(self) -> None:
        """Run a single reconciliation pass."""
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

            # Check for local positions not on exchange
            for sym, lp in local_map.items():
                if sym not in exchange_map:
                    logger.warning(
                        "Local position not on exchange — may have been liquidated",
                        symbol=sym,
                        trade_uuid=lp.trade_uuid,
                    )
                    await event_bus.emit(
                        Events.RECONCILIATION_MISMATCH,
                        symbol=sym,
                        trade_uuid=lp.trade_uuid,
                        mismatch_type="local_only",
                    )

            # Check for quantity mismatches
            for sym in set(exchange_map.keys()) & set(local_map.keys()):
                ep = exchange_map[sym]
                lp = local_map[sym]
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

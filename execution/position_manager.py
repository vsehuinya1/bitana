"""
Position Manager

Stop/TP/trailing management, time stops, funding fee tracking.
Implements position lifecycle state machine (AD-4).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from config.loader import AppConfig
from core.logging_setup import get_logger
from core.models import (
    Candle, EngineType, Position, PositionState, Side, TradeRecord,
)
from execution.order_manager import OrderManager
from storage.database import Database

logger = get_logger("position_manager")


def _atr_from_candles(candles: list[Candle], period: int = 14) -> float:
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        prev = candles[i - 1].close
        c = candles[i]
        tr = max(c.high - c.low, abs(c.high - prev), abs(c.low - prev))
        trs.append(tr)
    if len(trs) < period:
        return sum(trs) / len(trs) if trs else 0.0
    atr = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[i]) / period
    return atr


class PositionManager:
    """Manages open positions: stops, TP, trailing, time stops."""

    def __init__(
        self,
        order_manager: OrderManager,
        config: AppConfig,
        database: Database,
    ) -> None:
        self._orders = order_manager
        self._cfg = config
        self._db = database
        self._positions: dict[str, Position] = {}

    @property
    def positions(self) -> dict[str, Position]:
        return self._positions

    def get_open_positions(self) -> list[Position]:
        return [
            p for p in self._positions.values()
            if p.state not in (PositionState.CLOSED, PositionState.CANCELLED)
        ]

    async def add_position(self, position: Position) -> None:
        self._positions[position.trade_uuid] = position
        await self._db.save_position(position)

    async def remove_position(self, trade_uuid: str) -> Optional[Position]:
        return self._positions.pop(trade_uuid, None)

    async def recover_positions(self) -> None:
        """Recover positions from database on startup."""
        rows = await self._db.get_open_positions()
        for row in rows:
            raw_signal = row.get("signal_data") or "{}"
            try:
                signal_data = json.loads(raw_signal) if isinstance(raw_signal, str) else raw_signal
            except json.JSONDecodeError:
                signal_data = {}

            pos = Position(
                trade_uuid=row["trade_uuid"],
                symbol=row["symbol"],
                side=Side(row["side"]),
                engine=EngineType(row["engine"]),
                state=PositionState(row["state"]),
                entry_price=row["entry_price"],
                entry_time=(
                    datetime.fromisoformat(row["entry_time"])
                    if row.get("entry_time") else None
                ),
                quantity=row["quantity"],
                leverage=row["leverage"],
                stop_price=row["stop_price"],
                initial_stop=row["initial_stop"],
                risk_r=row["risk_r"],
                tp1_price=row["tp1_price"],
                tp1_hit=bool(row["tp1_hit"]),
                trailing_stop=row["trailing_stop"],
                trailing_active=bool(row["trailing_active"]),
                realized_pnl=row["realized_pnl"],
                commission_total=row["commission_total"],
                funding_fees=row["funding_fees"],
                candles_held=row["candles_held"],
                externally_managed=bool(row["externally_managed"]),
                client_order_ids=json.loads(row.get("client_order_ids", "[]")),
                signal_data=signal_data,
                entry_atr=float(row.get("entry_atr") or signal_data.get("entry_atr") or 0.0),
                peak_mfe_atr=float(row.get("peak_mfe_atr") or 0.0),
            )
            self._positions[pos.trade_uuid] = pos

        if rows:
            logger.info("Recovered positions from DB", count=len(rows))

    def _path_atr(self, pos: Position, candle: Candle) -> tuple[float, float]:
        """Return (favorable, adverse) excursion in ATR units for this bar."""
        entry = pos.entry_price
        atr = pos.entry_atr or 0.0
        if atr <= 0:
            atr = _atr_from_candles([], 14) or 1.0
        if pos.side == Side.LONG:
            return (candle.high - entry) / atr, (candle.low - entry) / atr
        return (entry - candle.low) / atr, (entry - candle.high) / atr

    async def _manage_burst_follow(
        self,
        pos: Position,
        candle: Candle,
        candles_5m: list[Candle],
    ) -> Optional[TradeRecord]:
        """Shadow-aligned exits: stop, optional TP, optional TSL, time exit."""
        sd = pos.signal_data or {}
        entry = pos.entry_price
        atr = pos.entry_atr or float(sd.get("entry_atr") or 0.0)
        if atr <= 0:
            atr = _atr_from_candles(candles_5m, 14)
        if atr <= 0:
            return None

        if pos.side == Side.LONG:
            fav = (candle.high - entry) / atr
        else:
            fav = (entry - candle.low) / atr
        pos.peak_mfe_atr = max(pos.peak_mfe_atr, fav)

        time_bars = int(sd.get("time_bars", 36))
        skip_tp = bool(sd.get("time_exit_only", False))
        tp_atr = float(sd.get("tp_atr", 3.0))

        trail_atr = sd.get("trail_atr")
        trail_trigger = sd.get("trail_trigger_r")
        use_trail = trail_atr is not None and trail_trigger is not None

        effective_sl = pos.stop_price
        trail_active = use_trail and pos.peak_mfe_atr >= float(trail_trigger)
        if trail_active:
            ta = float(trail_atr)
            if pos.side == Side.LONG:
                trail_sl = entry + (pos.peak_mfe_atr - ta) * atr
                effective_sl = max(pos.stop_price, trail_sl)
            else:
                trail_sl = entry - (pos.peak_mfe_atr - ta) * atr
                effective_sl = min(pos.stop_price, trail_sl)

        exit_price = 0.0
        exit_reason = None

        if pos.side == Side.LONG:
            if candle.low <= effective_sl:
                exit_price = effective_sl
                exit_reason = "trail" if trail_active and effective_sl > pos.initial_stop else "stop_loss"
            elif not skip_tp and tp_atr < 900 and candle.high >= entry + tp_atr * atr:
                exit_price = entry + tp_atr * atr
                exit_reason = "take_profit"
        else:
            if candle.high >= effective_sl:
                exit_price = effective_sl
                exit_reason = "trail" if trail_active and effective_sl < pos.initial_stop else "stop_loss"
            elif not skip_tp and tp_atr < 900 and candle.low <= entry - tp_atr * atr:
                exit_price = entry - tp_atr * atr
                exit_reason = "take_profit"

        if exit_reason is None and pos.candles_held >= time_bars:
            exit_price = candle.close
            exit_reason = "time_exit"

        if exit_reason is None:
            if trail_active:
                pos.trailing_active = True
                pos.trailing_stop = effective_sl
                pos.stop_price = effective_sl
                pos.transition_to(PositionState.TRAILING)
            return None

        return await self._close_position(pos, exit_price, exit_reason)

    async def manage_on_candle_close(
        self,
        symbol: str,
        candle: Candle,
        candles_5m: list[Candle],
    ) -> list[TradeRecord]:
        """Called on each 5m candle close. Manage stops, TP, trailing, time stops."""
        closed_trades: list[TradeRecord] = []
        cfg = self._cfg.profit_taking

        for pos in list(self._positions.values()):
            if pos.symbol != symbol:
                continue
            if pos.state in (PositionState.CLOSED, PositionState.CANCELLED):
                continue
            if pos.externally_managed:
                continue

            pos.candles_held += 1

            sd = pos.signal_data or {}
            if pos.engine == EngineType.LIQ_BURST_FOLLOW:
                trade = await self._manage_burst_follow(pos, candle, candles_5m)
                if trade:
                    closed_trades.append(trade)
                else:
                    await self._db.save_position(pos)
                continue

            current_price = candle.close
            entry = pos.entry_price
            stop_dist = abs(entry - pos.initial_stop)
            r_multiple = 0.0
            if stop_dist > 0:
                if pos.side == Side.LONG:
                    r_multiple = (current_price - entry) / stop_dist
                else:
                    r_multiple = (entry - current_price) / stop_dist

            if (
                pos.candles_held >= cfg.time_stop_candles
                and r_multiple < cfg.time_stop_r_threshold
                and not pos.tp1_hit
            ):
                trade = await self._close_position(pos, current_price, "time_stop")
                if trade:
                    closed_trades.append(trade)
                continue

            stop_hit = False
            if pos.side == Side.LONG:
                if candle.low <= pos.stop_price:
                    stop_hit = True
            else:
                if candle.high >= pos.stop_price:
                    stop_hit = True

            if stop_hit:
                trade = await self._close_position(pos, pos.stop_price, "stop_loss")
                if trade:
                    closed_trades.append(trade)
                continue

            if not pos.tp1_hit and r_multiple >= cfg.partial_close_r:
                tp_qty = pos.quantity * cfg.partial_close_pct
                result = await self._orders.execute_exit(pos, tp_qty, reason="partial_tp")
                if result and result.filled_qty > 0:
                    pos.tp1_hit = True
                    pos.quantity -= result.filled_qty
                    pos.realized_pnl += self._calc_pnl(
                        pos, result.avg_fill_price, result.filled_qty
                    )
                    pos.commission_total += result.commission
                    pos.trailing_active = True
                    pos.transition_to(PositionState.PARTIAL_TP)
                    logger.info(
                        "TP1 hit",
                        trade_uuid=pos.trade_uuid,
                        r=round(r_multiple, 2),
                    )

            if pos.trailing_active:
                atr = _atr_from_candles(candles_5m, 14)
                trail_dist = atr * cfg.trail_atr_multiplier

                if pos.side == Side.LONG:
                    new_trail = current_price - trail_dist
                    if new_trail > pos.trailing_stop:
                        pos.trailing_stop = new_trail
                        if pos.trailing_stop > pos.stop_price:
                            pos.stop_price = pos.trailing_stop
                else:
                    new_trail = current_price + trail_dist
                    if pos.trailing_stop == 0 or new_trail < pos.trailing_stop:
                        pos.trailing_stop = new_trail
                        if pos.trailing_stop < pos.stop_price or pos.stop_price == 0:
                            pos.stop_price = pos.trailing_stop

                pos.transition_to(PositionState.TRAILING)

            await self._db.save_position(pos)

        return closed_trades

    async def _close_position(
        self, pos: Position, exit_price: float, reason: str,
    ) -> Optional[TradeRecord]:
        """Fully close a position and create trade record."""
        result = await self._orders.execute_exit(pos, pos.quantity, reason=reason)
        if not result:
            return None

        actual_exit = result.avg_fill_price if result.avg_fill_price > 0 else exit_price
        exit_slippage_bps = 0.0
        if exit_price > 0 and actual_exit > 0:
            exit_slippage_bps = abs(actual_exit - exit_price) / exit_price * 10000
        entry_slip = float((pos.signal_data or {}).get("entry_slippage_bps") or 0.0)
        pnl = self._calc_pnl(pos, actual_exit, result.filled_qty)
        total_pnl = pos.realized_pnl + pnl
        total_commission = pos.commission_total + result.commission

        hold_time = 0.0
        if pos.entry_time:
            hold_time = (datetime.now(timezone.utc) - pos.entry_time).total_seconds()

        stop_dist = abs(pos.entry_price - pos.initial_stop)
        net_pnl = total_pnl - total_commission - pos.funding_fees
        initial_risk = stop_dist * result.filled_qty
        pnl_r = net_pnl / initial_risk if initial_risk > 0 else 0.0

        trade = TradeRecord(
            trade_uuid=pos.trade_uuid,
            engine=pos.engine,
            symbol=pos.symbol,
            side=pos.side,
            entry_price=pos.entry_price,
            exit_price=actual_exit,
            quantity=result.filled_qty,
            leverage=pos.leverage,
            initial_stop=pos.initial_stop,
            commission=total_commission,
            slippage_est=entry_slip + exit_slippage_bps,
            funding_fees=pos.funding_fees,
            pnl_usd=net_pnl,
            pnl_r=pnl_r,
            hold_time_s=hold_time,
            hold_candles=pos.candles_held,
            exit_reason=reason,
            signal_data={
                **(pos.signal_data or {}),
                "exit_trigger_price": exit_price,
                "exit_slippage_bps": exit_slippage_bps,
            },
        )

        pos.transition_to(PositionState.CLOSING)
        pos.transition_to(PositionState.CLOSED)
        await self._db.save_position(pos)
        await self._db.save_trade(trade)

        logger.info(
            "Position closed",
            trade_uuid=pos.trade_uuid,
            symbol=pos.symbol, reason=reason,
            pnl_usd=round(trade.pnl_usd, 2),
            pnl_r=round(trade.pnl_r, 2),
            exit_slippage_bps=round(exit_slippage_bps, 2),
            total_slippage_bps=round(trade.slippage_est, 2),
        )
        return trade

    def _calc_pnl(self, pos: Position, exit_price: float, qty: float) -> float:
        if pos.side == Side.LONG:
            return (exit_price - pos.entry_price) * qty
        return (pos.entry_price - exit_price) * qty

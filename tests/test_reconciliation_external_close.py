"""Tests for reconciliation local-only (manual flat) healing."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from core.models import EngineType, Position, PositionState, Side
from execution.position_manager import PositionManager
from execution.reconciliation import ReconciliationManager


def _pos(
    symbol: str = "ETHUSDT",
    side: Side = Side.SHORT,
    qty: float = 0.076,
    entry: float = 1884.46,
    stop: float = 1904.42,
) -> Position:
    pos = Position(
        trade_uuid="ghost-eth",
        symbol=symbol,
        side=side,
        engine=EngineType.LIQ_BURST_FOLLOW,
        state=PositionState.MANAGING,
        entry_price=entry,
        entry_time=datetime(2026, 7, 24, 6, 20, tzinfo=timezone.utc),
        quantity=qty,
        stop_price=stop,
        initial_stop=stop,
        leverage=3,
        signal_data={"shadow_strategy": "asia_pump_short_4h"},
    )
    return pos


def test_record_external_close_does_not_place_order(sample_config):
    orders = MagicMock()
    orders.execute_exit = AsyncMock()
    db = MagicMock()
    db.save_position = AsyncMock()
    db.save_trade = AsyncMock()
    mgr = PositionManager(orders, sample_config, db)
    pos = _pos()
    mgr._positions[pos.trade_uuid] = pos

    trade = asyncio.run(
        mgr.record_external_close(pos, exit_price=1888.0, commission=0.05)
    )

    orders.execute_exit.assert_not_awaited()
    assert trade is not None
    assert trade.exit_reason == "external_close"
    assert trade.exit_price == 1888.0
    assert trade.quantity == 0.076
    assert pos.state == PositionState.CLOSED
    # SHORT: entry 1884.46 -> exit 1888 = small loss
    assert trade.pnl_usd < 0
    db.save_trade.assert_awaited_once()


def test_reconcile_closes_only_local_only_ghost(sample_config):
    eth = _pos("ETHUSDT")
    sol = _pos("SOLUSDT", qty=1.01, entry=76.21, stop=77.68)
    sol.trade_uuid = "live-sol"
    xrp = _pos("XRPUSDT", qty=72.8, entry=1.1187, stop=1.139)
    xrp.trade_uuid = "live-xrp"

    orders = MagicMock()
    orders.execute_exit = AsyncMock()
    db = MagicMock()
    db.save_position = AsyncMock()
    db.save_trade = AsyncMock()
    db.set_system_state = AsyncMock()
    pos_mgr = PositionManager(orders, sample_config, db)
    for p in (eth, sol, xrp):
        pos_mgr._positions[p.trade_uuid] = p

    rest = MagicMock()
    rest.get_account_trades = AsyncMock(return_value=[
        {
            "side": "BUY",
            "qty": "0.076",
            "price": "1887.5",
            "quoteQty": str(1887.5 * 0.076),
            "commission": "0.04",
            "commissionAsset": "USDT",
            "time": int(datetime(2026, 7, 24, 8, 50, tzinfo=timezone.utc).timestamp() * 1000),
        },
    ])
    rest.get_mark_price = AsyncMock()
    rest.get_ticker_price = AsyncMock()

    executor = MagicMock()
    executor._rest = rest
    # Exchange still has SOL + XRP; ETH is flat (absent)
    executor.get_positions = AsyncMock(return_value=[
        {"symbol": "SOLUSDT", "positionAmt": "-1.01", "entryPrice": "76.21"},
        {"symbol": "XRPUSDT", "positionAmt": "-72.8", "entryPrice": "1.1187"},
    ])

    recon = ReconciliationManager(executor, pos_mgr, sample_config, db)
    closed = asyncio.run(recon.reconcile())

    assert len(closed) == 1
    assert closed[0].symbol == "ETHUSDT"
    assert closed[0].exit_reason == "external_close"
    assert abs(closed[0].exit_price - 1887.5) < 1e-9
    assert eth.state == PositionState.CLOSED
    assert sol.state == PositionState.MANAGING
    assert xrp.state == PositionState.MANAGING
    assert sol.quantity == 1.01
    assert xrp.quantity == 72.8
    orders.execute_exit.assert_not_awaited()
    rest.get_mark_price.assert_not_awaited()


def test_reconcile_leaves_matching_books_alone(sample_config):
    eth = _pos("ETHUSDT")
    orders = MagicMock()
    orders.execute_exit = AsyncMock()
    db = MagicMock()
    db.save_position = AsyncMock()
    db.save_trade = AsyncMock()
    db.set_system_state = AsyncMock()
    pos_mgr = PositionManager(orders, sample_config, db)
    pos_mgr._positions[eth.trade_uuid] = eth

    executor = MagicMock()
    executor.get_positions = AsyncMock(return_value=[
        {"symbol": "ETHUSDT", "positionAmt": "-0.076", "entryPrice": "1884.46"},
    ])

    recon = ReconciliationManager(executor, pos_mgr, sample_config, db)
    closed = asyncio.run(recon.reconcile())

    assert closed == []
    assert eth.state == PositionState.MANAGING
    orders.execute_exit.assert_not_awaited()

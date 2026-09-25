"""Exchange-resident catastrophe stop (2026-09-25): Algo-service STOP_MARKET closePosition backstop.

Covers trigger math, the OFF switches, clear-then-place at entry, cancel after close (code "200" success shape,
sweep fallback), periodic sync (alive / missing / young / external / rate limit), the PositionManager close hook,
and the exact REST parameters. No network: executor, REST transport, DB and alerts are mocked.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from core.models import EngineType, Position, PositionState, Side
from data.binance_rest import BinanceRestClient
from execution.live_executor import LiveExecutor
from execution.order_manager import OrderManager
from execution.paper_executor import PaperExecutor
from execution.position_manager import PositionManager

UUID = "11111111-2222-3333-4444-555555555555"


def _run(coro):
    """Private loop per call: _run() would clear the default loop and break later get_event_loop() tests."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _cfg(sample_config, mult=1.5, mode="live"):
    cfg = sample_config.model_copy(deep=True)
    cfg.mode = mode
    cfg.execution.catastrophe_stop_mult = mult
    return cfg


def _om(cfg, place_resp=None, cancel_resp=None, query_resp=None):
    ex = MagicMock()
    ex.place_algo_stop = AsyncMock(
        return_value=place_resp if place_resp is not None else {"algoId": 7, "algoStatus": "NEW"},
    )
    ex.cancel_algo_order = AsyncMock(
        return_value=cancel_resp if cancel_resp is not None else {"algoId": 7, "code": "200", "msg": "success"},
    )
    ex.cancel_all_algo_orders = AsyncMock(return_value={"code": 200, "msg": "done"})
    ex.get_algo_order = AsyncMock(return_value=query_resp)
    si = MagicMock()
    si.round_price.side_effect = lambda symbol, price: round(price, 4)
    db = MagicMock()
    db.save_position = AsyncMock()
    db.save_order = AsyncMock()
    alerts = MagicMock()
    alerts.critical = AsyncMock()
    return OrderManager(ex, si, cfg, db, alerts), ex, db, alerts


def _pos(side=Side.LONG, entry=100.0, stop=94.0, age_s=600, cs=None, ext=False, uuid=UUID, symbol="SOLUSDT"):
    return Position(
        trade_uuid=uuid, symbol=symbol, side=side, engine=EngineType.LIQ_BURST_FOLLOW,
        state=PositionState.MANAGING, entry_price=entry,
        entry_time=datetime.now(timezone.utc) - timedelta(seconds=age_s),
        quantity=1.0, stop_price=stop, initial_stop=stop, leverage=5,
        signal_data={} if cs is None else {"catastrophe_stop": cs}, externally_managed=ext,
    )


def test_trigger_long_and_short(sample_config):
    om, *_ = _om(_cfg(sample_config))
    assert om.catastrophe_trigger(_pos(Side.LONG, 100.0, 94.0)) == 91.0      # 100 - 1.5 x 6
    assert om.catastrophe_trigger(_pos(Side.SHORT, 100.0, 106.0)) == 109.0   # 100 + 1.5 x 6


def test_off_when_mult_zero_or_not_live(sample_config):
    for cfg in (_cfg(sample_config, mult=0.0), _cfg(sample_config, mode="paper")):
        om, ex, *_ = _om(cfg)
        assert _run(om.place_catastrophe_stop(_pos())) is False
        assert _run(om.sync_catastrophe_stops([_pos()])) == 0
        ex.place_algo_stop.assert_not_awaited()
        ex.cancel_all_algo_orders.assert_not_awaited()


def test_place_clears_symbol_then_places_close_position_stop(sample_config):
    om, ex, db, _ = _om(_cfg(sample_config))
    calls = []
    ex.cancel_all_algo_orders.side_effect = lambda s: calls.append(("clear", s)) or {"code": 200}
    ex.place_algo_stop.side_effect = lambda *a: calls.append(("place", a[0])) or {"algoId": 9, "algoStatus": "NEW"}
    pos = _pos()
    assert _run(om.place_catastrophe_stop(pos)) is True
    assert calls == [("clear", "SOLUSDT"), ("place", "SOLUSDT")]
    symbol, side, trigger, cid, working = ex.place_algo_stop.call_args.args
    assert (symbol, side, trigger, working) == ("SOLUSDT", "SELL", 91.0, "MARK_PRICE")
    assert cid.startswith("BITANA_CS_") and re.fullmatch(r"[\.A-Z\:/a-z0-9_-]{1,36}", cid)
    assert pos.signal_data["catastrophe_stop"] == {"client_algo_id": cid, "trigger": 91.0, "algo_id": 9}
    db.save_position.assert_awaited_once_with(pos)


def test_short_position_uses_buy_side(sample_config):
    om, ex, *_ = _om(_cfg(sample_config))
    assert _run(om.place_catastrophe_stop(_pos(Side.SHORT, 100.0, 106.0))) is True
    assert ex.place_algo_stop.call_args.args[1:3] == ("BUY", 109.0)


def test_rejection_alerts_and_stores_nothing(sample_config):
    om, _, db, alerts = _om(_cfg(sample_config), place_resp={"code": -4120, "msg": "STOP_ORDER_SWITCH_ALGO"})
    pos = _pos()
    assert _run(om.place_catastrophe_stop(pos)) is False
    alerts.critical.assert_awaited_once()
    assert "catastrophe_stop" not in pos.signal_data
    db.save_position.assert_not_awaited()


def test_placement_exception_is_contained(sample_config):
    om, ex, _, alerts = _om(_cfg(sample_config))
    ex.place_algo_stop.side_effect = RuntimeError("socket closed")
    assert _run(om.place_catastrophe_stop(_pos())) is False
    alerts.critical.assert_awaited_once()


def test_externally_managed_position_is_left_alone(sample_config):
    om, ex, *_ = _om(_cfg(sample_config))
    assert _run(om.place_catastrophe_stop(_pos(ext=True))) is False
    ex.place_algo_stop.assert_not_awaited()


def test_cancel_success_code_200_needs_no_sweep(sample_config):
    om, ex, *_ = _om(_cfg(sample_config))
    _run(om.cancel_catastrophe_stop(_pos(cs={"client_algo_id": "BITANA_CS_x"})))
    ex.cancel_algo_order.assert_awaited_once_with("BITANA_CS_x")
    ex.cancel_all_algo_orders.assert_not_awaited()


def test_cancel_failure_sweeps_the_symbol(sample_config):
    om, ex, *_ = _om(_cfg(sample_config), cancel_resp={"code": -2011, "msg": "Unknown order sent."})
    _run(om.cancel_catastrophe_stop(_pos(cs={"client_algo_id": "BITANA_CS_x"})))
    ex.cancel_all_algo_orders.assert_awaited_once_with("SOLUSDT")


def test_cancel_without_stop_is_noop(sample_config):
    om, ex, *_ = _om(_cfg(sample_config))
    _run(om.cancel_catastrophe_stop(_pos()))
    ex.cancel_algo_order.assert_not_awaited()
    ex.cancel_all_algo_orders.assert_not_awaited()


def test_sync_places_only_where_needed(sample_config):
    om, ex, *_ = _om(_cfg(sample_config), query_resp={"algoStatus": "NEW"})
    alive = _pos(uuid="a" * 36, cs={"client_algo_id": "BITANA_CS_alive"}, symbol="ETHUSDT")
    missing = _pos(uuid="b" * 36, symbol="XRPUSDT")
    young = _pos(uuid="c" * 36, age_s=10, symbol="ADAUSDT")
    external = _pos(uuid="d" * 36, ext=True, symbol="ZECUSDT")
    assert _run(om.sync_catastrophe_stops([alive, missing, young, external])) == 1
    assert [c.args[0] for c in ex.place_algo_stop.call_args_list] == ["XRPUSDT"]


def test_sync_replaces_finished_stop_and_rate_limits_retries(sample_config):
    om, ex, *_ = _om(_cfg(sample_config), query_resp={"algoStatus": "FINISHED"},
                     place_resp={"code": -1001, "msg": "Internal error"})
    pos = _pos(cs={"client_algo_id": "BITANA_CS_old"})
    assert _run(om.sync_catastrophe_stops([pos])) == 0     # attempted, rejected
    assert _run(om.sync_catastrophe_stops([pos])) == 0     # within 10 min: no retry
    assert ex.place_algo_stop.await_count == 1


def _pm(sample_config, cancel_side_effect=None):
    orders = MagicMock()
    orders.execute_exit = AsyncMock()
    orders.cancel_catastrophe_stop = AsyncMock(side_effect=cancel_side_effect)
    db = MagicMock()
    db.save_position = AsyncMock()
    db.save_trade = AsyncMock()
    return PositionManager(orders, sample_config, db), orders


def test_close_cancels_catastrophe_stop(sample_config):
    mgr, orders = _pm(sample_config)
    pos = _pos(cs={"client_algo_id": "BITANA_CS_x"})
    trade = _run(mgr.record_external_close(pos, exit_price=95.0))
    assert trade is not None
    orders.cancel_catastrophe_stop.assert_awaited_once_with(pos)


def test_close_accounting_survives_cancel_error(sample_config):
    mgr, _ = _pm(sample_config, cancel_side_effect=RuntimeError("boom"))
    trade = _run(mgr.record_external_close(_pos(), exit_price=95.0))
    assert trade is not None and trade.exit_reason == "external_close"


def test_rest_algo_order_parameters():
    rest = BinanceRestClient()
    rest._request = AsyncMock(return_value={"algoId": 1})
    _run(rest.place_algo_order(
        symbol="SOLUSDT", side="SELL", order_type="STOP_MARKET", trigger_price=91.0,
        client_algo_id="BITANA_CS_x",
    ))
    method, path = rest._request.call_args.args
    params = rest._request.call_args.kwargs["params"]
    assert (method, path) == ("POST", "/fapi/v1/algoOrder")
    assert params == {
        "algoType": "CONDITIONAL", "symbol": "SOLUSDT", "side": "SELL", "type": "STOP_MARKET",
        "triggerPrice": "91.0", "workingType": "MARK_PRICE", "priceProtect": "true",
        "closePosition": "true", "clientAlgoId": "BITANA_CS_x",
    }
    assert rest._request.call_args.kwargs["is_order"] is True
    _run(rest.cancel_algo_order("BITANA_CS_x"))
    assert rest._request.call_args.args == ("DELETE", "/fapi/v1/algoOrder")
    _run(rest.cancel_all_algo_orders("SOLUSDT"))
    assert rest._request.call_args.args == ("DELETE", "/fapi/v1/algoOpenOrders")


def test_live_executor_passes_close_position_stop(sample_config):
    rest = MagicMock()
    rest.place_algo_order = AsyncMock(return_value={"algoId": 1})
    ex = LiveExecutor(rest, MagicMock(), sample_config)
    _run(ex.place_algo_stop("SOLUSDT", "SELL", 91.0, "BITANA_CS_x", "MARK_PRICE"))
    kw = rest.place_algo_order.call_args.kwargs
    assert kw["order_type"] == "STOP_MARKET" and kw["close_position"] is True and kw["trigger_price"] == 91.0


def test_paper_executor_never_places_exchange_stops(sample_config):
    ex = PaperExecutor(sample_config)
    assert _run(ex.place_algo_stop("SOLUSDT", "SELL", 91.0, "BITANA_CS_x", "MARK_PRICE")) is None

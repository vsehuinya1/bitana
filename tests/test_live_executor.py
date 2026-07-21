import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.models import OrderRequest, OrderStatus, Side
from execution.live_executor import LiveExecutor


def _executor(sample_config, responses):
    rest = MagicMock()
    rest.place_order = AsyncMock(return_value=responses[0])
    rest.get_order = AsyncMock(
        side_effect=responses[1:] if len(responses) > 1 else [],
    )
    symbol_info = MagicMock()
    symbol_info.round_quantity.return_value = 0.03
    symbol_info.round_price.return_value = 0.0
    symbol_info.validate_order.return_value = (True, "")
    return LiveExecutor(rest, symbol_info, sample_config), rest


def _market_request():
    return OrderRequest(
        trade_uuid="trade-1",
        client_order_id="BITANA_test",
        symbol="ZECUSDT",
        side=Side.SHORT,
        quantity=0.03,
        order_type="MARKET",
    )


def test_market_entry_requests_result_response(sample_config):
    response = {
        "orderId": 1,
        "status": "FILLED",
        "executedQty": "0.030",
        "avgPrice": "554.15",
        "cumQuote": "16.6245",
    }
    executor, rest = _executor(sample_config, [response])

    result = asyncio.run(executor.place_order(_market_request()))

    assert result.status == OrderStatus.FILLED
    assert result.filled_qty == 0.03
    assert result.avg_fill_price == pytest.approx(554.15)
    assert rest.place_order.await_args.kwargs["new_order_resp_type"] == "RESULT"
    rest.get_order.assert_not_awaited()


def test_market_entry_refreshes_new_ack_until_filled(sample_config):
    ack = {
        "orderId": 803604861540,
        "status": "NEW",
        "executedQty": "0.000",
        "avgPrice": "0.00",
    }
    filled = {
        "orderId": 803604861540,
        "status": "FILLED",
        "executedQty": "0.030",
        "avgPrice": "0.00",
        "cumQuote": "16.6245",
    }
    executor, rest = _executor(sample_config, [ack, filled])

    with patch("execution.live_executor.asyncio.sleep", new=AsyncMock()):
        result = asyncio.run(executor.place_order(_market_request()))

    assert result.status == OrderStatus.FILLED
    assert result.filled_qty == 0.03
    assert result.avg_fill_price == pytest.approx(554.15)
    rest.get_order.assert_awaited_once_with(
        "ZECUSDT",
        order_id=803604861540,
        client_order_id=None,
    )

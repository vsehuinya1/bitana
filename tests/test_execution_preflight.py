import asyncio
from unittest.mock import AsyncMock, MagicMock

from core.models import EngineType, Side, Signal
from data.binance_rest import BinanceRestClient
from execution.order_manager import OrderManager
from tg_bot.alerts import TelegramAlerts


def test_order_permission_uses_no_fill_endpoint():
    client = BinanceRestClient("key", "secret", testnet=False)
    client._request = AsyncMock(return_value={})

    ok, response = asyncio.run(client.test_order_permission())

    assert ok is True
    assert response == {}
    client._request.assert_awaited_once()
    args, kwargs = client._request.await_args
    assert args == ("POST", "/fapi/v1/order/test")
    assert kwargs["signed"] is True
    assert kwargs["is_order"] is True


def test_order_permission_surfaces_exchange_rejection():
    client = BinanceRestClient("key", "secret", testnet=False)
    rejection = {"code": -2015, "msg": "Invalid API-key or permissions"}
    client._request = AsyncMock(return_value=rejection)

    ok, response = asyncio.run(client.test_order_permission())

    assert ok is False
    assert response == rejection


def test_entry_stops_and_alerts_when_leverage_is_rejected(sample_config):
    config = sample_config.model_copy(deep=True)
    config.mode = "live"
    executor = MagicMock()
    executor.set_leverage = AsyncMock(return_value=False)
    executor.place_order = AsyncMock()
    symbol_info = MagicMock()
    symbol_info.round_quantity.return_value = 1.0
    database = MagicMock()
    database.save_order = AsyncMock()
    alerts = MagicMock()
    alerts.critical = AsyncMock(return_value=True)
    manager = OrderManager(executor, symbol_info, config, database, alerts)
    signal = Signal(
        engine=EngineType.LIQ_BURST_FOLLOW,
        symbol="SOLUSDT",
        side=Side.SHORT,
        entry_price=100.0,
        stop_price=110.0,
    )

    result = asyncio.run(manager.execute_entry(signal, quantity=1.0, leverage=2))

    assert result is None
    executor.place_order.assert_not_awaited()
    alerts.critical.assert_awaited_once()


def test_telegram_preflight_sends_real_message():
    alerts = TelegramAlerts("token", "chat")
    alerts._bot = MagicMock()
    alerts._bot.get_chat = AsyncMock(return_value={})
    alerts._bot.send_message = AsyncMock(return_value={})

    ok = asyncio.run(alerts.verify())

    assert ok is True
    alerts._bot.get_chat.assert_awaited_once_with(chat_id="chat")
    alerts._bot.send_message.assert_awaited_once()

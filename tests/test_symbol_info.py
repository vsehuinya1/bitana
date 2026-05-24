"""
Tests for Symbol Info (precision/filter layer).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from data.symbol_info import SymbolInfoManager


@pytest.fixture
def sym_info():
    mgr = SymbolInfoManager()
    # Mock exchange info
    exchange_info = {
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "1000"},
                    {"filterType": "MIN_NOTIONAL", "notional": "5"},
                ],
            },
            {
                "symbol": "SOLUSDT",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.0100"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.1", "minQty": "0.1", "maxQty": "100000"},
                    {"filterType": "MIN_NOTIONAL", "notional": "5"},
                ],
            },
        ]
    }
    mgr.load_from_exchange_info(exchange_info)
    return mgr


class TestPriceRounding:
    def test_btc_price_rounds_to_tick(self, sym_info):
        assert sym_info.round_price("BTCUSDT", 67543.67) == 67543.6

    def test_sol_price_rounds_to_tick(self, sym_info):
        assert sym_info.round_price("SOLUSDT", 156.789) == 156.78


class TestQuantityRounding:
    def test_btc_qty_rounds_down(self, sym_info):
        assert sym_info.round_quantity("BTCUSDT", 0.1234) == 0.123

    def test_sol_qty_rounds_down(self, sym_info):
        assert sym_info.round_quantity("SOLUSDT", 15.67) == 15.6


class TestOrderValidation:
    def test_valid_order(self, sym_info):
        valid, err = sym_info.validate_order("BTCUSDT", 0.001, 67000.0)
        assert valid
        assert err == ""

    def test_below_min_qty(self, sym_info):
        valid, err = sym_info.validate_order("BTCUSDT", 0.0001, 67000.0)
        assert not valid
        assert "min" in err.lower()

    def test_below_min_notional(self, sym_info):
        valid, err = sym_info.validate_order("SOLUSDT", 0.1, 1.0)
        assert not valid
        assert "Notional" in err

    def test_unknown_symbol(self, sym_info):
        valid, err = sym_info.validate_order("UNKNOWN", 1.0, 100.0)
        assert not valid

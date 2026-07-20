"""
Tests for Risk Manager.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from config.loader import load_config
from risk.risk_manager import RiskManager


@pytest.fixture
def config():
    return load_config(
        config_path=Path(__file__).parent.parent / "config" / "settings.yaml",
        env_path=Path(__file__).parent.parent / ".env.example",
    )


@pytest.fixture
def risk_mgr(config):
    return RiskManager(config)


class TestPositionSizing:
    def test_basic_sizing(self, risk_mgr):
        risk_mgr.update_equity(10000)
        qty, lev = risk_mgr.calculate_position_size(
            equity=10000, entry_price=100, stop_price=98,
        )
        # Risk = 1.5% of 10000 = 150
        # Stop distance = 2
        # Qty = 150 / 2 = 75
        assert qty == pytest.approx(75, rel=0.1)
        assert 1 <= lev <= 10

    def test_zero_stop_distance(self, risk_mgr):
        risk_mgr.update_equity(10000)
        qty, lev = risk_mgr.calculate_position_size(
            equity=10000, entry_price=100, stop_price=100,
        )
        assert qty == 0.0

    def test_leverage_hard_cap(self, risk_mgr):
        risk_mgr.update_equity(100)
        qty, lev = risk_mgr.calculate_position_size(
            equity=100, entry_price=50000, stop_price=49500,
        )
        assert lev <= 10

    def test_symbol_specific_risk(self, risk_mgr):
        risk_mgr.update_equity(10000)
        qty_default, _ = risk_mgr.calculate_position_size(
            equity=10000, entry_price=100, stop_price=98,
        )
        qty_lower, _ = risk_mgr.calculate_position_size(
            equity=10000, entry_price=100, stop_price=98, symbol_risk_pct=0.75,
        )
        assert qty_lower < qty_default

    def test_active_reduction_caps_symbol_risk(self, risk_mgr, config):
        risk_mgr.update_equity(10000)
        risk_mgr.state.risk_pct_active = config.risk.reduced_risk_pct
        qty, _ = risk_mgr.calculate_position_size(
            equity=10000,
            entry_price=100,
            stop_price=98,
            symbol_risk_pct=config.risk.default_risk_pct,
        )
        expected = (
            10000 * config.risk.reduced_risk_pct / 100.0
        ) / 2.0
        assert qty == pytest.approx(expected)


class TestDrawdownAdjustment:
    def test_reduces_risk_at_threshold(self, risk_mgr, config):
        risk_mgr.state.peak_equity = 10000
        risk_mgr.update_equity(8400)  # 16% DD
        assert risk_mgr.state.risk_pct_active == config.risk.reduced_risk_pct

    def test_restores_risk_above_threshold(self, risk_mgr, config):
        risk_mgr.state.peak_equity = 10000
        risk_mgr.update_equity(8400)  # reduce
        risk_mgr.update_equity(9100)  # recover above -10%
        assert risk_mgr.state.risk_pct_active == config.risk.default_risk_pct

    def test_new_peak_resets_dd(self, risk_mgr):
        risk_mgr.state.peak_equity = 10000
        risk_mgr.update_equity(11000)
        assert risk_mgr.state.peak_equity == 11000
        assert risk_mgr.state.current_drawdown_pct == 0.0

    def test_normalizes_stale_recovered_risk(self, risk_mgr, config):
        risk_mgr.state.risk_pct_active = 8.0
        risk_mgr.normalize_active_risk()
        assert risk_mgr.state.risk_pct_active == config.risk.default_risk_pct


class TestStreakTracking:
    def test_consecutive_losses_reduce_risk(self, risk_mgr, config):
        risk_mgr.update_equity(10000)
        for _ in range(config.brakes.consecutive_loss_threshold):
            risk_mgr.record_trade_result(-1.0)
        assert risk_mgr.state.risk_pct_active == config.risk.reduced_risk_pct
        assert risk_mgr.state.reduced_risk_trades_remaining > 0

    def test_win_resets_streak(self, risk_mgr):
        risk_mgr.update_equity(10000)
        risk_mgr.record_trade_result(-1.0)
        risk_mgr.record_trade_result(-1.0)
        risk_mgr.record_trade_result(2.0)
        assert risk_mgr.state.consecutive_losses == 0

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


class TestMarginReserve:
    def test_leverage_floors_margin_to_equity_slot(self, risk_mgr, config):
        # A position whose notional is a large fraction of equity must not lock
        # up more than equity / max_concurrent_positions as initial margin.
        risk_mgr.update_equity(19.0)
        risk_mgr.state.risk_pct_active = 4.0
        qty, lev = risk_mgr.calculate_position_size(
            equity=19.0, entry_price=554.0, stop_price=578.0, symbol_risk_pct=4.0,
        )
        slots = config.portfolio.max_concurrent_positions
        margin = (qty * 554.0) / lev
        assert margin <= 19.0 / slots + 1e-9
        # Regression: 1x leverage would have locked the full notional (~$17).
        assert lev >= 2

    def test_risk_at_stop_unchanged_by_leverage(self, risk_mgr):
        # Leverage only affects margin when slot fits; may cut qty if lever capped.
        risk_mgr.update_equity(19.0)
        risk_mgr.state.risk_pct_active = 4.0
        qty, _ = risk_mgr.calculate_position_size(
            equity=19.0, entry_price=554.0, stop_price=578.0, symbol_risk_pct=4.0,
        )
        loss_at_stop = qty * abs(578.0 - 554.0)
        assert loss_at_stop == pytest.approx(19.0 * 0.04, rel=1e-6)

    def test_size_reduces_when_max_leverage_blocks_slots(self, risk_mgr, config):
        # Live ZEC regression: risk_pct=15, stop ~3% of price, max_leverage=10,
        # max_concurrent=3 → required lev ~14–15; without size-down margin locked
        # ~half equity and 2nd/3rd entries hit insufficient margin.
        config.portfolio.max_concurrent_positions = 3
        config.risk.max_leverage = 10
        config.risk.liquidation_buffer_pct = 0.05
        risk_mgr.update_equity(15.26)
        risk_mgr.state.risk_pct_active = 15.0
        qty, lev = risk_mgr.calculate_position_size(
            equity=15.26,
            entry_price=475.5,
            stop_price=489.62,
            symbol_risk_pct=15.0,
        )
        assert lev == 10
        slots = 3
        margin = (qty * 475.5) / lev
        assert margin <= 15.26 / slots + 1e-6
        # Full 15% risk would need > slot margin at 10x — effective risk cut.
        stop_dist = abs(489.62 - 475.5)
        risk_actual = qty * stop_dist
        assert risk_actual < 15.26 * 0.15 - 1e-9
        # Three identical slots still fit under full equity (with buffer).
        assert 3 * margin <= 15.26 * (1 - 0.05) + 1e-6

    def test_higher_max_leverage_preserves_full_risk_for_slots(self, risk_mgr, config):
        # With max_leverage=50, same ZEC params should keep full 15% risk and
        # still fit 3 concurrent margin slots (required lev ~15).
        config.portfolio.max_concurrent_positions = 3
        config.risk.max_leverage = 50
        config.risk.liquidation_buffer_pct = 0.05
        risk_mgr.update_equity(15.26)
        risk_mgr.state.risk_pct_active = 15.0
        qty, lev = risk_mgr.calculate_position_size(
            equity=15.26,
            entry_price=475.5,
            stop_price=489.62,
            symbol_risk_pct=15.0,
        )
        stop_dist = abs(489.62 - 475.5)
        risk_actual = qty * stop_dist
        assert risk_actual == pytest.approx(15.26 * 0.15, rel=1e-6)
        assert 2 <= lev <= 50
        margin = (qty * 475.5) / lev
        assert margin <= 15.26 / 3 + 1e-6


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

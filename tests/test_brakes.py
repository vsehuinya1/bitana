"""
Tests for Brake System.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from config.loader import load_config
from core.models import BrakeState, BrakeType
from risk.brakes import BrakeManager


@pytest.fixture
def config():
    return load_config(
        config_path=Path(__file__).parent.parent / "config" / "settings.yaml",
        env_path=Path(__file__).parent.parent / ".env.example",
    )


@pytest.fixture
def brake_mgr(config):
    return BrakeManager(config.brakes)


class TestDailyLoss:
    def test_blocks_after_limit(self, brake_mgr, config):
        # Simulate 5% daily loss (limit is 4%)
        brake_mgr.record_loss(0.05)
        allowed, reason = brake_mgr.check_entry_allowed()
        assert not allowed
        assert "Daily" in reason

    def test_allows_before_limit(self, brake_mgr):
        brake_mgr.record_loss(0.01)
        allowed, _ = brake_mgr.check_entry_allowed()
        assert allowed


class TestWeeklyLoss:
    def test_triggers_cooldown(self, brake_mgr):
        brake_mgr.record_loss(0.09)  # > 8% weekly limit
        triggered = brake_mgr.record_loss(0.0)  # just to check
        allowed, reason = brake_mgr.check_entry_allowed()
        assert not allowed
        assert brake_mgr.state.weekly_cooldown_until is not None


class TestEquityBrakes:
    def test_pause_at_25pct_dd(self, brake_mgr):
        triggered = brake_mgr.check_equity_brakes(0.26)
        assert BrakeType.EQUITY_PAUSE in triggered
        assert brake_mgr.state.manual_review_required is True

    def test_shutdown_at_40pct_dd(self, brake_mgr):
        triggered = brake_mgr.check_equity_brakes(0.41)
        assert BrakeType.EQUITY_SHUTDOWN in triggered
        assert brake_mgr.state.is_shutdown is True


class TestPauseResume:
    def test_pause_blocks_entries(self, brake_mgr):
        brake_mgr.pause("test pause")
        allowed, reason = brake_mgr.check_entry_allowed()
        assert not allowed
        assert "PAUSED" in reason

    def test_resume_allows_entries(self, brake_mgr):
        brake_mgr.pause("test")
        brake_mgr.resume()
        allowed, _ = brake_mgr.check_entry_allowed()
        assert allowed


class TestPersistence:
    def test_state_serializable(self, brake_mgr):
        brake_mgr.record_loss(0.02)
        brake_mgr.pause("test")
        state = brake_mgr.state
        # Verify state can be serialized
        data = state.model_dump()
        restored = BrakeState(**data)
        assert restored.is_paused is True
        assert restored.daily_realized_loss > 0

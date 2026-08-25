"""
Hard Risk Brakes

All brakes are persistent across restarts via SQLite.
Daily loss limits, consecutive losses, equity drawdown pauses.
"""
from __future__ import annotations

from datetime import datetime, timezone

from config.loader import BrakesConfig
from core.logging_setup import get_logger
from core.models import BrakeState, BrakeType

logger = get_logger("brakes")


class BrakeManager:
    """Enforces hard risk brakes. State is persisted via Database."""

    def __init__(self, config: BrakesConfig) -> None:
        self._cfg = config
        self.state = BrakeState()

    def load_state(self, state: BrakeState) -> None:
        self.state = state

    def check_entry_allowed(self) -> tuple[bool, str]:
        """Check if new entries are allowed. Returns (allowed, reason)."""
        now = datetime.now(timezone.utc)

        if self.state.is_shutdown:
            return False, f"SHUTDOWN: {self.state.shutdown_reason}"

        if self.state.manual_review_required:
            return False, "Manual review required — use /resume"

        if self.state.is_paused:
            return False, f"PAUSED: {self.state.pause_reason}"

        # Daily loss check
        today = now.strftime("%Y-%m-%d")
        if self.state.daily_reset_date != today:
            self.state.daily_realized_loss = 0.0
            self.state.daily_reset_date = today

        if self.state.daily_realized_loss >= self._cfg.daily_loss_limit_pct:
            return False, f"Daily loss limit hit: {self.state.daily_realized_loss:.2%}"

        return True, ""

    def record_loss(self, loss_pct: float) -> list[BrakeType]:
        """Record a realized loss (as fraction of equity). Returns triggered brakes."""
        triggered = []
        now = datetime.now(timezone.utc)

        # Reset daily loss if needed
        today = now.strftime("%Y-%m-%d")
        if self.state.daily_reset_date != today:
            self.state.daily_realized_loss = 0.0
            self.state.daily_reset_date = today

        self.state.daily_realized_loss += abs(loss_pct)

        if self.state.daily_realized_loss >= self._cfg.daily_loss_limit_pct:
            triggered.append(BrakeType.DAILY_LOSS)
            # Owner ruling 2026-08-25: DAILY_LOSS must PAUSE, not just alert.
            # Persists across midnight (unlike the soft block in
            # check_entry_allowed) until explicit /resume.
            reason = (
                f"Daily loss {self.state.daily_realized_loss:.1%} >= "
                f"limit {self._cfg.daily_loss_limit_pct:.0%} — auto-pause"
            )
            self.pause(reason)
            logger.warning("BRAKE: Daily loss limit — paused",
                           loss=self.state.daily_realized_loss)

        return triggered

    def check_equity_brakes(self, drawdown_pct: float) -> list[BrakeType]:
        """Check equity-based brakes. Returns triggered brakes."""
        triggered = []

        if drawdown_pct >= self._cfg.equity_shutdown_drawdown:
            self.state.is_shutdown = True
            self.state.shutdown_reason = f"Equity DD {drawdown_pct:.1%} >= {self._cfg.equity_shutdown_drawdown:.0%}"
            triggered.append(BrakeType.EQUITY_SHUTDOWN)
            logger.critical("BRAKE: EQUITY SHUTDOWN", dd=drawdown_pct)

        elif drawdown_pct >= self._cfg.equity_pause_drawdown:
            self.state.manual_review_required = True
            self.state.is_paused = True
            self.state.pause_reason = f"Equity DD {drawdown_pct:.1%} — manual review required"
            triggered.append(BrakeType.EQUITY_PAUSE)
            logger.critical("BRAKE: EQUITY PAUSE — manual review", dd=drawdown_pct)

        return triggered

    def pause(self, reason: str = "Manual pause") -> None:
        self.state.is_paused = True
        self.state.pause_reason = reason

    def resume(self) -> None:
        self.state.is_paused = False
        self.state.pause_reason = ""
        self.state.manual_review_required = False

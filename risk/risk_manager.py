"""
Risk Manager

Position sizing, leverage calculation, drawdown-adjusted risk.
No martingale, no averaging, no pyramiding.
"""
from __future__ import annotations

from config.loader import AppConfig
from core.logging_setup import get_logger
from core.models import RiskState, Side

logger = get_logger("risk_manager")


class RiskManager:
    """Manages per-trade risk, position sizing, and leverage."""

    def __init__(self, config: AppConfig) -> None:
        self._cfg = config
        self.state = RiskState(
            peak_equity=0.0,
            current_equity=0.0,
            risk_pct_active=config.risk.default_risk_pct,
        )

    def update_equity(self, equity: float) -> None:
        """Update equity and recalculate drawdown state."""
        self.state.current_equity = equity
        if equity > self.state.peak_equity:
            self.state.peak_equity = equity

        if self.state.peak_equity > 0:
            dd = (self.state.peak_equity - equity) / self.state.peak_equity
            self.state.current_drawdown_pct = dd
        else:
            self.state.current_drawdown_pct = 0.0

        # Dynamic risk adjustment
        cfg = self._cfg.risk
        if self.state.current_drawdown_pct > cfg.drawdown_reduce_threshold:
            self.state.risk_pct_active = cfg.reduced_risk_pct
        elif self.state.current_drawdown_pct < cfg.drawdown_restore_threshold:
            # Only restore if not in consecutive loss reduction
            if self.state.reduced_risk_trades_remaining <= 0:
                self.state.risk_pct_active = cfg.default_risk_pct

    def record_trade_result(self, pnl_r: float) -> None:
        """Update streak counters after a trade closes."""
        cfg = self._cfg.brakes
        if pnl_r < 0:
            self.state.consecutive_losses += 1
            if self.state.consecutive_losses >= cfg.consecutive_loss_threshold:
                self.state.reduced_risk_trades_remaining = cfg.consecutive_loss_reduced_trades
                self.state.risk_pct_active = self._cfg.risk.reduced_risk_pct
                logger.warning(
                    "Consecutive loss streak — reducing risk",
                    streak=self.state.consecutive_losses,
                    reduced_trades=self.state.reduced_risk_trades_remaining,
                )
        else:
            self.state.consecutive_losses = 0

        if self.state.reduced_risk_trades_remaining > 0:
            self.state.reduced_risk_trades_remaining -= 1
            self.state.risk_pct_active = self._cfg.risk.reduced_risk_pct
            if self.state.reduced_risk_trades_remaining <= 0:
                if self.state.current_drawdown_pct < self._cfg.risk.drawdown_reduce_threshold:
                    self.state.risk_pct_active = self._cfg.risk.default_risk_pct

    def calculate_position_size(
        self,
        equity: float,
        entry_price: float,
        stop_price: float,
        symbol_risk_pct: float | None = None,
    ) -> tuple[float, int]:
        """Calculate position size and leverage from stop distance.

        Returns (quantity, leverage).
        """
        risk_pct = symbol_risk_pct or self.state.risk_pct_active
        risk_amount = equity * (risk_pct / 100.0)

        stop_distance = abs(entry_price - stop_price)
        if stop_distance == 0 or entry_price == 0:
            logger.warning("Invalid stop distance or entry price")
            return 0.0, 1

        # Position size = risk_amount / stop_distance_per_unit
        quantity = risk_amount / stop_distance

        # Notional value
        notional = quantity * entry_price

        # Required leverage
        required_leverage = notional / equity if equity > 0 else 1
        leverage = min(int(required_leverage) + 1, self._cfg.risk.max_leverage)
        leverage = max(leverage, 1)

        # Liquidation proximity check
        available_margin = equity  # simplified for cross margin
        liq_buffer = self._cfg.risk.liquidation_buffer_pct
        max_notional = available_margin * leverage * (1 - liq_buffer)

        if notional > max_notional:
            quantity = max_notional / entry_price
            logger.warning(
                "Position reduced for liquidation buffer",
                original_notional=notional,
                max_notional=max_notional,
            )

        if quantity <= 0:
            return 0.0, 1

        logger.info(
            "Position sized",
            equity=round(equity, 2),
            risk_pct=risk_pct,
            risk_amount=round(risk_amount, 2),
            stop_dist=round(stop_distance, 4),
            quantity=round(quantity, 6),
            leverage=leverage,
        )
        return quantity, leverage

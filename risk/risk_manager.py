"""
Risk Manager

Position sizing, leverage calculation, drawdown-adjusted risk.
No martingale, no averaging, no pyramiding.
"""
from __future__ import annotations

import math

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

    def normalize_active_risk(self) -> None:
        """Align recovered risk with current config while preserving reductions."""
        reduced = (
            self.state.current_drawdown_pct > self._cfg.risk.drawdown_reduce_threshold
            or self.state.reduced_risk_trades_remaining > 0
        )
        self.state.risk_pct_active = (
            self._cfg.risk.reduced_risk_pct
            if reduced
            else self._cfg.risk.default_risk_pct
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

    def record_trade_result(self, pnl_r: float, cluster_bucket: str | None = None) -> None:
        """Update streak counters after a trade closes.

        Args:
            pnl_r: Trade PnL in R multiples.
            cluster_bucket: The 15-min cluster bucket this trade belongs to.
                           If provided, streaks are tracked per-bucket.
                           If None (legacy), falls back to global counter key "".
        """
        cfg = self._cfg.brakes
        bucket = cluster_bucket or ""

        if pnl_r < 0:
            # Increment streak for this bucket
            current = self.state.consecutive_losses.get(bucket, 0) + 1
            self.state.consecutive_losses[bucket] = current

            if current >= cfg.consecutive_loss_threshold:
                self.state.reduced_risk_trades_remaining = cfg.consecutive_loss_reduced_trades
                self.state.risk_pct_active = self._cfg.risk.reduced_risk_pct
                logger.warning(
                    "Consecutive loss streak — reducing risk",
                    bucket=bucket,
                    streak=current,
                    reduced_trades=self.state.reduced_risk_trades_remaining,
                )
        else:
            # Reset streak for this bucket on win
            self.state.consecutive_losses[bucket] = 0

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
        requested_risk = (
            symbol_risk_pct
            if symbol_risk_pct is not None
            else self.state.risk_pct_active
        )
        # Per-engine/symbol risk is a ceiling, never a way around active
        # drawdown or consecutive-loss reductions.
        risk_pct = min(requested_risk, self.state.risk_pct_active)
        risk_amount = equity * (risk_pct / 100.0)

        stop_distance = abs(entry_price - stop_price)
        if stop_distance == 0 or entry_price == 0:
            logger.warning("Invalid stop distance or entry price")
            return 0.0, 1

        # Position size = risk_amount / stop_distance_per_unit
        quantity = risk_amount / stop_distance

        # Notional value
        notional = quantity * entry_price

        # Leverage: pick the smallest whole leverage that keeps this position's
        # initial margin within one equity slot, so several concurrent
        # positions can be funded. margin = notional / leverage, and we want
        # margin <= equity / max_concurrent_positions, hence
        # leverage >= max_concurrent_positions * notional / equity.
        # Leverage never changes the loss at stop by itself — only the margin
        # locked up. If max_leverage still cannot fund the target risk inside
        # one slot, quantity is reduced (risk in $ falls) so concurrent slots
        # remain fundable (avoids "insufficient margin for concurrent positions").
        slots = max(self._cfg.portfolio.max_concurrent_positions, 1)
        required_leverage = slots * notional / equity if equity > 0 else 1
        leverage = min(math.ceil(required_leverage), self._cfg.risk.max_leverage)
        leverage = max(leverage, 1)

        liq_buffer = self._cfg.risk.liquidation_buffer_pct
        # Reserve 1/slots of equity as initial margin per position (cross).
        slot_margin = equity / slots if equity > 0 else 0.0
        max_notional = slot_margin * leverage * (1 - liq_buffer)

        if notional > max_notional and entry_price > 0:
            old_notional = notional
            quantity = max_notional / entry_price
            notional = max_notional
            new_risk = quantity * stop_distance
            logger.warning(
                "Position reduced for concurrent margin slot",
                original_notional=round(old_notional, 4),
                max_notional=round(max_notional, 4),
                slots=slots,
                leverage=leverage,
                risk_amount_target=round(risk_amount, 4),
                risk_amount_actual=round(new_risk, 4),
            )

        if quantity <= 0:
            return 0.0, 1

        logger.info(
            "Position sized",
            equity=round(equity, 2),
            risk_pct=risk_pct,
            risk_amount=round(quantity * stop_distance, 2),
            risk_pct_effective=round(
                (quantity * stop_distance / equity) * 100.0, 2
            )
            if equity > 0
            else 0.0,
            stop_dist=round(stop_distance, 4),
            quantity=round(quantity, 6),
            leverage=leverage,
            margin_est=round(notional / leverage, 2) if leverage else 0.0,
        )
        return quantity, leverage

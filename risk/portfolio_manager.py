"""
Portfolio Manager

Enforces position limits, BTC priority, correlation rules.
"""
from __future__ import annotations

from config.loader import PortfolioConfig
from core.logging_setup import get_logger
from core.models import Position, Side, Signal

logger = get_logger("portfolio")


class PortfolioManager:
    """Manages portfolio-level position constraints."""

    def __init__(self, config: PortfolioConfig) -> None:
        self._cfg = config

    def can_open(
        self,
        signal: Signal,
        open_positions: list[Position],
    ) -> tuple[bool, str]:
        """Check if a new position can be opened."""
        cfg = self._cfg

        # Max concurrent positions
        active = [p for p in open_positions if p.state.value not in ("CLOSED", "CANCELLED")]
        if len(active) >= cfg.max_concurrent_positions:
            return False, f"Max positions reached: {len(active)}/{cfg.max_concurrent_positions}"

        # Max per symbol
        sym_positions = [p for p in active if p.symbol == signal.symbol]
        if len(sym_positions) >= cfg.max_per_symbol:
            return False, f"Max positions for {signal.symbol}: {len(sym_positions)}/{cfg.max_per_symbol}"

        # No duplicate entries
        for p in active:
            if p.symbol == signal.symbol and p.side == signal.side:
                return False, f"Duplicate {signal.side.value} on {signal.symbol}"

        # Correlation check: if BTC long open and SOL long signal, require independent
        if cfg.correlation_require_independent and signal.symbol == "SOLUSDT":
            btc_positions = [p for p in active if p.symbol == "BTCUSDT"]
            for bp in btc_positions:
                if bp.side == signal.side:
                    logger.info(
                        "Correlation filter: BTC same-direction position open",
                        signal_symbol=signal.symbol,
                        signal_side=signal.side.value,
                    )
                    # Signal allowed but may need independent validation
                    # (engine already validates independently)

        return True, ""

    def get_sizing_multiplier(
        self, signal: Signal, open_positions: list[Position]
    ) -> float:
        """Get sizing multiplier based on correlation."""
        if self._cfg.correlation_sizing_reduction <= 0:
            return 1.0

        if signal.symbol == "SOLUSDT":
            btc_positions = [
                p for p in open_positions
                if p.symbol == "BTCUSDT"
                and p.side == signal.side
                and p.state.value not in ("CLOSED", "CANCELLED")
            ]
            if btc_positions:
                return 1.0 - self._cfg.correlation_sizing_reduction

        return 1.0

    def prioritize_signals(self, signals: list[Signal]) -> list[Signal]:
        """BTC priority when simultaneous signals."""
        if not self._cfg.btc_priority or len(signals) <= 1:
            return signals

        btc = [s for s in signals if s.symbol == "BTCUSDT"]
        others = [s for s in signals if s.symbol != "BTCUSDT"]

        return btc + others

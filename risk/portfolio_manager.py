"""
Portfolio Manager

Enforces position limits, BTC priority, correlation rules.
"""
from __future__ import annotations

from config.loader import PortfolioConfig
from core.logging_setup import get_logger
from core.models import EngineType, Position, Side, Signal

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

        engine_scoped = cfg.limit_by_engine

        # Max per symbol (optionally per engine — testnet burst vs legacy positions)
        sym_positions = [
            p for p in active
            if p.symbol == signal.symbol
            and (not engine_scoped or p.engine == signal.engine)
        ]
        if len(sym_positions) >= cfg.max_per_symbol:
            return False, f"Max positions for {signal.symbol}: {len(sym_positions)}/{cfg.max_per_symbol}"

        # No duplicate entries (same symbol + side + engine)
        for p in active:
            if p.symbol != signal.symbol or p.side != signal.side:
                continue
            if engine_scoped and p.engine != signal.engine:
                continue
            return False, f"Duplicate {signal.side.value} on {signal.symbol}"

        if cfg.max_cluster_positions > 0 and signal.engine == EngineType.LIQ_BURST_FOLLOW:
            sd = signal.signal_data or {}
            session = sd.get("session")
            bucket = sd.get("cluster_bucket")
            if session and bucket:
                cluster_count = sum(
                    1 for p in active
                    if p.engine == EngineType.LIQ_BURST_FOLLOW
                    and p.side == signal.side
                    and (p.signal_data or {}).get("session") == session
                    and (p.signal_data or {}).get("cluster_bucket") == bucket
                )
                if cluster_count >= cfg.max_cluster_positions:
                    return False, (
                        f"Cluster cap reached: {cluster_count}/{cfg.max_cluster_positions} "
                        f"({session} {signal.side.value} {bucket})"
                    )

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

    def get_cluster_risk_multiplier(
        self,
        signal: Signal,
        open_positions: list[Position],
        equity: float,
        new_leg_risk_pct: float,
    ) -> float:
        """2026-08-31 (owner order): same-cluster aggregate-risk cap, sizing-only.

        Groups open positions by the exact max_cluster_positions predicate
        (engine + session + side + 15-min cluster bucket) and caps their SUM of
        remaining risk-to-stop at cfg.max_cluster_risk_pct (% of equity). The new
        leg's quantity is multiplied by clamp(remaining / new_leg_risk_pct, 0, 1):
        legs 2-3 are sized DOWN to fit; 0 => same-bucket budget exhausted (the
        upstream zero-size check skips the entry — never blocked while budget
        remains). Returns 1.0 when the cap is off or the signal carries no
        cluster metadata.
        """
        cap = float(getattr(self._cfg, "max_cluster_risk_pct", 0.0) or 0.0)
        if cap <= 0 or equity <= 0 or new_leg_risk_pct <= 0:
            return 1.0
        sd = signal.signal_data or {}
        session = sd.get("session")
        bucket = sd.get("cluster_bucket")
        if not session or not bucket:
            return 1.0

        open_risk_pct = 0.0
        for p in open_positions:
            if p.state.value in ("CLOSED", "CANCELLED"):
                continue
            if p.engine != signal.engine or p.side != signal.side:
                continue
            psd = p.signal_data or {}
            if psd.get("session") != session or psd.get("cluster_bucket") != bucket:
                continue
            if p.entry_price and p.stop_price and p.quantity:
                open_risk_pct += (
                    abs(p.entry_price - p.stop_price) * p.quantity / equity * 100.0
                )

        remaining = cap - open_risk_pct
        if remaining <= 0:
            logger.warning(
                "Cluster risk cap exhausted — entry skipped",
                session=session,
                bucket=bucket,
                open_risk_pct=round(open_risk_pct, 2),
                cap=cap,
            )
            return 0.0
        mult = min(1.0, remaining / new_leg_risk_pct)
        if mult < 1.0:
            logger.info(
                "Cluster risk cap — sizing new leg down",
                session=session,
                bucket=bucket,
                open_risk_pct=round(open_risk_pct, 2),
                cap=cap,
                new_leg_risk_pct=round(new_leg_risk_pct, 2),
                multiplier=round(mult, 3),
            )
        return mult

    def prioritize_signals(self, signals: list[Signal]) -> list[Signal]:
        """BTC priority when simultaneous signals."""
        if not self._cfg.btc_priority or len(signals) <= 1:
            return signals

        btc = [s for s in signals if s.symbol == "BTCUSDT"]
        others = [s for s in signals if s.symbol != "BTCUSDT"]

        return btc + others

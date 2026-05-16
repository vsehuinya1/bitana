"""
Fee and slippage cost model.

Binance futures realistic assumptions:
- Taker fee: 0.04% (4 bps)
- Maker fee: 0.02% (2 bps)
- Slippage: configurable 1-3 bps

ALL results evaluated AFTER costs.
"""
from dataclasses import dataclass
from research.config.settings import TAKER_FEE_BPS, MAKER_FEE_BPS, DEFAULT_SLIPPAGE_BPS


@dataclass
class CostModel:
    """Trading cost model."""
    taker_fee_bps: float = TAKER_FEE_BPS
    maker_fee_bps: float = MAKER_FEE_BPS
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS
    use_taker: bool = True  # Default to taker (conservative)

    @property
    def fee_rate(self) -> float:
        """Fee rate as decimal."""
        fee = self.taker_fee_bps if self.use_taker else self.maker_fee_bps
        return fee / 10_000

    @property
    def slippage_rate(self) -> float:
        """Slippage rate as decimal."""
        return self.slippage_bps / 10_000

    @property
    def total_one_way_rate(self) -> float:
        """Total one-way cost (fee + slippage) as decimal."""
        return self.fee_rate + self.slippage_rate

    @property
    def total_round_trip_bps(self) -> float:
        """Total round-trip cost in bps."""
        return (self.fee_rate + self.slippage_rate) * 2 * 10_000


def apply_costs(
    price: float,
    size: float,
    cost_model: CostModel | None = None,
    mode: str = "entry",
) -> float:
    """
    Calculate cost for a trade leg.

    Args:
        price: Execution price
        size: Position size (base asset)
        cost_model: Cost model to use
        mode: 'entry' or 'exit'

    Returns: Total cost in quote currency
    """
    if cost_model is None:
        cost_model = CostModel()

    notional = abs(price * size)
    fee = notional * cost_model.fee_rate
    slippage = notional * cost_model.slippage_rate

    return fee + slippage


def round_trip_cost(
    entry_price: float,
    exit_price: float,
    size: float,
    cost_model: CostModel | None = None,
) -> float:
    """Calculate round-trip (entry + exit) cost."""
    if cost_model is None:
        cost_model = CostModel()

    entry_cost = apply_costs(entry_price, size, cost_model, "entry")
    exit_cost = apply_costs(exit_price, size, cost_model, "exit")
    return entry_cost + exit_cost

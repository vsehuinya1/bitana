"""
Binance Symbol Info — Precision & Filter Layer

Fetches exchangeInfo, extracts tick/step/notional rules,
provides rounding helpers and preflight order validation.
"""
from __future__ import annotations

import math
from decimal import Decimal, ROUND_DOWN
from typing import Optional

from pydantic import BaseModel

from core.logging_setup import get_logger

logger = get_logger("symbol_info")


class SymbolFilters(BaseModel):
    """Parsed Binance symbol filters."""
    symbol: str
    tick_size: float          # PRICE_FILTER
    step_size: float          # LOT_SIZE
    min_qty: float            # LOT_SIZE
    max_qty: float            # LOT_SIZE
    min_notional: float       # MIN_NOTIONAL
    price_precision: int
    quantity_precision: int


def _precision_from_step(step: float) -> int:
    """Derive decimal precision from step size."""
    if step >= 1.0:
        return 0
    s = f"{step:.12f}".rstrip("0")
    if "." in s:
        return len(s.split(".")[1])
    return 0


class SymbolInfoManager:
    """Manages symbol precision and validates orders before submission."""

    def __init__(self) -> None:
        self._filters: dict[str, SymbolFilters] = {}

    def load_from_exchange_info(self, exchange_info: dict) -> None:
        """Parse exchangeInfo response and extract filters for all symbols."""
        for sym_data in exchange_info.get("symbols", []):
            symbol = sym_data["symbol"]
            filters_raw = {f["filterType"]: f for f in sym_data.get("filters", [])}

            price_filter = filters_raw.get("PRICE_FILTER", {})
            lot_size = filters_raw.get("LOT_SIZE", {})
            min_notional = filters_raw.get("MIN_NOTIONAL", {})

            tick_size = float(price_filter.get("tickSize", "0.01"))
            step_size = float(lot_size.get("stepSize", "0.001"))

            sf = SymbolFilters(
                symbol=symbol,
                tick_size=tick_size,
                step_size=step_size,
                min_qty=float(lot_size.get("minQty", "0.001")),
                max_qty=float(lot_size.get("maxQty", "9999999")),
                min_notional=float(min_notional.get("notional", "5")),
                price_precision=_precision_from_step(tick_size),
                quantity_precision=_precision_from_step(step_size),
            )
            self._filters[symbol] = sf

        logger.info(
            "Symbol filters loaded",
            count=len(self._filters),
            symbols=list(self._filters.keys())[:10],
        )

    def get_filters(self, symbol: str) -> Optional[SymbolFilters]:
        return self._filters.get(symbol)

    def round_price(self, symbol: str, price: float) -> float:
        """Round price to valid tick size."""
        sf = self._filters.get(symbol)
        if not sf:
            return price
        d = Decimal(str(price))
        tick = Decimal(str(sf.tick_size))
        rounded = float((d / tick).to_integral_value(rounding=ROUND_DOWN) * tick)
        return round(rounded, sf.price_precision)

    def round_quantity(self, symbol: str, qty: float) -> float:
        """Round quantity to valid step size."""
        sf = self._filters.get(symbol)
        if not sf:
            return qty
        d = Decimal(str(qty))
        step = Decimal(str(sf.step_size))
        rounded = float((d / step).to_integral_value(rounding=ROUND_DOWN) * step)
        return round(rounded, sf.quantity_precision)

    def validate_order(
        self, symbol: str, quantity: float, price: float
    ) -> tuple[bool, str]:
        """Preflight validation before order submission.

        Returns (is_valid, error_message).
        """
        sf = self._filters.get(symbol)
        if not sf:
            return False, f"No filters loaded for {symbol}"

        # Quantity bounds
        if quantity < sf.min_qty:
            return False, f"Qty {quantity} < min {sf.min_qty}"
        if quantity > sf.max_qty:
            return False, f"Qty {quantity} > max {sf.max_qty}"

        # Step size alignment (use Decimal to avoid float precision issues)
        d_qty = Decimal(str(quantity))
        d_step = Decimal(str(sf.step_size))
        remainder = d_qty % d_step
        if remainder > d_step * Decimal("0.01"):
            return False, f"Qty {quantity} not aligned to step {sf.step_size}"

        # Tick size alignment
        d_price = Decimal(str(price))
        d_tick = Decimal(str(sf.tick_size))
        price_remainder = d_price % d_tick
        if price_remainder > d_tick * Decimal("0.01"):
            return False, f"Price {price} not aligned to tick {sf.tick_size}"

        # Min notional
        notional = quantity * price
        if notional < sf.min_notional:
            return False, f"Notional {notional:.2f} < min {sf.min_notional}"

        return True, ""

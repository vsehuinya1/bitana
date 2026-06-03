"""
Shadow Exit Evaluators — Hypothetical exit logic for research.

These run alongside manage_position() but NEVER trigger actual exits.
They log when they WOULD have fired and at what price/R.

Each evaluator receives the same inputs as manage_position() and
returns a list of (shadow_name, trigger_price, shadow_r) tuples
for any shadows that triggered on this bar.
"""
from __future__ import annotations

import numpy as np
from typing import Optional
from core.models import Candle


def evaluate_shadows(
    candles_5m: list[Candle],
    entry_price: float,
    risk_per_unit: float,
    best_price: float,
    bars_held: int,
    consecutive_red: int,
    entry_context: dict,
) -> list[tuple[str, float, float]]:
    """
    Evaluate all shadow exits on current bar.
    Returns list of (shadow_name, trigger_price, shadow_r) for any that fired.

    entry_context should contain:
        range_high: float (the breakout level that triggered entry)
        ema_value: float (EMA at entry time — we recompute current)
        decile: int
    """
    if len(candles_5m) < 2 or risk_per_unit <= 0:
        return []

    triggers = []
    candle = candles_5m[-1]
    price = candle.close
    low = candle.low
    current_r = (price - entry_price) / risk_per_unit

    closes = np.array([c.close for c in candles_5m])
    highs = np.array([c.high for c in candles_5m])
    lows = np.array([c.low for c in candles_5m])

    # ATR for trail calculations
    atr = _simple_atr(highs, lows, closes, 14)

    # EMA(20) current value
    ema = _ema(closes, 20)

    # ── Shadow 1: Structural Invalidation ─────────────────────────
    # If close drops below the range_high that triggered the breakout
    # AND we're underwater (< 1R profit), the thesis is dead.
    range_high = entry_context.get("range_high", 0)
    if range_high > 0 and price < range_high and current_r < 1.0:
        inv_price = price
        inv_r = current_r
        triggers.append(("structural_invalidation", inv_price, inv_r))

    # ── Shadow 2: Momentum Reversal ───────────────────────────────
    # 3+ consecutive red bars AND close below EMA(20) AND underwater
    if consecutive_red >= 3 and price < ema and current_r < 0:
        triggers.append(("momentum_reversal", price, current_r))

    # ── Shadow 3: Tight ATR Trail (1.5 ATR) ───────────────────────
    # Tighter than standard vol_trail (2.0-3.0 ATR)
    if atr > 0:
        tight_trail = best_price - atr * 1.5
        if tight_trail > entry_price and low <= tight_trail:
            tight_r = (tight_trail - entry_price) / risk_per_unit
            triggers.append(("tight_atr_trail_1.5", tight_trail, tight_r))

    # ── Shadow 4: Loose Runner Trail (5.0 ATR after 2R) ───────────
    # Only fires after MFE > 2R, uses very wide trail
    mfe = (best_price - entry_price) / risk_per_unit
    if atr > 0 and mfe > 2.0:
        loose_trail = best_price - atr * 5.0
        if loose_trail > entry_price and low <= loose_trail:
            loose_r = (loose_trail - entry_price) / risk_per_unit
            triggers.append(("loose_runner_trail_5.0", loose_trail, loose_r))

    # ── Shadow 5: Breakeven Stop After 0.5R MFE ───────────────────
    # Phase 5/exit_sim OOS winner: test whether earlier breakeven
    # would prevent green trades from round-tripping to full stops.
    if mfe >= 0.5 and low <= entry_price:
        triggers.append(("breakeven_after_0.5R", entry_price, 0.0))

    # ── Shadow 6: Breakeven Stop After 1R MFE ─────────────────────
    # Once MFE hits 1R, move stop to entry price
    if mfe >= 1.0 and low <= entry_price:
        triggers.append(("breakeven_after_1R", entry_price, 0.0))

    # ── Shadow 7: Early Dead Trade Cut ────────────────────────────
    # At bar 15, if unrealized < -0.5R and MFE < 0.1R, trade is dead
    if bars_held >= 15 and current_r < -0.5 and mfe < 0.1:
        triggers.append(("early_dead_cut", price, current_r))

    return triggers


def _simple_atr(highs, lows, closes, period: int) -> float:
    """Simple mean ATR (matches engine implementation)."""
    if len(closes) < 2:
        return 0.0
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        trs.append(tr)
    if len(trs) < period:
        return np.mean(trs) if trs else 0.0
    return np.mean(trs[-period:])


def _ema(values, span: int) -> float:
    """EMA matching engine implementation."""
    if len(values) < 2:
        return values[-1] if len(values) else 0.0
    alpha = 2.0 / (span + 1)
    ema = values[0]
    for v in values[1:]:
        ema = alpha * v + (1 - alpha) * ema
    return ema

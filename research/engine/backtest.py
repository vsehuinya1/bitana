"""
Event-driven backtest engine.

Core features:
- Bar-by-bar processing (no lookahead)
- Multi-timeframe context support
- Position tracking with PnL calculation
- Trade log with full metadata
- Cost model integration
"""
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional
from loguru import logger

from research.engine.costs import apply_costs, CostModel


class Side(Enum):
    LONG = 1
    SHORT = -1


@dataclass
class Position:
    """Active position state."""
    side: Side
    entry_price: float
    entry_time: int
    size: float
    stop_loss: float | None = None
    take_profit: float | None = None
    trailing_stop: float | None = None
    trailing_distance: float | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class Trade:
    """Completed trade record."""
    side: str
    entry_price: float
    exit_price: float
    entry_time: int
    exit_time: int
    size: float
    pnl_gross: float
    pnl_net: float
    cost: float
    hold_bars: int
    exit_reason: str
    metadata: dict = field(default_factory=dict)


class BacktestEngine:
    """
    Event-driven backtest engine.

    Usage:
        engine = BacktestEngine(cost_model=CostModel())
        engine.run(df, signal_fn, exit_fn)
        trades = engine.get_trades()
    """

    def __init__(
        self,
        cost_model: CostModel | None = None,
        initial_capital: float = 10_000.0,
        max_positions: int = 1,
    ):
        self.cost_model = cost_model or CostModel()
        self.initial_capital = initial_capital
        self.max_positions = max_positions

        # State
        self.capital = initial_capital
        self.position: Position | None = None
        self.trades: list[Trade] = []
        self.equity_curve: list[dict] = []
        self._bar_count = 0

    def reset(self):
        """Reset engine state for new run."""
        self.capital = self.initial_capital
        self.position = None
        self.trades = []
        self.equity_curve = []
        self._bar_count = 0

    def run(
        self,
        df: pd.DataFrame,
        signal_fn: Callable[[pd.Series, Optional[Position], dict], dict | None],
        context: dict | None = None,
    ) -> list[Trade]:
        """
        Run backtest on DataFrame.

        Args:
            df: OHLCV DataFrame with features already computed
            signal_fn: Function(bar, position, context) -> action dict or None
                Action dict: {
                    'action': 'buy'|'sell'|'close',
                    'size': float (fraction of capital or fixed),
                    'stop_loss': float (optional),
                    'take_profit': float (optional),
                    'trailing_distance': float (optional),
                    'exit_reason': str (for close actions),
                    'metadata': dict (optional),
                }
            context: Additional context (e.g., higher TF data)

        Returns: List of completed trades
        """
        self.reset()
        context = context or {}

        for idx in range(len(df)):
            bar = df.iloc[idx]
            self._bar_count += 1

            # 1. Check stop/take profit/trailing on current bar
            if self.position:
                exit_triggered = self._check_exits(bar)
                if exit_triggered:
                    continue  # Position was closed, skip signal eval

            # 2. Evaluate signal
            action = signal_fn(bar, self.position, context)

            if action:
                self._process_action(action, bar)

            # 3. Track equity
            unrealized = 0.0
            if self.position:
                price_diff = bar["close"] - self.position.entry_price
                unrealized = price_diff * self.position.size * self.position.side.value

            self.equity_curve.append({
                "timestamp": int(bar.get("timestamp", idx)),
                "equity": self.capital + unrealized,
                "capital": self.capital,
                "in_position": self.position is not None,
            })

        # Close any remaining position at last bar
        if self.position and len(df) > 0:
            self._close_position(df.iloc[-1], "end_of_data")

        return self.trades

    def _check_exits(self, bar: pd.Series) -> bool:
        """Check stop loss, take profit, and trailing stop."""
        pos = self.position
        if not pos:
            return False

        # Update trailing stop
        if pos.trailing_distance and pos.trailing_stop is not None:
            if pos.side == Side.LONG:
                new_trail = bar["high"] - pos.trailing_distance
                if new_trail > pos.trailing_stop:
                    pos.trailing_stop = new_trail
            else:
                new_trail = bar["low"] + pos.trailing_distance
                if new_trail < pos.trailing_stop:
                    pos.trailing_stop = new_trail

        # Check stop loss
        if pos.stop_loss is not None:
            if pos.side == Side.LONG and bar["low"] <= pos.stop_loss:
                self._close_position(bar, "stop_loss", exit_price=pos.stop_loss)
                return True
            elif pos.side == Side.SHORT and bar["high"] >= pos.stop_loss:
                self._close_position(bar, "stop_loss", exit_price=pos.stop_loss)
                return True

        # Check trailing stop
        if pos.trailing_stop is not None:
            if pos.side == Side.LONG and bar["low"] <= pos.trailing_stop:
                self._close_position(bar, "trailing_stop", exit_price=pos.trailing_stop)
                return True
            elif pos.side == Side.SHORT and bar["high"] >= pos.trailing_stop:
                self._close_position(bar, "trailing_stop", exit_price=pos.trailing_stop)
                return True

        # Check take profit
        if pos.take_profit is not None:
            if pos.side == Side.LONG and bar["high"] >= pos.take_profit:
                self._close_position(bar, "take_profit", exit_price=pos.take_profit)
                return True
            elif pos.side == Side.SHORT and bar["low"] <= pos.take_profit:
                self._close_position(bar, "take_profit", exit_price=pos.take_profit)
                return True

        return False

    def _process_action(self, action: dict, bar: pd.Series):
        """Process a signal action."""
        act = action.get("action")

        if act == "close" and self.position:
            self._close_position(bar, action.get("exit_reason", "signal_close"))
            return

        if act in ("buy", "sell") and self.position is None:
            side = Side.LONG if act == "buy" else Side.SHORT
            entry_price = bar["close"]
            size = action.get("size", self.capital * 0.01 / entry_price)

            # Apply entry cost
            entry_cost = apply_costs(entry_price, size, self.cost_model, mode="entry")
            self.capital -= entry_cost

            self.position = Position(
                side=side,
                entry_price=entry_price,
                entry_time=int(bar.get("timestamp", self._bar_count)),
                size=size,
                stop_loss=action.get("stop_loss"),
                take_profit=action.get("take_profit"),
                trailing_distance=action.get("trailing_distance"),
                trailing_stop=(
                    entry_price - action["trailing_distance"]
                    if action.get("trailing_distance") and side == Side.LONG
                    else entry_price + action["trailing_distance"]
                    if action.get("trailing_distance") and side == Side.SHORT
                    else None
                ),
                metadata=action.get("metadata", {}),
            )

    def _close_position(self, bar: pd.Series, reason: str, exit_price: float | None = None):
        """Close the current position and record the trade."""
        if not self.position:
            return

        pos = self.position
        if exit_price is None:
            exit_price = bar["close"]

        # PnL calculation
        price_diff = exit_price - pos.entry_price
        pnl_gross = price_diff * pos.size * pos.side.value

        # Exit costs
        exit_cost = apply_costs(exit_price, pos.size, self.cost_model, mode="exit")
        entry_cost = apply_costs(pos.entry_price, pos.size, self.cost_model, mode="entry")
        total_cost = entry_cost + exit_cost

        pnl_net = pnl_gross - total_cost

        exit_time = int(bar.get("timestamp", self._bar_count))
        hold_bars = self._bar_count  # Approximate

        trade = Trade(
            side=pos.side.name,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            entry_time=pos.entry_time,
            exit_time=exit_time,
            size=pos.size,
            pnl_gross=pnl_gross,
            pnl_net=pnl_net,
            cost=total_cost,
            hold_bars=hold_bars,
            exit_reason=reason,
            metadata=pos.metadata,
        )

        self.trades.append(trade)
        self.capital += pnl_net + (pos.entry_price * pos.size)  # Return capital + PnL
        self.position = None

    def get_trades(self) -> pd.DataFrame:
        """Return trades as DataFrame."""
        if not self.trades:
            return pd.DataFrame()

        return pd.DataFrame([
            {
                "side": t.side,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "entry_time": t.entry_time,
                "exit_time": t.exit_time,
                "size": t.size,
                "pnl_gross": t.pnl_gross,
                "pnl_net": t.pnl_net,
                "cost": t.cost,
                "hold_bars": t.hold_bars,
                "exit_reason": t.exit_reason,
                **t.metadata,
            }
            for t in self.trades
        ])

    def get_equity_curve(self) -> pd.DataFrame:
        """Return equity curve as DataFrame."""
        return pd.DataFrame(self.equity_curve)

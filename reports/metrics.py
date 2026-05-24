"""
Metrics Calculator

Win rate, expectancy, profit factor, Sharpe proxy, etc.
Includes funding fees in all PnL calculations.
"""
from __future__ import annotations

import math
from typing import Optional


class MetricsCalculator:
    """Calculates trading performance metrics from trade records."""

    @staticmethod
    def calculate(trades: list[dict], equity: float = 0) -> dict:
        """Calculate comprehensive metrics from trade records.

        Each trade dict must have at minimum:
        pnl_usd, pnl_r, engine, symbol, hold_time_s, funding_fees
        """
        if not trades:
            return {"total_trades": 0}

        pnls = [t.get("pnl_usd", 0) for t in trades]
        pnl_rs = [t.get("pnl_r", 0) for t in trades]

        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        total = len(pnls)
        win_rate = len(wins) / total if total > 0 else 0
        total_pnl = sum(pnls)
        total_funding = sum(t.get("funding_fees", 0) for t in trades)

        # Profit factor
        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 1
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        # Expectancy in R
        avg_r = sum(pnl_rs) / total if total > 0 else 0

        # Avg hold time
        hold_times = [t.get("hold_time_s", 0) for t in trades]
        avg_hold = sum(hold_times) / total if total > 0 else 0

        # Sharpe-like proxy (annualized R / std of R)
        if len(pnl_rs) > 1:
            mean_r = sum(pnl_rs) / len(pnl_rs)
            var = sum((r - mean_r) ** 2 for r in pnl_rs) / (len(pnl_rs) - 1)
            std_r = math.sqrt(var) if var > 0 else 1
            # Assume ~250 trading days, ~10 trades/day rough estimate
            sharpe_proxy = mean_r / std_r * math.sqrt(min(total, 252))
        else:
            sharpe_proxy = 0

        # Rolling drawdown
        equity_curve = []
        running = equity if equity > 0 else 1000
        peak = running
        max_dd = 0
        for p in pnls:
            running += p
            equity_curve.append(running)
            if running > peak:
                peak = running
            dd = (peak - running) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)

        # Best/worst trades
        best = max(pnls) if pnls else 0
        worst = min(pnls) if pnls else 0

        # Per-engine stats
        engines = set(t.get("engine", "") for t in trades)
        per_engine = {}
        for eng in engines:
            eng_trades = [t for t in trades if t.get("engine") == eng]
            eng_pnls = [t.get("pnl_usd", 0) for t in eng_trades]
            eng_wins = [p for p in eng_pnls if p > 0]
            per_engine[eng] = {
                "trades": len(eng_trades),
                "win_rate": len(eng_wins) / len(eng_trades) if eng_trades else 0,
                "total_pnl": sum(eng_pnls),
                "avg_r": sum(t.get("pnl_r", 0) for t in eng_trades) / len(eng_trades) if eng_trades else 0,
            }

        # Per-symbol stats
        symbols = set(t.get("symbol", "") for t in trades)
        per_symbol = {}
        for sym in symbols:
            sym_trades = [t for t in trades if t.get("symbol") == sym]
            sym_pnls = [t.get("pnl_usd", 0) for t in sym_trades]
            sym_wins = [p for p in sym_pnls if p > 0]
            per_symbol[sym] = {
                "trades": len(sym_trades),
                "win_rate": len(sym_wins) / len(sym_trades) if sym_trades else 0,
                "total_pnl": sum(sym_pnls),
            }

        return {
            "total_trades": total,
            "win_rate": round(win_rate, 4),
            "total_pnl": round(total_pnl, 2),
            "total_funding_fees": round(total_funding, 2),
            "expectancy_r": round(avg_r, 4),
            "profit_factor": round(profit_factor, 4),
            "sharpe_proxy": round(sharpe_proxy, 4),
            "max_drawdown": round(max_dd, 4),
            "avg_hold_time_s": round(avg_hold, 1),
            "best_trade": round(best, 2),
            "worst_trade": round(worst, 2),
            "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
            "per_engine": per_engine,
            "per_symbol": per_symbol,
        }

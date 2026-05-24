"""
Full metrics suite for trade evaluation.

Required metrics:
- PF gross / net
- Expectancy (bps)
- CAGR-style growth
- Max drawdown
- Ulcer index
- Sharpe-like stability
- Max consecutive losers
- Avg winner / loser
- Avg hold
- Trades/day
- Session performance
- Regime performance
"""
import pandas as pd
import numpy as np
from loguru import logger


def compute_metrics(
    trades: pd.DataFrame,
    equity_curve: pd.DataFrame | None = None,
    initial_capital: float = 10_000.0,
) -> dict:
    """
    Compute full metrics suite from trade log.

    Args:
        trades: DataFrame with columns: pnl_gross, pnl_net, side, entry_time, exit_time, etc.
        equity_curve: Optional equity curve DataFrame
        initial_capital: Starting capital

    Returns: Dict of all metrics
    """
    if trades.empty:
        return {"status": "NO_TRADES"}

    m = {}
    n = len(trades)
    m["total_trades"] = n

    # ── PnL ──
    m["total_pnl_gross"] = trades["pnl_gross"].sum()
    m["total_pnl_net"] = trades["pnl_net"].sum()
    m["total_costs"] = trades["cost"].sum()

    # ── Win rate ──
    winners_gross = trades[trades["pnl_gross"] > 0]
    losers_gross = trades[trades["pnl_gross"] <= 0]
    winners_net = trades[trades["pnl_net"] > 0]
    losers_net = trades[trades["pnl_net"] <= 0]

    m["win_rate_gross"] = len(winners_gross) / n * 100
    m["win_rate_net"] = len(winners_net) / n * 100

    # ── Profit Factor ──
    gross_profits = winners_gross["pnl_gross"].sum() if len(winners_gross) > 0 else 0
    gross_losses = abs(losers_gross["pnl_gross"].sum()) if len(losers_gross) > 0 else 0
    m["pf_gross"] = gross_profits / gross_losses if gross_losses > 0 else float("inf")

    net_profits = winners_net["pnl_net"].sum() if len(winners_net) > 0 else 0
    net_losses = abs(losers_net["pnl_net"].sum()) if len(losers_net) > 0 else 0
    m["pf_net"] = net_profits / net_losses if net_losses > 0 else float("inf")

    # ── Expectancy ──
    m["expectancy_gross"] = trades["pnl_gross"].mean()
    m["expectancy_net"] = trades["pnl_net"].mean()

    # Expectancy in bps (using average notional)
    if "entry_price" in trades.columns and "size" in trades.columns:
        avg_notional = (trades["entry_price"] * trades["size"]).mean()
        if avg_notional > 0:
            m["expectancy_bps_gross"] = m["expectancy_gross"] / avg_notional * 10_000
            m["expectancy_bps_net"] = m["expectancy_net"] / avg_notional * 10_000

    # ── Avg winner / loser ──
    m["avg_winner_gross"] = winners_gross["pnl_gross"].mean() if len(winners_gross) > 0 else 0
    m["avg_loser_gross"] = losers_gross["pnl_gross"].mean() if len(losers_gross) > 0 else 0
    m["avg_winner_net"] = winners_net["pnl_net"].mean() if len(winners_net) > 0 else 0
    m["avg_loser_net"] = losers_net["pnl_net"].mean() if len(losers_net) > 0 else 0

    # Win/loss ratio
    if m["avg_loser_gross"] != 0:
        m["win_loss_ratio_gross"] = abs(m["avg_winner_gross"] / m["avg_loser_gross"])
    else:
        m["win_loss_ratio_gross"] = float("inf")

    # ── Hold time ──
    if "entry_time" in trades.columns and "exit_time" in trades.columns:
        hold_ms = trades["exit_time"] - trades["entry_time"]
        m["avg_hold_hours"] = hold_ms.mean() / 3_600_000
        m["median_hold_hours"] = hold_ms.median() / 3_600_000
        m["max_hold_hours"] = hold_ms.max() / 3_600_000

    # ── Consecutive losers ──
    is_loser = (trades["pnl_net"] <= 0).astype(int)
    if len(is_loser) > 0:
        # Count max consecutive losers
        groups = (is_loser != is_loser.shift()).cumsum()
        streak_lengths = is_loser.groupby(groups).sum()
        m["max_consecutive_losers"] = int(streak_lengths.max()) if len(streak_lengths) > 0 else 0

    # ── Trades/day ──
    if "entry_time" in trades.columns and n > 1:
        span_ms = trades["exit_time"].max() - trades["entry_time"].min()
        span_days = span_ms / 86_400_000
        m["trades_per_day"] = n / span_days if span_days > 0 else 0
        m["span_days"] = span_days

    # ── Long vs Short breakdown ──
    if "side" in trades.columns:
        for side in ["LONG", "SHORT"]:
            side_trades = trades[trades["side"] == side]
            if len(side_trades) > 0:
                m[f"{side.lower()}_count"] = len(side_trades)
                m[f"{side.lower()}_pnl_net"] = side_trades["pnl_net"].sum()
                m[f"{side.lower()}_win_rate"] = (side_trades["pnl_net"] > 0).mean() * 100
                m[f"{side.lower()}_avg_pnl"] = side_trades["pnl_net"].mean()

    # ── Equity curve metrics ──
    if equity_curve is not None and not equity_curve.empty:
        eq = equity_curve["equity"]

        # Max drawdown
        rolling_max = eq.cummax()
        drawdown = (eq - rolling_max) / rolling_max * 100
        m["max_drawdown_pct"] = abs(drawdown.min())

        # Ulcer index
        dd_sq = drawdown ** 2
        m["ulcer_index"] = np.sqrt(dd_sq.mean())

        # CAGR-like return
        if len(eq) > 1:
            total_return = eq.iloc[-1] / eq.iloc[0] - 1
            m["total_return_pct"] = total_return * 100

            if "timestamp" in equity_curve.columns:
                span_ms = equity_curve["timestamp"].max() - equity_curve["timestamp"].min()
                years = span_ms / (365.25 * 86_400_000)
                if years > 0:
                    m["cagr_pct"] = ((1 + total_return) ** (1 / years) - 1) * 100

        # Sharpe-like ratio (using equity returns)
        eq_returns = eq.pct_change().dropna()
        if len(eq_returns) > 1 and eq_returns.std() > 0:
            m["sharpe_like"] = eq_returns.mean() / eq_returns.std() * np.sqrt(252 * 24)  # Annualized hourly

    # ── Exit reason breakdown ──
    if "exit_reason" in trades.columns:
        m["exit_reasons"] = trades["exit_reason"].value_counts().to_dict()

    return m


def print_metrics(metrics: dict, title: str = ""):
    """Pretty print metrics."""
    if title:
        logger.info(f"\n{'='*60}")
        logger.info(f"  {title}")
        logger.info(f"{'='*60}")

    if metrics.get("status") == "NO_TRADES":
        logger.info("  No trades to evaluate")
        return

    logger.info(f"  Trades: {metrics.get('total_trades', 0)} "
                f"| Span: {metrics.get('span_days', 0):.0f} days "
                f"| Trades/day: {metrics.get('trades_per_day', 0):.2f}")

    logger.info(f"\n  PnL Gross: {metrics.get('total_pnl_gross', 0):.2f} "
                f"| Net: {metrics.get('total_pnl_net', 0):.2f} "
                f"| Costs: {metrics.get('total_costs', 0):.2f}")

    logger.info(f"  PF Gross: {metrics.get('pf_gross', 0):.2f} "
                f"| PF Net: {metrics.get('pf_net', 0):.2f}")

    logger.info(f"  Win Rate Gross: {metrics.get('win_rate_gross', 0):.1f}% "
                f"| Net: {metrics.get('win_rate_net', 0):.1f}%")

    logger.info(f"  Expectancy (bps): Gross={metrics.get('expectancy_bps_gross', 0):.1f} "
                f"| Net={metrics.get('expectancy_bps_net', 0):.1f}")

    logger.info(f"  Avg Winner: {metrics.get('avg_winner_net', 0):.2f} "
                f"| Avg Loser: {metrics.get('avg_loser_net', 0):.2f} "
                f"| W/L Ratio: {metrics.get('win_loss_ratio_gross', 0):.2f}")

    logger.info(f"  Avg Hold: {metrics.get('avg_hold_hours', 0):.1f}h "
                f"| Max Consecutive Losers: {metrics.get('max_consecutive_losers', 0)}")

    if "max_drawdown_pct" in metrics:
        logger.info(f"\n  Max DD: {metrics.get('max_drawdown_pct', 0):.1f}% "
                    f"| Ulcer: {metrics.get('ulcer_index', 0):.2f} "
                    f"| Sharpe-like: {metrics.get('sharpe_like', 0):.2f}")

    if "cagr_pct" in metrics:
        logger.info(f"  Total Return: {metrics.get('total_return_pct', 0):.1f}% "
                    f"| CAGR: {metrics.get('cagr_pct', 0):.1f}%")

    # Long vs Short
    if "long_count" in metrics or "short_count" in metrics:
        logger.info(f"\n  LONG: {metrics.get('long_count', 0)} trades, "
                    f"PnL={metrics.get('long_pnl_net', 0):.2f}, "
                    f"WR={metrics.get('long_win_rate', 0):.1f}%")
        logger.info(f"  SHORT: {metrics.get('short_count', 0)} trades, "
                    f"PnL={metrics.get('short_pnl_net', 0):.2f}, "
                    f"WR={metrics.get('short_win_rate', 0):.1f}%")

    if "exit_reasons" in metrics:
        logger.info(f"\n  Exit reasons: {metrics['exit_reasons']}")

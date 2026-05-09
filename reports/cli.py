"""
CLI Metrics Display
"""
from __future__ import annotations

from reports.metrics import MetricsCalculator


def print_metrics(trades: list[dict], equity: float = 0) -> str:
    """Format metrics for CLI output."""
    m = MetricsCalculator.calculate(trades, equity)
    if m.get("total_trades", 0) == 0:
        return "No trades recorded."

    lines = [
        "=" * 50,
        "  BITANA TRADING METRICS",
        "=" * 50,
        f"  Total Trades:     {m['total_trades']}",
        f"  Win Rate:         {m['win_rate']:.1%}",
        f"  Total PnL:        ${m['total_pnl']:+.2f}",
        f"  Funding Fees:     ${m['total_funding_fees']:.2f}",
        f"  Expectancy:       {m['expectancy_r']:+.4f}R",
        f"  Profit Factor:    {m['profit_factor']:.2f}",
        f"  Sharpe Proxy:     {m['sharpe_proxy']:.2f}",
        f"  Max Drawdown:     {m['max_drawdown']:.1%}",
        f"  Avg Hold Time:    {m['avg_hold_time_s'] / 60:.1f} min",
        f"  Best Trade:       ${m['best_trade']:+.2f}",
        f"  Worst Trade:      ${m['worst_trade']:+.2f}",
        f"  Avg Win:          ${m['avg_win']:+.2f}",
        f"  Avg Loss:         ${m['avg_loss']:+.2f}",
        "",
    ]

    per_engine = m.get("per_engine", {})
    if per_engine:
        lines.append("  BY ENGINE:")
        for eng, stats in per_engine.items():
            lines.append(
                f"    {eng}: {stats['trades']} trades, "
                f"WR {stats['win_rate']:.0%}, "
                f"PnL ${stats['total_pnl']:+.2f}, "
                f"Avg {stats['avg_r']:+.2f}R"
            )
        lines.append("")

    per_symbol = m.get("per_symbol", {})
    if per_symbol:
        lines.append("  BY SYMBOL:")
        for sym, stats in per_symbol.items():
            lines.append(
                f"    {sym}: {stats['trades']} trades, "
                f"WR {stats['win_rate']:.0%}, "
                f"PnL ${stats['total_pnl']:+.2f}"
            )

    lines.append("=" * 50)
    output = "\n".join(lines)
    print(output)
    return output

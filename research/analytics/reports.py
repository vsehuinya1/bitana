"""
Report generation (text and summary).
"""
import pandas as pd
from pathlib import Path
from loguru import logger
from research.analytics.metrics import compute_metrics, print_metrics
from research.config.settings import REPORTS_DIR


def generate_report(
    trades: pd.DataFrame,
    equity_curve: pd.DataFrame | None = None,
    title: str = "Backtest Report",
    save: bool = True,
) -> dict:
    """Generate and optionally save a performance report."""
    metrics = compute_metrics(trades, equity_curve)
    print_metrics(metrics, title)

    if save:
        out_path = REPORTS_DIR / f"{title.lower().replace(' ', '_')}.txt"
        out_path.parent.mkdir(parents=True, exist_ok=True)

        with open(out_path, "w") as f:
            f.write(f"{'='*60}\n")
            f.write(f"  {title}\n")
            f.write(f"{'='*60}\n\n")

            for k, v in metrics.items():
                if isinstance(v, dict):
                    f.write(f"  {k}:\n")
                    for kk, vv in v.items():
                        f.write(f"    {kk}: {vv}\n")
                else:
                    f.write(f"  {k}: {v}\n")

        logger.info(f"Report saved to {out_path}")

    return metrics

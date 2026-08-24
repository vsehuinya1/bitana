"""Read-only exit-rule simulator.

Replays each trade's bar-by-bar r_path against alternative exit rules to estimate
the R/day uplift of better trade management (vs. the actual realized exit). Validates
the best rule out-of-sample (train on earliest trades, test on most recent).

IMPORTANT — approximations / honesty:
- r_path marks are bar-close (plus running mfe_so_far / mae_so_far). Intrabar stop
  fills are NOT modeled exactly; results are indicative of direction and rough
  magnitude, not tick-accurate.
- Baseline per trade is the ACTUAL realized pnl_r. A rule only changes the outcome
  when it would have fired earlier than the real exit.
- This is observational. Any rule that looks good here must be confirmed via the live
  shadow-exit logging before any engine change.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from research_duo.config_loader import PipelineConfig
from research_duo.experiments.entry_labels import load_labeled_cohort
from research_duo.paths import REPO_ROOT
from research_duo.phase3_io import load_r_path_long


def _sim_breakeven(path: pd.DataFrame, baseline: float, trigger: float) -> float:
    """Once mfe reaches `trigger` R, move stop to entry (0R). Exit at 0R if it pulls back."""
    armed = path["mfe_so_far"] >= trigger
    if not armed.any():
        return baseline
    first = armed.idxmax()
    after = path.loc[first:]
    if (after["unrealized_r"] <= 0.0).any():
        return 0.0
    return baseline


def _sim_trail(path: pd.DataFrame, baseline: float, width: float) -> float:
    """Arm once mfe >= width; exit at (peak − width) on first pullback to that level."""
    armed = path["mfe_so_far"] >= width
    if not armed.any():
        return baseline
    stop = path["mfe_so_far"] - width
    hit = armed & (path["unrealized_r"] <= stop)
    if hit.any():
        i = hit.idxmax()
        return float(stop.loc[i])
    return baseline


def _sim_partial(path: pd.DataFrame, baseline: float, trigger: float, frac: float) -> float:
    """Take `frac` of position at `trigger` R; remainder rides to the actual exit."""
    if (path["mfe_so_far"] >= trigger).any():
        return frac * trigger + (1.0 - frac) * baseline
    return baseline


def _sim_cut_only(path: pd.DataFrame, baseline: float, confirm_by: int,
                  confirm_r: float) -> float:
    """Isolate the early-cut lever: cut unconfirmed trades at bar `confirm_by`;
    confirmed trades ride to their ACTUAL exit (baseline). Measures the value of
    killing the dead alone, independent of any winner-side trail change."""
    p = path.sort_values("bar_index").reset_index(drop=True)
    window = p[p["bar_index"] <= confirm_by]
    if window.empty:
        return baseline
    if not (window["mfe_so_far"] >= confirm_r).any():
        return float(window["unrealized_r"].iloc[-1])
    return baseline


def _sim_runner(path: pd.DataFrame, baseline: float, confirm_by: int,
                confirm_r: float, give_back: float) -> float:
    """Fewer-trades-bigger-winners management.

    Stage 1 (kill the dead): if mfe has not reached `confirm_r` within the first
    `confirm_by` bars, exit at that bar's unrealized_r (cut before the full -1R stop).
    Stage 2 (let it run): once confirmed, lock a breakeven floor and trail `give_back`
    R below the running peak, so EXPLOSIVE runners realize more of their MFE.
    """
    p = path.sort_values("bar_index").reset_index(drop=True)
    window = p[p["bar_index"] <= confirm_by]
    if window.empty:
        return baseline
    if not (window["mfe_so_far"] >= confirm_r).any():
        # Not confirmed in time → cut at the last bar of the confirmation window.
        return float(window["unrealized_r"].iloc[-1])
    armed = p["mfe_so_far"] >= confirm_r
    first = armed.idxmax()
    after = p.loc[first:]
    stop = (after["mfe_so_far"] - give_back).clip(lower=0.0)  # breakeven floor once confirmed
    hit = after["unrealized_r"] <= stop
    if hit.any():
        i = hit.idxmax()
        return float(stop.loc[i])
    return baseline


# Pre-registered rules (fixed params, no tuning) — keyed by name.
RULES: dict[str, Any] = {
    "baseline (actual)": None,
    "breakeven_after_0.5R": lambda p, b: _sim_breakeven(p, b, 0.5),
    "breakeven_after_1.0R": lambda p, b: _sim_breakeven(p, b, 1.0),
    "trail_0.75R": lambda p, b: _sim_trail(p, b, 0.75),
    "trail_1.0R": lambda p, b: _sim_trail(p, b, 1.0),
    "partial50_at_1.0R": lambda p, b: _sim_partial(p, b, 1.0, 0.5),
    # Fewer-trades-bigger-winners: confirm by bar 10 at +0.3R else cut; then run with wide trail.
    "confirm10_cut_only": lambda p, b: _sim_cut_only(p, b, 10, 0.3),
    "confirm10_cut_trail0.75": lambda p, b: _sim_runner(p, b, 10, 0.3, 0.75),
    "confirm10_cut_trail1.25": lambda p, b: _sim_runner(p, b, 10, 0.3, 1.25),
    "confirm8_cut_trail1.0": lambda p, b: _sim_runner(p, b, 8, 0.5, 1.0),
}


def _paths_by_uuid(config: PipelineConfig) -> dict[str, pd.DataFrame]:
    rp = load_r_path_long(config)
    out: dict[str, pd.DataFrame] = {}
    for uuid, g in rp.groupby("trade_uuid"):
        out[uuid] = g.sort_values("bar_index").reset_index(drop=True)
    return out


def _apply_rule(trades: pd.DataFrame, paths: dict[str, pd.DataFrame], rule) -> np.ndarray:
    """Return counterfactual pnl_r per trade for `rule` (None = baseline)."""
    vals = []
    for _, row in trades.iterrows():
        base = float(row["pnl_r"])
        path = paths.get(row["trade_uuid"])
        if rule is None or path is None or path.empty:
            vals.append(base)
        else:
            vals.append(float(rule(path, base)))
    return np.array(vals, dtype=float)


def _span_days(trades: pd.DataFrame) -> float:
    t = pd.to_datetime(trades["entry_time"], utc=True)
    return max((t.max() - t.min()).total_seconds() / 86400, 1 / 24)


def _stats(r: np.ndarray, span: float) -> dict[str, float]:
    return {
        "n": int(len(r)),
        "wr": float((r > 0).mean()) if len(r) else 0.0,
        "total_r": float(r.sum()),
        "r_per_day": float(r.sum() / span),
        "avg_r": float(r.mean()) if len(r) else 0.0,
    }


def _table(trades: pd.DataFrame, paths: dict[str, pd.DataFrame]) -> dict[str, dict[str, float]]:
    span = _span_days(trades)
    base_total = float(trades["pnl_r"].sum())
    rows: dict[str, dict[str, float]] = {}
    for name, rule in RULES.items():
        r = _apply_rule(trades, paths, rule)
        s = _stats(r, span)
        s["uplift_r"] = s["total_r"] - base_total
        rows[name] = s
    return rows


def run_exit_sim(config: PipelineConfig) -> dict[str, Any]:
    paths = _paths_by_uuid(config)
    out: dict[str, Any] = {}
    lines = [
        "# Exit-Rule Simulator (read-only)",
        "",
        "Replays each trade's bar-by-bar r_path against alternative exit rules. Baseline = actual "
        "realized pnl_r. Bar-close approximation; intrabar fills not modeled. Observational only — "
        "confirm via live shadow-exit logging before any engine change.",
        "",
    ]

    for cohort_name, post_gate in [("post_gate", True), ("all_r_path", False)]:
        trades = load_labeled_cohort(config, post_gate_only=post_gate)
        trades = trades.sort_values("entry_time").reset_index(drop=True)
        full = _table(trades, paths)
        out[cohort_name] = {"full": full}

        lines.extend([
            f"## Cohort `{cohort_name}` (N={len(trades)})",
            "",
            "| Exit rule | N | WR | total R | R/day | uplift vs actual |",
            "| --- | --- | --- | --- | --- | --- |",
        ])
        base_total = full["baseline (actual)"]["total_r"]
        for name, s in full.items():
            up = "" if name.startswith("baseline") else f"{s['uplift_r']:+.2f}R"
            lines.append(
                f"| {name} | {s['n']} | {s['wr']:.0%} | {s['total_r']:+.2f}R | "
                f"{s['r_per_day']:+.2f}R | {up} |"
            )
        lines.append("")

        # Out-of-sample: pick best rule on earliest 60%, report on recent 40%.
        n = len(trades)
        n_train = int(n * 0.6)
        train, test = trades.iloc[:n_train], trades.iloc[n_train:]
        if len(test) >= 8 and len(train) >= 8:
            train_tbl = _table(train, paths)
            best = max(train_tbl.items(), key=lambda kv: kv[1]["total_r"])[0]
            test_tbl = _table(test, paths)
            tb = test_tbl[best]
            base = test_tbl["baseline (actual)"]
            out[cohort_name]["oos"] = {"picked": best, "test_rule": tb, "test_baseline": base}
            lines.extend([
                f"### Out-of-sample (`{cohort_name}`)",
                "",
                f"Best rule on earliest {n_train} trades: **{best}**. Applied to most recent {len(test)}:",
                "",
                "| | N | WR | R/day | total R |",
                "| --- | --- | --- | --- | --- |",
                f"| Actual exits | {base['n']} | {base['wr']:.0%} | {base['r_per_day']:+.2f}R | {base['total_r']:+.2f}R |",
                f"| **{best}** | {tb['n']} | {tb['wr']:.0%} | {tb['r_per_day']:+.2f}R | {tb['total_r']:+.2f}R |",
                "",
                f"**OOS uplift: {tb['total_r'] - base['total_r']:+.2f}R "
                f"({tb['r_per_day'] - base['r_per_day']:+.2f}R/day).**",
                "",
                "_If the picked rule is `baseline (actual)`, no simulated rule beat doing nothing on "
                "training data — exit changes are not yet justified._" if best.startswith("baseline")
                else "_Confirm with live shadow logging before changing the engine._",
                "",
            ])
        else:
            lines.extend([
                f"### Out-of-sample (`{cohort_name}`)",
                "",
                f"Underpowered for OOS split (train={len(train)}, test={len(test)}). Need more trades.",
                "",
            ])

    report_dir = REPO_ROOT / "research_duo" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "exit_sim.md"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out["report"] = str(report_path)
    return out

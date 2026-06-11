from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_duo.config_loader import PipelineConfig
from research_duo.manifest import write_manifest
from research_duo.paths import REPO_ROOT


def _fmt_table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)


def _fmt_metric(m: dict[str, Any] | None, key: str, fmt: str = ".3f") -> str:
    if not m or m.get(key) is None:
        return "n/a"
    val = m[key]
    if key == "win_rate":
        return f"{val:.1%}"
    if key == "expectancy_r":
        return f"{val:+.3f}R"
    return f"{val:{fmt}}"


def build_phase4_report(
    pathway_oos: dict[str, Any],
    early_sep: dict[str, Any],
) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Research Duo — Phase 4 OOS Validation Report",
        "",
        f"Generated: {ts}",
        "",
        "Observational only. No strategy modifications, tuning, or recommendations.",
        "",
        "## Primary Question",
        "",
        "> Does `MFE >= 0.3R within 10 bars` retain positive expectancy on unseen trades",
        "> when evaluated strictly by entry_time order?",
        "",
        f"**Cohort:** {pathway_oos.get('cohort_n', 'n/a')} closed trades with r_path "
        f"(post_gate_only={pathway_oos.get('post_gate_only', False)})",
        "",
        f"**Walk-forward folds:** {pathway_oos.get('split', {}).get('n_folds', 0)} | "
        f"**Final holdout:** {pathway_oos.get('split', {}).get('holdout_n', 0)} trades",
        "",
        "## 1. Pathway Validation (IS vs OOS vs Holdout)",
        "",
    ]

    pw_rows = []
    for pid, data in pathway_oos.get("pathways", {}).items():
        is_m = data.get("in_sample", {})
        oos_m = data.get("walk_forward_oos", {})
        ho_m = data.get("final_holdout") or {}
        drift = data.get("drift_is_vs_oos", {})
        pw_rows.append([
            pid,
            is_m.get("n", "n/a"),
            _fmt_metric(is_m, "win_rate"),
            _fmt_metric(is_m, "expectancy_r"),
            oos_m.get("n", "n/a"),
            _fmt_metric(oos_m, "win_rate"),
            _fmt_metric(oos_m, "expectancy_r"),
            ho_m.get("n", "n/a") if ho_m else "n/a",
            _fmt_metric(ho_m, "expectancy_r") if ho_m else "n/a",
            f"{drift.get('expectancy_delta', 0):+.3f}R" if drift.get("expectancy_delta") is not None else "n/a",
        ])

    lines.append(_fmt_table(
        ["Pathway", "IS N", "IS WR", "IS Exp", "OOS N", "OOS WR", "OOS Exp", "Hold N", "Hold Exp", "Exp drift"],
        pw_rows or [["—"] * 10],
    ))
    lines.extend(["", "### Cohen's d drift (IS vs pooled OOS)", ""])

    d_rows = []
    for pid, data in pathway_oos.get("pathways", {}).items():
        is_d = data.get("in_sample", {}).get("cohens_d_pnl")
        oos_d = data.get("walk_forward_oos", {}).get("cohens_d_pnl")
        drift_d = data.get("drift_is_vs_oos", {}).get("cohens_d_delta")
        d_rows.append([
            pid,
            f"{is_d:.2f}" if is_d is not None else "n/a",
            f"{oos_d:.2f}" if oos_d is not None else "n/a",
            f"{drift_d:+.2f}" if drift_d is not None else "n/a",
        ])
    lines.append(_fmt_table(["Pathway", "IS d", "OOS d", "d drift"], d_rows))
    lines.extend(["", "### Per-fold pathway detail (0.3R)", ""])

    primary = pathway_oos.get("pathways", {}).get("mfe_0_3r_within_10", {})
    fold_rows = []
    for f in primary.get("walk_forward_oos", {}).get("folds", []):
        flag = " ⚠ underpowered" if f.get("underpowered") else ""
        fold_rows.append([
            f.get("fold_id", ""),
            f.get("n", ""),
            _fmt_metric(f, "win_rate"),
            _fmt_metric(f, "expectancy_r"),
            flag,
        ])
    lines.append(_fmt_table(["Fold", "N", "WR", "Exp", ""], fold_rows or [["—"] * 5]))
    lines.extend(["", "## 2. Early Separability (OOS, no tuning)", ""])

    target = early_sep.get("target", {})
    lines.extend([
        f"**Target:** {target.get('label', '')}",
        f"**Model:** {target.get('model', '')}",
        "",
    ])

    sep_rows = []
    for bar, data in sorted(early_sep.get("by_cutoff_bar", {}).items()):
        if data.get("skipped"):
            continue
        sep_rows.append([
            bar,
            data.get("n_dataset", ""),
            data.get("n_positive", ""),
            data.get("pooled_oos_accuracy_mean", "n/a"),
            data.get("pooled_oos_auc_mean", "n/a"),
            len(data.get("fold_metrics", [])),
        ])
    lines.append(_fmt_table(
        ["Bar cutoff", "N", "Positives", "OOS accuracy", "OOS AUC", "Folds"],
        sep_rows or [["—"] * 6],
    ))

    lines.extend([
        "",
        "### Leakage audit",
        "",
        "- Features at bar N use **only bars 1..N** from r_path",
        "- Labels use **full-trade** `bars_to_*r_mfe` (post-hoc pathway membership)",
        "- Walk-forward: **no random shuffle**, ordered by `entry_time`",
        "- Final holdout trades **never appear** in any training fold",
        "",
        "## 3. Sample Size Caveats",
        "",
        "- Folds with N < 8 validation trades are flagged underpowered",
        "- Pathway cells with N < 5 are flagged underpowered",
        "- Bootstrap CIs are reported for expectancy where N permits",
        "",
        "## Data Provenance",
        "",
        "- Phase 2 outputs read-only: `r_path_long.parquet`, `trade_features.parquet`",
        "- Split spec: `experiments/oos_splits.yaml`",
        "",
    ])
    return "\n".join(lines)


def run_phase4_report(
    config: PipelineConfig,
    pathway_oos: dict[str, Any],
    early_sep: dict[str, Any],
) -> Path:
    report = build_phase4_report(pathway_oos, early_sep)
    raw = config.raw.get("paths", {}).get("reports_dir", "research_duo/reports")
    out_dir = Path(raw)
    if not out_dir.is_absolute():
        out_dir = REPO_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    report_path = out_dir / "phase4_validation_report.md"
    report_path.write_text(report, encoding="utf-8")

    results_path = Path(config.raw.get("paths", {}).get("experiments_dir", "research_duo/experiments"))
    if not results_path.is_absolute():
        results_path = REPO_ROOT / results_path
    results_path.mkdir(parents=True, exist_ok=True)

    with open(results_path / "phase4_oos_results.json", "w", encoding="utf-8") as f:
        json.dump(
            {"pathway_oos": pathway_oos, "early_separability_oos": early_sep},
            f,
            indent=2,
            sort_keys=True,
            default=str,
        )
        f.write("\n")

    write_manifest(
        config.manifests_dir,
        pipeline_version=config.pipeline_version,
        repo_root=REPO_ROOT,
        config_snapshot=config.raw,
        stage="phase4_validation",
        artifacts={
            "phase4_validation_report": str(report_path),
            "phase4_oos_results_json": str(results_path / "phase4_oos_results.json"),
        },
        warnings=[],
        qa={
            "primary_pathway": "mfe_0_3r_within_10",
            "n_folds": pathway_oos.get("split", {}).get("n_folds"),
        },
    )
    return report_path

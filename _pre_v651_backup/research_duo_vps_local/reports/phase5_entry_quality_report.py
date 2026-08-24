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


def _answer_question(cohort: dict[str, Any]) -> list[str]:
    """Synthesize observational answer from feature comparison."""
    fc = cohort.get("feature_comparison", {})
    lines = []

    present_in_explosive = []
    absent_in_early_dead = []

    checks = [
        ("cascade_strength", "Higher cascade strength"),
        ("breakout_distance_pct", "Higher BD % (closer above range)"),
        ("imbalance_z", "Higher imbalance z"),
        ("vol_z", "Higher vol z"),
        ("confirm_vol", "Volume confirmation"),
        ("confirm_momentum", "Momentum confirmation"),
    ]

    for key, desc in checks:
        item = fc.get(key, {})
        if not item:
            continue
        d = item.get("cohens_d")
        if d is not None and d > 0.2:
            present_in_explosive.append(f"{desc} (d={d:+.2f})")
        exp_rate = item.get("explosive_rate")
        dead_rate = item.get("early_dead_rate")
        if exp_rate is not None and dead_rate is not None and exp_rate > dead_rate + 0.1:
            present_in_explosive.append(f"{desc}: EXPLOSIVE {exp_rate:.0%} vs EARLY_DEAD {dead_rate:.0%}")

    sep = cohort.get("earliest_separation", {}).get("entry", {}).get("features", [])
    top = [f for f in sep if f.get("cohens_d") and abs(f["cohens_d"]) >= 0.3][:5]
    if top:
        lines.append("**Strongest entry separators (|d| >= 0.3):**")
        for f in top:
            lines.append(f"- `{f['feature']}`: d={f['cohens_d']:+.2f}, AUC={f.get('univariate_auc', 'n/a')}")

    if present_in_explosive:
        lines.append("")
        lines.append("**Present more often / higher in EXPLOSIVE vs EARLY_DEAD:**")
        for p in present_in_explosive[:8]:
            lines.append(f"- {p}")

    cascade = cohort.get("cascade_quality", {})
    if cascade.get("decile_table"):
        high = cascade["decile_table"][-1] if cascade["decile_table"] else {}
        low = cascade["decile_table"][0] if cascade["decile_table"] else {}
        if high and low:
            lines.append("")
            lines.append(
                f"**Cascade:** highest decile explosive rate {high.get('explosive_rate', 0):.0%} "
                f"vs lowest {low.get('explosive_rate', 0):.0%}; "
                f"early_dead highest decile {high.get('early_dead_rate', 0):.0%} "
                f"vs lowest {low.get('early_dead_rate', 0):.0%}."
            )

    if not lines:
        lines.append("_Insufficient separation or sample size for confident characterization._")

    return lines


def build_phase5_report(results: dict[str, Any]) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Research Duo — Phase 5 Entry Quality Report",
        "",
        f"Generated: {ts}",
        "",
        "Observational only. No engine modifications, optimization, or recommendations.",
        "",
        "## Label Definitions",
        "",
    ]
    for k, v in results.get("label_definitions", {}).items():
        lines.append(f"- **{k}:** {v}")
    lines.append("")

    for cohort_name, cohort in results.get("cohorts", {}).items():
        lines.extend([f"## Cohort: `{cohort_name}`", ""])
        counts = cohort.get("class_counts", {})
        lines.append(f"**N={cohort.get('n', 0)}** | " + ", ".join(f"{k}: {v}" for k, v in counts.items()))
        lines.append("")

        qa = cohort.get("confirmation_integrity", {})
        lines.extend(["### Confirmation Integrity QA", ""])
        for k, v in qa.items():
            lines.append(f"- {k}: {v}")
        lines.append("")

        lines.extend(["### Feature Comparison (EXPLOSIVE vs EARLY_DEAD)", ""])
        fc = cohort.get("feature_comparison", {})
        num_rows = []
        for key, val in fc.items():
            if "cohens_d" in val:
                num_rows.append([
                    key,
                    val.get("explosive_mean", "n/a"),
                    val.get("early_dead_mean", "n/a"),
                    f"{val.get('cohens_d', 0):+.2f}" if val.get("cohens_d") is not None else "n/a",
                ])
            elif "explosive_rate" in val:
                num_rows.append([
                    key,
                    f"{val.get('explosive_rate', 0):.0%}",
                    f"{val.get('early_dead_rate', 0):.0%}",
                    "—",
                ])
        lines.append(_fmt_table(["Feature", "EXP mean/rate", "DEAD mean/rate", "Cohen's d"], num_rows[:20]))
        lines.append("")

        for horizon in ("entry", "bar_3", "bar_5"):
            sep = cohort.get("earliest_separation", {}).get(horizon, {})
            feats = sep.get("features", [])[:8]
            if not feats:
                continue
            lines.extend([f"### Earliest Separation — {horizon} (top features)", ""])
            rows = [
                [
                    f["feature"],
                    f"{f.get('cohens_d', 0):+.2f}" if f.get("cohens_d") is not None else "n/a",
                    f"{f.get('mutual_information', 0):.3f}" if f.get("mutual_information") is not None else "n/a",
                    f"{f.get('univariate_auc', 0):.3f}" if f.get("univariate_auc") is not None else "n/a",
                ]
                for f in feats
            ]
            lines.append(_fmt_table(["Feature", "Cohen's d", "MI", "AUC"], rows))
            lines.append("")

        lines.extend(["### Confirmation Stack", ""])
        stack = cohort.get("confirmation_stack", {})
        srows = []
        for key, val in {**stack.get("single", {}), **stack.get("combinations", {})}.items():
            srows.append([
                key,
                val.get("n", 0),
                f"{val.get('win_rate', 0):.0%}" if val.get("win_rate") is not None else "n/a",
                f"{val.get('explosive_rate', 0):.0%}" if val.get("explosive_rate") is not None else "n/a",
                f"{val.get('early_dead_rate', 0):.0%}" if val.get("early_dead_rate") is not None else "n/a",
            ])
        lines.append(_fmt_table(["Confirm/combo", "N", "WR", "EXP rate", "EARLY_DEAD rate"], srows))
        lines.append("")

        bd = cohort.get("breakout_expansion", {}).get("bd_bucket_table", [])
        if bd:
            lines.extend(["### Breakout Distance Buckets", ""])
            brows = [
                [
                    r["bd_bucket"],
                    r["n"],
                    f"{r.get('explosive_rate', 0):.0%}",
                    f"{r.get('early_dead_rate', 0):.0%}",
                    r.get("mean_first10_mfe"),
                ]
                for r in bd
            ]
            lines.append(_fmt_table(["BD bucket", "N", "EXP rate", "EARLY_DEAD rate", "Mean MFE@10"], brows))
            lines.append("")

        casc = cohort.get("cascade_quality", {}).get("decile_table", [])
        if casc:
            lines.extend(["### Cascade Strength Deciles", ""])
            crows = [
                [
                    r["cascade_decile"],
                    r["n"],
                    f"{r.get('explosive_rate', 0):.0%}",
                    f"{r.get('early_dead_rate', 0):.0%}",
                ]
                for r in casc
            ]
            lines.append(_fmt_table(["Decile", "N", "EXP rate", "EARLY_DEAD rate"], crows))
            lines.append("")

        conf = cohort.get("symbol_tier_confound", {})
        if conf.get("by_tier"):
            lines.extend(["### Class Counts by Tier", ""])
            for row in conf["by_tier"]:
                lines.append(f"- {row}")
            lines.append("")

        lines.extend([
            f"### Answer: What differs at entry? (`{cohort_name}`)",
            "",
        ])
        lines.extend(_answer_question(cohort))
        lines.append("")

    lines.extend([
        "## Data Provenance",
        "",
        "- Phase 2 outputs unmodified: `trades_reconstructed.parquet`, `r_path_long.parquet`, `trade_features.parquet`",
        "",
    ])
    return "\n".join(lines)


def run_phase5_report(config: PipelineConfig, results: dict[str, Any]) -> Path:
    report = build_phase5_report(results)
    raw = config.raw.get("paths", {}).get("reports_dir", "research_duo/reports")
    out_dir = Path(raw)
    if not out_dir.is_absolute():
        out_dir = REPO_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    report_path = out_dir / "phase5_entry_quality_report.md"
    report_path.write_text(report, encoding="utf-8")

    exp_raw = config.raw.get("paths", {}).get("experiments_dir", "research_duo/experiments")
    exp_dir = Path(exp_raw)
    if not exp_dir.is_absolute():
        exp_dir = REPO_ROOT / exp_dir
    with open(exp_dir / "phase5_entry_quality_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, sort_keys=True, default=str)
        f.write("\n")

    write_manifest(
        config.manifests_dir,
        pipeline_version=config.pipeline_version,
        repo_root=REPO_ROOT,
        config_snapshot=config.raw,
        stage="phase5_entry_quality",
        artifacts={
            "phase5_entry_quality_report": str(report_path),
            "phase5_results_json": str(exp_dir / "phase5_entry_quality_results.json"),
        },
        warnings=[],
        qa={"cohorts": list(results.get("cohorts", {}).keys())},
    )
    return report_path

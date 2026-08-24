from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_duo.config_loader import PipelineConfig
from research_duo.manifest import write_manifest
from research_duo.paths import REPO_ROOT


def _reports_dir(config: PipelineConfig) -> Path:
    raw = config.raw.get("paths", {}).get("reports_dir", "research_duo/reports")
    path = Path(raw)
    return path if path.is_absolute() else REPO_ROOT / path


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _fmt_table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)


def build_phase3_report(
    clustering_qa: dict[str, Any],
    classifier_qa: dict[str, Any],
    pathways_qa: dict[str, Any],
) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Research Duo — Phase 3 Observational Report",
        "",
        f"Generated: {ts}",
        "",
        "This report is purely observational. No strategy modifications or recommendations.",
        "",
        "## 1. Cluster Stability",
        "",
    ]

    ablation = clustering_qa.get("ablation_stability_k3_kmeans", {})
    ab_rows = []
    for name, data in ablation.items():
        if data.get("skipped"):
            ab_rows.append([name, "skipped", data.get("reason", ""), ""])
        else:
            ab_rows.append([
                name,
                data.get("features_remaining", ""),
                f"{data.get('adjusted_rand_index_vs_full_kmeans_k3', 0):.3f}",
                len(data.get("features_removed", [])),
            ])
    lines.append(_fmt_table(
        ["Ablation", "Features remaining", "ARI vs full k=3 k-means", "Features removed"],
        ab_rows or [["—", "—", "—", "—"]],
    ))
    lines.append("")

    metrics = clustering_qa.get("cluster_metrics", {}).get("algorithms", {})
    sil_rows = []
    for algo, data in sorted(metrics.items()):
        if "silhouette" in data:
            sil_rows.append([algo, f"{data['silhouette']:.3f}" if data["silhouette"] is not None else "n/a"])
    lines.extend(["### Silhouette scores", ""])
    lines.append(_fmt_table(["Algorithm", "Silhouette"], sil_rows or [["—", "—"]]))
    lines.extend([
        "",
        f"Trades clustered: {clustering_qa.get('n_trades_clustered', 'n/a')}",
        f"Features used: {clustering_qa.get('n_features', 'n/a')}",
        "",
        "## 2. Early Prediction Accuracy (bar 10 cutoff)",
        "",
    ])

    label_def = classifier_qa.get("label_definition", {})
    lines.extend([
        f"**Label:** {label_def.get('positive_class', 'EXPLOSIVE')} — {label_def.get('rule', '')}",
        f"**Features:** bars 1–{label_def.get('feature_cutoff_bar', 10)} only",
        "",
    ])

    models = classifier_qa.get("models", {})
    acc_rows = []
    for model_name in ("logistic_regression", "random_forest"):
        m = models.get(model_name, {})
        if not m or model_name == "feature_names":
            continue
        acc_rows.append([
            model_name,
            f"{m.get('accuracy', 0):.3f}",
            f"{m.get('precision', 0):.3f}",
            f"{m.get('recall', 0):.3f}",
            f"{m.get('f1', 0):.3f}",
            f"{m.get('roc_auc', 'n/a')}",
        ])
    lines.append(_fmt_table(
        ["Model", "Accuracy", "Precision", "Recall", "F1", "ROC-AUC"],
        acc_rows or [["—", "—", "—", "—", "—", "—"]],
    ))
    lines.extend([
        "",
        f"Samples: {models.get('n_samples', 'n/a')} "
        f"(positive: {models.get('n_positive', 'n/a')}, negative: {models.get('n_negative', 'n/a')})",
        "",
        "## 3. Feature Importance Ranking",
        "",
    ])

    rf = models.get("random_forest", {})
    ranking = rf.get("feature_importance_ranking", [])
    if ranking:
        imp_rows = [[i + 1, name, f"{score:.4f}"] for i, (name, score) in enumerate(ranking[:15])]
        lines.append(_fmt_table(["Rank", "Feature", "Importance"], imp_rows))
    else:
        lr = models.get("logistic_regression", {})
        imp = lr.get("feature_importance", {})
        if imp:
            sorted_imp = sorted(imp.items(), key=lambda x: -abs(x[1]))[:15]
            imp_rows = [[i + 1, name, f"{score:.4f}"] for i, (name, score) in enumerate(sorted_imp)]
            lines.append(_fmt_table(["Rank", "Feature", "Coefficient"], imp_rows))
        else:
            lines.append("_No feature importance available._")

    lines.extend(["", "## 4. Pathway Separability", ""])

    pathways = pathways_qa.get("pathways", {})
    pw_rows = []
    for key, p in pathways.items():
        pw_rows.append([
            p.get("pathway", key),
            p.get("n", ""),
            f"{p.get('win_rate', 0):.1%}" if p.get("win_rate") is not None else "n/a",
            f"{p.get('expectancy_r', 0):+.3f}R" if p.get("expectancy_r") is not None else "n/a",
            f"{p.get('cohens_d_pnl_vs_complement', 0):.2f}" if p.get("cohens_d_pnl_vs_complement") is not None else "n/a",
        ])
    lines.append(_fmt_table(
        ["Pathway", "N", "WR", "Expectancy", "Cohen's d vs complement"],
        pw_rows or [["—", "—", "—", "—", "—"]],
    ))

    sep = pathways_qa.get("separability", {})
    if sep:
        lines.extend(["", "### Separability on observables", ""])
        sep_rows = []
        for key, s in sep.items():
            sep_rows.append([
                key,
                f"{s.get('cohens_d_pnl_r', 'n/a')}",
                f"{s.get('cohens_d_max_mfe', 'n/a')}",
                f"{s.get('cohens_d_max_mae', 'n/a')}",
            ])
        lines.append(_fmt_table(
            ["Pathway", "d(pnl_r)", "d(max_mfe)", "d(max_mae)"],
            sep_rows,
        ))

    lines.extend([
        "",
        "## Data Provenance",
        "",
        "- Inputs: `r_path_long.parquet`, `trade_tensor.parquet`, `trade_features.parquet` (Phase 2, unmodified)",
        "- Phase 2 outputs were not modified by this analysis.",
        "",
    ])
    return "\n".join(lines)


def run_phase3_report(
    config: PipelineConfig,
    clustering_qa: dict[str, Any],
    classifier_qa: dict[str, Any],
    pathways_qa: dict[str, Any],
) -> Path:
    report = build_phase3_report(clustering_qa, classifier_qa, pathways_qa)
    out_dir = _reports_dir(config)
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / "phase3_report.md"
    output_path.write_text(report, encoding="utf-8")

    write_manifest(
        config.manifests_dir,
        pipeline_version=config.pipeline_version,
        repo_root=REPO_ROOT,
        config_snapshot=config.raw,
        stage="phase3_report",
        artifacts={"phase3_report_md": str(output_path)},
        warnings=[],
        qa={"report_sections": ["cluster_stability", "early_prediction", "feature_importance", "pathway_separability"]},
    )
    return output_path

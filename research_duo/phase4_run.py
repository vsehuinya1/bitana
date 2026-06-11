from __future__ import annotations

from pathlib import Path

from research_duo.config_loader import PipelineConfig
from research_duo.experiments.early_separability_oos import run_early_separability_oos
from research_duo.experiments.pathway_oos import run_pathway_oos
from research_duo.paths import REPO_ROOT
from research_duo.reports.phase4_validation_report import run_phase4_report


def run_phase4(config: PipelineConfig) -> dict:
    pathway_results = run_pathway_oos(config)
    early_results = run_early_separability_oos(config)
    report_path = run_phase4_report(config, pathway_results, early_results)

    exp_raw = config.raw.get("paths", {}).get("experiments_dir", "research_duo/experiments")
    exp_dir = Path(exp_raw)
    if not exp_dir.is_absolute():
        exp_dir = REPO_ROOT / exp_dir
    return {
        "phase4_oos_results": exp_dir / "phase4_oos_results.json",
        "phase4_validation_report": report_path,
    }

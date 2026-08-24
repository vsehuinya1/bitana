from __future__ import annotations

from research_duo.config_loader import PipelineConfig
from research_duo.experiments.entry_quality import run_entry_quality
from research_duo.reports.phase5_entry_quality_report import run_phase5_report


def run_phase5(config: PipelineConfig) -> dict:
    results = run_entry_quality(config)
    report_path = run_phase5_report(config, results)
    return {
        "phase5_entry_quality_report": report_path,
        "phase5_results_json": config.raw.get("paths", {}).get(
            "experiments_dir", "research_duo/experiments"
        ),
    }

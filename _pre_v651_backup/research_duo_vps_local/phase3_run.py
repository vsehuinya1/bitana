from __future__ import annotations

from research_duo.config_loader import PipelineConfig
from research_duo.reports.phase3_report import run_phase3_report


def run_phase3(config: PipelineConfig) -> dict:
    from research_duo.clustering.runner import run_clustering
    from research_duo.experiments.pathways import run_pathways
    from research_duo.states.early_classifier import run_early_classifier

    cluster_path, _, clustering_qa = run_clustering(config)
    classifier_path, classifier_qa = run_early_classifier(config)
    pathways_path, pathways_qa = run_pathways(config)
    report_path = run_phase3_report(config, clustering_qa, classifier_qa, pathways_qa)

    return {
        "cluster_labels": cluster_path,
        "early_classifier_results": classifier_path,
        "pathways_results": pathways_path,
        "phase3_report": report_path,
    }

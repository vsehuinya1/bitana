from __future__ import annotations

import argparse
import sys
from pathlib import Path

from research_duo import __version__
from research_duo.config_loader import load_config
from research_duo.paths import DEFAULT_CONFIG_PATH


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="research_duo",
        description="Read-only Bitana analytics pipeline",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Path to pipeline.yaml",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("discover", help="Introspect v5/v6 databases and write schema manifest")
    sub.add_parser("trades", help="Reconstruct trades dataset from v5 + v6 telemetry")
    sub.add_parser("r_path", help="Reconstruct r_path long-format trajectories + QA")
    sub.add_parser("tensor", help="Build fixed-length trade tensor parquet")
    sub.add_parser("features", help="Extract per-trade scalar features")
    sub.add_parser("phase1", help="Run discover + trades")
    sub.add_parser("phase2", help="Run r_path + tensor + features (requires phase1 trades)")
    sub.add_parser("phase3", help="Exploratory analytics: clustering, classifier, pathways, report")
    sub.add_parser("phase4", help="OOS validation: walk-forward pathways + early separability")
    sub.add_parser("phase5", help="Entry quality: EXPLOSIVE vs EARLY_DEAD investigation")
    sub.add_parser("compression", help="Minimal entry compression test (EXPLOSIVE vs EARLY_DEAD)")
    sub.add_parser("exit_sim", help="Read-only exit-rule simulator with OOS validation")
    sub.add_parser("clustering", help="Run clustering only")
    sub.add_parser("early_classifier", help="Run bar-10 EXPLOSIVE classifier only")
    sub.add_parser("pathways", help="Run pathway separability analysis only")
    sub.add_parser("all", help="Run full pipeline: phase1 + phase2")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    config = load_config(args.config)

    if args.command == "discover":
        from research_duo.db.discovery import run_discovery

        manifest = run_discovery(config)
        print(f"discovery manifest: {manifest}")
        return 0

    if args.command == "trades":
        from research_duo.pipeline.trades import run_trades

        parquet_path, manifest = run_trades(config)
        print(f"trades parquet: {parquet_path}")
        print(f"trades manifest: {manifest}")
        return 0

    if args.command == "r_path":
        from research_duo.pipeline.r_path import run_r_path

        parquet_path, manifest = run_r_path(config)
        print(f"r_path parquet: {parquet_path}")
        print(f"r_path manifest: {manifest}")
        return 0

    if args.command == "tensor":
        from research_duo.pipeline.tensor import run_tensor

        parquet_path, manifest = run_tensor(config)
        print(f"tensor parquet: {parquet_path}")
        print(f"tensor manifest: {manifest}")
        return 0

    if args.command == "features":
        from research_duo.pipeline.features import run_features

        parquet_path, manifest = run_features(config)
        print(f"features parquet: {parquet_path}")
        print(f"features manifest: {manifest}")
        return 0

    if args.command == "phase1":
        from research_duo.db.discovery import run_discovery
        from research_duo.pipeline.trades import run_trades

        print(f"discovery manifest: {run_discovery(config)}")
        parquet_path, trades_manifest = run_trades(config)
        print(f"trades parquet: {parquet_path}")
        print(f"trades manifest: {trades_manifest}")
        return 0

    if args.command == "phase2":
        from research_duo.pipeline.features import run_features
        from research_duo.pipeline.r_path import run_r_path
        from research_duo.pipeline.tensor import run_tensor

        rp, m1 = run_r_path(config)
        print(f"r_path parquet: {rp}")
        print(f"r_path manifest: {m1}")
        tp, m2 = run_tensor(config)
        print(f"tensor parquet: {tp}")
        print(f"tensor manifest: {m2}")
        fp, m3 = run_features(config)
        print(f"features parquet: {fp}")
        print(f"features manifest: {m3}")
        return 0

    if args.command == "phase3":
        from research_duo.phase3_run import run_phase3

        outputs = run_phase3(config)
        for key, path in outputs.items():
            print(f"{key}: {path}")
        return 0

    if args.command == "phase4":
        from research_duo.phase4_run import run_phase4

        outputs = run_phase4(config)
        for key, path in outputs.items():
            print(f"{key}: {path}")
        return 0

    if args.command == "phase5":
        from research_duo.phase5_run import run_phase5

        outputs = run_phase5(config)
        for key, path in outputs.items():
            print(f"{key}: {path}")
        return 0

    if args.command == "compression":
        from research_duo.experiments.entry_quality import run_entry_compression_test

        result = run_entry_compression_test(config)
        print(f"verdict: {result['primary_verdict']}")
        print(f"report: {result['report']}")
        return 0

    if args.command == "exit_sim":
        from research_duo.experiments.exit_sim import run_exit_sim

        result = run_exit_sim(config)
        print(f"report: {result['report']}")
        return 0

    if args.command == "clustering":
        from research_duo.clustering.runner import run_clustering

        path, manifest, _ = run_clustering(config)
        print(f"cluster_labels: {path}")
        print(f"manifest: {manifest}")
        return 0

    if args.command == "early_classifier":
        from research_duo.states.early_classifier import run_early_classifier

        path, _ = run_early_classifier(config)
        print(f"early_classifier_results: {path}")
        return 0

    if args.command == "pathways":
        from research_duo.experiments.pathways import run_pathways

        path, _ = run_pathways(config)
        print(f"pathways_results: {path}")
        return 0

    if args.command == "all":
        from research_duo.db.discovery import run_discovery
        from research_duo.pipeline.features import run_features
        from research_duo.pipeline.r_path import run_r_path
        from research_duo.pipeline.tensor import run_tensor
        from research_duo.pipeline.trades import run_trades

        print(f"discovery manifest: {run_discovery(config)}")
        parquet_path, trades_manifest = run_trades(config)
        print(f"trades parquet: {parquet_path}")
        print(f"trades manifest: {trades_manifest}")
        rp, m1 = run_r_path(config)
        print(f"r_path parquet: {rp}")
        print(f"r_path manifest: {m1}")
        tp, m2 = run_tensor(config)
        print(f"tensor parquet: {tp}")
        print(f"tensor manifest: {m2}")
        fp, m3 = run_features(config)
        print(f"features parquet: {fp}")
        print(f"features manifest: {m3}")
        return 0

    parser.error(f"unknown command: {args.command}")
    return 1


if __name__ == "__main__":
    sys.exit(main())

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from research_duo.config_loader import PipelineConfig
from research_duo.manifest import write_manifest
from research_duo.paths import REPO_ROOT
from research_duo.phase3_io import load_trade_features, phase3_settings


def _experiments_dir(config: PipelineConfig) -> Path:
    raw = config.raw.get("paths", {}).get("experiments_dir", "research_duo/experiments")
    path = Path(raw)
    return path if path.is_absolute() else REPO_ROOT / path


def _cohens_d(a: np.ndarray, b: np.ndarray) -> float | None:
    if len(a) < 2 or len(b) < 2:
        return None
    mean_a, mean_b = float(a.mean()), float(b.mean())
    var_a, var_b = float(a.var(ddof=1)), float(b.var(ddof=1))
    pooled = np.sqrt(((len(a) - 1) * var_a + (len(b) - 1) * var_b) / (len(a) + len(b) - 2))
    if pooled == 0:
        return None
    return (mean_a - mean_b) / pooled


def _pathway_metrics(df: pd.DataFrame, mask: pd.Series, pathway_name: str) -> dict[str, Any]:
    sub = df[mask.fillna(False)]
    rest = df[~mask.fillna(False)]
    pnls_in = sub["pnl_r"].dropna().to_numpy(dtype=float)
    pnls_out = rest["pnl_r"].dropna().to_numpy(dtype=float)

    wins = pnls_in[pnls_in > 0]
    losses = pnls_in[pnls_in <= 0]

    return {
        "pathway": pathway_name,
        "n": int(len(sub)),
        "n_complement": int(len(rest)),
        "win_rate": float((pnls_in > 0).mean()) if len(pnls_in) else None,
        "expectancy_r": float(pnls_in.mean()) if len(pnls_in) else None,
        "total_r": float(pnls_in.sum()) if len(pnls_in) else None,
        "avg_win_r": float(wins.mean()) if len(wins) else None,
        "avg_loss_r": float(losses.mean()) if len(losses) else None,
        "complement_expectancy_r": float(pnls_out.mean()) if len(pnls_out) else None,
        "cohens_d_pnl_vs_complement": _cohens_d(pnls_in, pnls_out),
    }


def run_pathways(config: PipelineConfig) -> tuple[Path, dict[str, Any]]:
    settings = phase3_settings(config)
    pw = settings.get("pathways", {})

    confirmed_mfe = float(pw.get("confirmed_mfe_r", 0.3))
    confirmed_bars = int(pw.get("confirmed_max_bars", 10))
    recovery_min = float(pw.get("recovery_min_r", 1.0))

    features = load_trade_features(config)
    with_path = features[features["path_bars"].notna() & (features["path_bars"] > 0)].copy()

    # Pathway 1: MFE >= 0.3R within 10 bars (real-time detectable via bars_to_0_3r_mfe)
    col_mfe = "bars_to_0_3r_mfe"
    if col_mfe in with_path.columns:
        confirmed_mask = with_path[col_mfe].notna() & (with_path[col_mfe] <= confirmed_bars)
    else:
        confirmed_mask = pd.Series(False, index=with_path.index)

    # Pathway 2: recovery > 1R (post-hoc only — uses full-path mae_recovery)
    recovery_mask = with_path["mae_recovery"].notna() & (with_path["mae_recovery"] >= recovery_min)

    pathways = {
        "confirmed_mfe_0_3r_within_10_bars": _pathway_metrics(
            with_path,
            confirmed_mask,
            f"MFE>={confirmed_mfe}R within {confirmed_bars} bars",
        ),
        "high_recovery_gt_1r_posthoc": _pathway_metrics(
            with_path,
            recovery_mask,
            f"mae_recovery>={recovery_min}R (post-hoc)",
        ),
    }

    # Separability: compare pathway vs complement on key observables
    separability: dict[str, Any] = {}
    for key, metrics in pathways.items():
        mask = confirmed_mask if "confirmed" in key else recovery_mask
        in_grp = with_path[mask.fillna(False)]
        out_grp = with_path[~mask.fillna(False)]
        separability[key] = {
            "cohens_d_pnl_r": metrics.get("cohens_d_pnl_vs_complement"),
            "cohens_d_max_mfe": _cohens_d(
                in_grp["max_mfe"].dropna().to_numpy(),
                out_grp["max_mfe"].dropna().to_numpy(),
            ),
            "cohens_d_max_mae": _cohens_d(
                in_grp["max_mae"].dropna().to_numpy(),
                out_grp["max_mae"].dropna().to_numpy(),
            ),
        }

    qa: dict[str, Any] = {
        "pathways": pathways,
        "separability": separability,
        "cohort": "closed trades with r_path telemetry",
        "n_trades": int(len(with_path)),
        "note": "Observational metrics only. No recommendations or rule generation.",
    }

    out_dir = _experiments_dir(config)
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / "pathways_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(qa, f, indent=2, sort_keys=True)
        f.write("\n")

    manifest_path = write_manifest(
        config.manifests_dir,
        pipeline_version=config.pipeline_version,
        repo_root=REPO_ROOT,
        config_snapshot=config.raw,
        stage="pathways",
        artifacts={"pathways_results_json": str(output_path)},
        warnings=[],
        qa=qa,
    )
    return output_path, qa

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from research_duo.config_loader import PipelineConfig
from research_duo.experiments.oos_common import (
    bars_to_column,
    bootstrap_expectancy_ci,
    cohens_d,
    load_cohort_frame,
    pathway_mask,
    pathway_metrics,
    phase4_settings,
)
from research_duo.experiments.walk_forward import build_walk_forward_folds


def _continuous_bars_stats(df: pd.DataFrame, threshold_r: float) -> dict[str, Any]:
    col = bars_to_column(threshold_r)
    if col not in df.columns:
        return {"available": False}
    vals = df[col].dropna()
    return {
        "available": True,
        "median": float(vals.median()) if len(vals) else None,
        "mean": float(vals.mean()) if len(vals) else None,
        "never_reached_pct": float((df[col].isna()).mean()),
    }


def run_pathway_oos(config: PipelineConfig) -> dict[str, Any]:
    settings = phase4_settings(config)
    pw_cfg = settings.get("pathways", {})
    thresholds = pw_cfg.get("thresholds", [0.3, 0.5])
    max_bars = int(pw_cfg.get("max_bars", 10))
    min_val_n = int(settings.get("min_fold_validation_n", 8))
    min_pw_n = int(settings.get("min_pathway_n", 5))
    bootstrap_n = int(settings.get("bootstrap_samples", 1000))
    seed = int(settings.get("random_seed", config.random_seed))

    df = load_cohort_frame(config)
    split = build_walk_forward_folds(df, config)

    # In-sample: all non-holdout trades
    holdout_set = set(split.get("holdout_uuids", []))
    is_df = df[~df["trade_uuid"].isin(holdout_set)].copy()
    holdout_df = df[df["trade_uuid"].isin(holdout_set)].copy()

    results: dict[str, Any] = {
        "cohort_n": len(df),
        "post_gate_only": bool(settings.get("post_gate_only", False)),
        "split": {
            "method": "expanding_walk_forward",
            "n_folds": len(split.get("folds", [])),
            "holdout_n": len(holdout_df),
        },
        "pathways": {},
    }

    for thr in thresholds:
        pid = f"mfe_{str(thr).replace('.', '_')}r_within_{max_bars}"
        mask_is = pathway_mask(is_df, thr, max_bars)
        is_metrics = pathway_metrics(is_df, mask_is)
        is_metrics["expectancy_bootstrap_ci"] = bootstrap_expectancy_ci(
            is_df.loc[mask_is, "pnl_r"].dropna().to_numpy(dtype=float),
            bootstrap_n,
            seed,
        )
        is_metrics["bars_to_continuous"] = _continuous_bars_stats(is_df, thr)

        fold_results = []
        val_frames: list[pd.DataFrame] = []

        for fold in split.get("folds", []):
            val = df[df["trade_uuid"].isin(fold["val_uuids"])]
            val_frames.append(val)
            mask_val = pathway_mask(val, thr, max_bars)
            metrics = pathway_metrics(val, mask_val)
            metrics["fold_id"] = fold["fold_id"]
            metrics["underpowered"] = (
                metrics["n"] < min_pw_n or fold["val_n"] < min_val_n
            )
            fold_results.append(metrics)

        pooled_val = pd.concat(val_frames, ignore_index=True) if val_frames else pd.DataFrame()
        pooled_oos: dict[str, Any] = {"n_folds": len(fold_results), "folds": fold_results}
        if not pooled_val.empty:
            mask_pooled = pathway_mask(pooled_val, thr, max_bars)
            pooled_metrics = pathway_metrics(pooled_val, mask_pooled)
            pooled_oos.update(pooled_metrics)
            pnls_path = pooled_val.loc[mask_pooled, "pnl_r"].dropna().to_numpy(dtype=float)
            if len(pnls_path):
                pooled_oos["expectancy_bootstrap_ci"] = bootstrap_expectancy_ci(
                    pnls_path, bootstrap_n, seed
                )

        # Final holdout (never seen in walk-forward)
        holdout_metrics = None
        if not holdout_df.empty:
            mask_h = pathway_mask(holdout_df, thr, max_bars)
            holdout_metrics = pathway_metrics(holdout_df, mask_h)
            holdout_metrics["underpowered"] = holdout_metrics["n"] < min_pw_n
            holdout_metrics["expectancy_bootstrap_ci"] = bootstrap_expectancy_ci(
                holdout_df.loc[mask_h, "pnl_r"].dropna().to_numpy(dtype=float),
                bootstrap_n,
                seed + 1,
            )

        # Drift: IS vs pooled OOS
        drift = {}
        if pooled_oos.get("expectancy_r") is not None and is_metrics.get("expectancy_r") is not None:
            drift["expectancy_delta"] = pooled_oos["expectancy_r"] - is_metrics["expectancy_r"]
            drift["win_rate_delta"] = (pooled_oos.get("win_rate") or 0) - (
                is_metrics.get("win_rate") or 0
            )
            d_is = is_metrics.get("cohens_d_pnl")
            d_oos = pooled_oos.get("cohens_d_pnl")
            drift["cohens_d_delta"] = (
                (d_oos - d_is) if d_is is not None and d_oos is not None else None
            )

        results["pathways"][pid] = {
            "definition": f"bars_to_{str(thr).replace('.', '_')}r_mfe <= {max_bars}",
            "in_sample": is_metrics,
            "walk_forward_oos": pooled_oos,
            "final_holdout": holdout_metrics,
            "drift_is_vs_oos": drift,
        }

    # Also report post_gate subset if not already filtered
    if not settings.get("post_gate_only", False) and "post_gate" in df.columns:
        pg = df[df["post_gate"]].copy()
        if len(pg) >= min_pw_n:
            mask_pg = pathway_mask(pg, 0.3, max_bars)
            results["post_gate_subset_mfe_0_3r"] = pathway_metrics(pg, mask_pg)

    return results

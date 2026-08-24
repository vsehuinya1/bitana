from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.cluster import AgglomerativeClustering, KMeans
from sklearn.metrics import adjusted_rand_score, silhouette_score
from sklearn.preprocessing import StandardScaler

from research_duo.config_loader import PipelineConfig
from research_duo.manifest import sha256_file, write_manifest
from research_duo.paths import REPO_ROOT
from research_duo.phase3_io import load_trade_features, phase3_settings

CLUSTER_LABELS_OUTPUT = "cluster_labels.parquet"

# Outcome / identity columns excluded from clustering feature space.
EXCLUDE_COLS = {
    "trade_uuid",
    "symbol",
    "pnl_r",
    "exit_reason",
    "hold_candles",
    "outcome_mae",
    "outcome_mfe",
    "is_winner",
    "session",
}

ENTRY_FEATURE_COLS = {
    "decile",
    "cascade_strength",
    "confirmations_count",
    "post_gate",
    "post_gate_audit_v2",
    "bd_distance_pct",
    "imbalance_z",
    "imb_fallback_flag",
    "is_experimental",
}

PATH_FEATURE_COLS = {
    "max_mfe",
    "max_mae",
    "mae_recovery",
    "stale_pct",
    "mfe_slope_early_bars_1_5",
    "mfe_slope_late_30pct",
    "path_bars",
    "bars_to_0_1r_mfe",
    "bars_to_0_3r_mfe",
    "bars_to_0_5r_mfe",
    "bars_to_0_1r_unrealized",
    "bars_to_0_3r_unrealized",
    "bars_to_0_5r_unrealized",
}


def _clustering_dir(config: PipelineConfig) -> Path:
    raw = config.raw.get("paths", {}).get("clustering_dir", "research_duo/clustering")
    path = Path(raw)
    return path if path.is_absolute() else REPO_ROOT / path


def _select_feature_matrix(df: pd.DataFrame, feature_cols: list[str]) -> tuple[pd.DataFrame, np.ndarray, list[str]]:
    usable = [c for c in feature_cols if c in df.columns]
    sub = df[usable].copy()
    for col in usable:
        sub[col] = pd.to_numeric(sub[col], errors="coerce")
    sub = sub.dropna(axis=0, how="any")
    if sub.empty:
        raise ValueError("No rows with complete feature data for clustering.")
    idx = sub.index
    matrix = sub.to_numpy(dtype=float)
    return df.loc[idx], matrix, usable


def _feature_columns(df: pd.DataFrame) -> list[str]:
    cols = []
    for c in df.columns:
        if c in EXCLUDE_COLS:
            continue
        if pd.api.types.is_numeric_dtype(df[c]) or df[c].dtype == bool:
            cols.append(c)
    return sorted(cols)


def _run_clusterers(
    matrix: np.ndarray,
    trade_uuids: list[str],
    k_values: list[int],
    seed: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    scaled = StandardScaler().fit_transform(matrix)
    out = pd.DataFrame({"trade_uuid": trade_uuids})
    metrics: dict[str, Any] = {"k_values": k_values, "algorithms": {}}

    for k in k_values:
        if k >= len(matrix):
            continue

        km = KMeans(n_clusters=k, random_state=seed, n_init=10)
        km_labels = km.fit_predict(scaled)
        out[f"kmeans_k{k}"] = km_labels

        agg = AgglomerativeClustering(n_clusters=k, linkage="ward")
        agg_labels = agg.fit_predict(scaled)
        out[f"agg_k{k}"] = agg_labels

        sil_km = silhouette_score(scaled, km_labels) if len(set(km_labels)) > 1 else None
        sil_agg = silhouette_score(scaled, agg_labels) if len(set(agg_labels)) > 1 else None
        metrics["algorithms"][f"kmeans_k{k}"] = {
            "silhouette": sil_km,
            "inertia": float(km.inertia_),
        }
        metrics["algorithms"][f"agg_k{k}"] = {"silhouette": sil_agg}
        metrics["algorithms"][f"ari_kmeans_vs_agg_k{k}"] = {
            "adjusted_rand_index": float(adjusted_rand_score(km_labels, agg_labels))
        }

    return out, metrics


def _ablation_stability(
    df: pd.DataFrame,
    full_matrix: np.ndarray,
    feature_cols: list[str],
    baseline_labels: np.ndarray,
    seed: int,
    k: int = 3,
) -> dict[str, Any]:
    """Measure cluster stability when feature groups are removed."""
    scaled_full = StandardScaler().fit_transform(full_matrix)
    results: dict[str, Any] = {}

    ablations = {
        "drop_entry_context": [c for c in feature_cols if c in ENTRY_FEATURE_COLS],
        "drop_path_dynamics": [c for c in feature_cols if c in PATH_FEATURE_COLS],
        "drop_post_gate_flags": [c for c in feature_cols if c in ("post_gate", "post_gate_audit_v2")],
    }

    label_by_idx = pd.Series(baseline_labels, index=df.index)

    for name, drop_cols in ablations.items():
        keep = [c for c in feature_cols if c not in drop_cols]
        if len(keep) < 2:
            results[name] = {"skipped": True, "reason": "too few features remaining"}
            continue
        sub = df[keep].apply(pd.to_numeric, errors="coerce").dropna(axis=0, how="any")
        if len(sub) < k + 1:
            results[name] = {"skipped": True, "reason": "insufficient rows"}
            continue
        scaled = StandardScaler().fit_transform(sub.to_numpy(dtype=float))
        km = KMeans(n_clusters=k, random_state=seed, n_init=10)
        ablated_labels = km.fit_predict(scaled)
        base_labels = label_by_idx.loc[sub.index].to_numpy()
        ari = adjusted_rand_score(base_labels, ablated_labels)
        results[name] = {
            "features_removed": drop_cols,
            "features_remaining": len(keep),
            "n_samples": len(sub),
            "adjusted_rand_index_vs_full_kmeans_k3": float(ari),
        }

    return results


def run_clustering(config: PipelineConfig) -> tuple[Path, Path, dict[str, Any]]:
    settings = phase3_settings(config)
    seed = int(settings.get("random_seed", config.random_seed))
    k_values = list(settings.get("cluster_k", [3, 5, 7]))

    features = load_trade_features(config)
    # Cluster only trades with path telemetry
    features = features[features["path_bars"].notna() & (features["path_bars"] > 0)].copy()

    feature_cols = _feature_columns(features)
    subset, matrix, used_cols = _select_feature_matrix(features, feature_cols)
    trade_uuids = subset["trade_uuid"].tolist()

    labels_df, cluster_metrics = _run_clusterers(matrix, trade_uuids, k_values, seed)

    baseline_km = KMeans(n_clusters=3, random_state=seed, n_init=10)
    baseline_labels = baseline_km.fit_predict(StandardScaler().fit_transform(matrix))
    ablation = _ablation_stability(subset, matrix, used_cols, baseline_labels, seed, k=3)

    # Attach minimal observational outcome columns for downstream inspection (not used in clustering)
    labels_df = labels_df.merge(
        subset[["trade_uuid", "pnl_r", "is_winner", "max_mfe", "post_gate"]],
        on="trade_uuid",
        how="left",
    )

    out_dir = _clustering_dir(config)
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / CLUSTER_LABELS_OUTPUT
    labels_df.to_parquet(output_path, index=False)

    qa = {
        "n_trades_clustered": len(labels_df),
        "n_features": len(used_cols),
        "feature_columns": used_cols,
        "cluster_metrics": cluster_metrics,
        "ablation_stability_k3_kmeans": ablation,
        "outcome_columns_attached": ["pnl_r", "is_winner", "max_mfe", "post_gate"],
        "note": "Outcomes attached for inspection only; not used in clustering input.",
    }

    manifest_path = write_manifest(
        config.manifests_dir,
        pipeline_version=config.pipeline_version,
        repo_root=REPO_ROOT,
        config_snapshot=config.raw,
        stage="clustering",
        artifacts={
            "cluster_labels_parquet": str(output_path),
            "cluster_labels_sha256": sha256_file(output_path),
            "row_count": len(labels_df),
        },
        warnings=[],
        qa=qa,
    )
    return output_path, manifest_path, qa

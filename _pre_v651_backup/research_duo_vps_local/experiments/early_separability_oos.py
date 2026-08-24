from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.preprocessing import StandardScaler

from research_duo.config_loader import PipelineConfig
from research_duo.experiments.oos_common import load_cohort_frame, pathway_mask, phase4_settings
from research_duo.experiments.walk_forward import build_walk_forward_folds
from research_duo.phase3_io import load_r_path_long


def _early_bar_features(r_path: pd.DataFrame, trade_uuid: str, cutoff_bar: int) -> dict[str, float] | None:
    g = r_path[r_path["trade_uuid"] == trade_uuid].sort_values("bar_index")
    early = g[g["bar_index"] <= cutoff_bar]
    if early.empty:
        return None
    last = early.iloc[-1]
    return {
        f"unrealized_r_b{cutoff_bar}": float(last["unrealized_r"]),
        f"mfe_so_far_b{cutoff_bar}": float(last["mfe_so_far"]),
        f"mae_so_far_b{cutoff_bar}": float(last["mae_so_far"]),
        f"mfe_velocity_b{cutoff_bar}": float(last["mfe_velocity"]),
        f"delta_r_mean_b{cutoff_bar}": float(early["delta_r"].mean()),
        "bars_observed": float(len(early)),
    }


def _build_dataset(
    df: pd.DataFrame,
    r_path: pd.DataFrame,
    cutoff_bar: int,
    threshold_r: float,
    max_bars: int,
) -> pd.DataFrame:
    rows = []
    static_cols = [
        "decile",
        "cascade_strength",
        "confirmations_count",
        "bd_distance_pct",
        "imbalance_z",
    ]
    for _, row in df.iterrows():
        feats = _early_bar_features(r_path, row["trade_uuid"], cutoff_bar)
        if feats is None:
            continue
        label = int(pathway_mask(pd.DataFrame([row]), threshold_r, max_bars).iloc[0])
        entry = {c: row[c] for c in static_cols if c in row.index}
        rows.append({"trade_uuid": row["trade_uuid"], "label": label, **entry, **feats})
    return pd.DataFrame(rows)


def run_early_separability_oos(config: PipelineConfig) -> dict[str, Any]:
    settings = phase4_settings(config)
    early_bars = list(settings.get("early_bars", [3, 5, 7, 10]))
    threshold_r = float(settings.get("pathways", {}).get("primary_threshold_r", 0.3))
    max_bars = int(settings.get("pathways", {}).get("max_bars", 10))
    seed = int(settings.get("random_seed", config.random_seed))
    min_train = int(settings.get("min_train_trades", 30))

    df = load_cohort_frame(config)
    r_path = load_r_path_long(config)
    split = build_walk_forward_folds(df, config)

    results: dict[str, Any] = {
        "target": {
            "label": f"pathway_mfe_{threshold_r}r_within_{max_bars}_bars",
            "rule": f"bars_to_{str(threshold_r).replace('.', '_')}r_mfe <= {max_bars} (post-hoc label)",
            "features_use_bars": "1..N only",
            "model": "LogisticRegression(max_iter=1000, random_state=fixed)",
            "no_tuning": True,
        },
        "by_cutoff_bar": {},
    }

    for cutoff_bar in early_bars:
        dataset = _build_dataset(df, r_path, cutoff_bar, threshold_r, max_bars)
        if dataset.empty:
            results["by_cutoff_bar"][cutoff_bar] = {"skipped": True}
            continue

        feature_cols = [c for c in dataset.columns if c not in ("trade_uuid", "label")]
        fold_metrics = []

        for fold in split.get("folds", []):
            train = dataset[dataset["trade_uuid"].isin(fold["train_uuids"])]
            val = dataset[dataset["trade_uuid"].isin(fold["val_uuids"])]
            if len(train) < min_train or len(val) < 3:
                continue
            if train["label"].nunique() < 2:
                continue

            X_train_raw = train[feature_cols].apply(pd.to_numeric, errors="coerce")
            train_valid = X_train_raw.notna().all(axis=1)
            X_train = X_train_raw.loc[train_valid]
            y_train = train.loc[train_valid, "label"].to_numpy()
            if len(X_train) < min_train or len(set(y_train)) < 2:
                continue

            X_val = val[feature_cols].apply(pd.to_numeric, errors="coerce")
            valid = X_val.notna().all(axis=1)
            X_val = X_val.loc[valid]
            y_val = val.loc[valid, "label"].to_numpy()
            if len(X_val) < 2:
                continue

            scaler = StandardScaler()
            X_tr = scaler.fit_transform(X_train.to_numpy(dtype=float))
            X_va = scaler.transform(X_val.to_numpy(dtype=float))

            model = LogisticRegression(max_iter=1000, random_state=seed)
            model.fit(X_tr, y_train)
            y_pred = model.predict(X_va)
            y_prob = model.predict_proba(X_va)[:, 1] if len(set(y_val)) > 1 else None

            entry: dict[str, Any] = {
                "fold_id": fold["fold_id"],
                "train_n": len(y_train),
                "val_n": len(y_val),
                "accuracy": float(accuracy_score(y_val, y_pred)),
                "positive_rate_val": float(y_val.mean()),
            }
            if y_prob is not None and len(set(y_val)) > 1:
                entry["roc_auc"] = float(roc_auc_score(y_val, y_prob))
            fold_metrics.append(entry)

        # Feature importance from full non-holdout train (observational reference)
        holdout_set = set(split.get("holdout_uuids", []))
        ref_train = dataset[~dataset["trade_uuid"].isin(holdout_set)]
        importance = {}
        ranking: list[tuple[str, float]] = []
        if len(ref_train) >= min_train and ref_train["label"].nunique() == 2:
            X_raw = ref_train[feature_cols].apply(pd.to_numeric, errors="coerce")
            valid = X_raw.notna().all(axis=1)
            X = X_raw.loc[valid]
            y = ref_train.loc[valid, "label"].to_numpy()
            if len(X) >= min_train and len(set(y)) == 2:
                scaler = StandardScaler()
                model = LogisticRegression(max_iter=1000, random_state=seed)
                model.fit(scaler.fit_transform(X.to_numpy(dtype=float)), y)
                importance = {
                    feature_cols[i]: float(model.coef_.ravel()[i])
                    for i in range(len(feature_cols))
                }
                ranking = sorted(importance.items(), key=lambda x: -abs(x[1]))

        pooled_auc = [f["roc_auc"] for f in fold_metrics if "roc_auc" in f]
        pooled_acc = [f["accuracy"] for f in fold_metrics]

        results["by_cutoff_bar"][cutoff_bar] = {
            "n_dataset": len(dataset),
            "n_positive": int(dataset["label"].sum()),
            "fold_metrics": fold_metrics,
            "pooled_oos_accuracy_mean": float(np.mean(pooled_acc)) if pooled_acc else None,
            "pooled_oos_auc_mean": float(np.mean(pooled_auc)) if pooled_auc else None,
            "coefficient_ranking_reference": ranking[:10] if importance else [],
            "leakage_note": (
                f"Features restricted to bars 1..{cutoff_bar}. "
                "Label uses full-trade bars_to_mfe (post-hoc)."
            ),
        }

    return results

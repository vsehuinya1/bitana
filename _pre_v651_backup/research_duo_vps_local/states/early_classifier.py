from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    precision_recall_fscore_support,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

from research_duo.config_loader import PipelineConfig
from research_duo.manifest import write_manifest
from research_duo.paths import REPO_ROOT
from research_duo.phase3_io import load_r_path_long, load_trade_features, phase3_settings


def _states_dir(config: PipelineConfig) -> Path:
    raw = config.raw.get("paths", {}).get("states_dir", "research_duo/states")
    path = Path(raw)
    return path if path.is_absolute() else REPO_ROOT / path


def _post_hoc_explosive_label(
    r_path: pd.DataFrame,
    outcomes: pd.DataFrame,
    *,
    mfe_threshold: float,
    max_bars_to_mfe: int,
) -> pd.DataFrame:
    """
    Post-hoc label for observational classifier evaluation.
    EXPLOSIVE = winner AND reached mfe_threshold within max_bars_to_mfe (audit §6 proxy).
    """
    rows = []
    for trade_uuid, group in r_path.groupby("trade_uuid"):
        g = group.sort_values("bar_index")
        outcome = outcomes[outcomes["trade_uuid"] == trade_uuid]
        if outcome.empty:
            continue
        pnl_r = float(outcome.iloc[0]["pnl_r"])
        hits = g.loc[g["mfe_so_far"] >= mfe_threshold, "bar_index"]
        reached_bar = int(hits.iloc[0]) if not hits.empty else None
        is_explosive = (
            pnl_r > 0
            and reached_bar is not None
            and reached_bar <= max_bars_to_mfe
        )
        rows.append(
            {
                "trade_uuid": trade_uuid,
                "label_explosive": int(is_explosive),
                "reached_mfe_1r_bar": reached_bar,
                "pnl_r": pnl_r,
            }
        )
    return pd.DataFrame(rows)


def _bar10_features(r_path: pd.DataFrame, cutoff_bar: int) -> pd.DataFrame:
    """Features computable using only bars 1..cutoff_bar (real-time at bar cutoff)."""
    rows = []
    for trade_uuid, group in r_path.groupby("trade_uuid"):
        g = group.sort_values("bar_index")
        early = g[g["bar_index"] <= cutoff_bar]
        if early.empty:
            continue
        last = early.iloc[-1]
        rows.append(
            {
                "trade_uuid": trade_uuid,
                f"unrealized_r_bar{cutoff_bar}": float(last["unrealized_r"]),
                f"mfe_so_far_bar{cutoff_bar}": float(last["mfe_so_far"]),
                f"mae_so_far_bar{cutoff_bar}": float(last["mae_so_far"]),
                f"mfe_velocity_bar{cutoff_bar}": float(last["mfe_velocity"]),
                f"mae_velocity_bar{cutoff_bar}": float(last["mae_velocity"]),
                "delta_r_mean_early": float(early["delta_r"].mean()),
                "delta_r_std_early": float(early["delta_r"].std(ddof=0)),
                "bars_observed": int(len(early)),
            }
        )
    return pd.DataFrame(rows)


def _static_entry_features(features: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "trade_uuid",
        "decile",
        "cascade_strength",
        "confirmations_count",
        "bd_distance_pct",
        "imbalance_z",
        "imb_fallback_flag",
    ]
    cols = [c for c in cols if c in features.columns]
    return features[cols].copy()


def _train_models(
    X: np.ndarray,
    y: np.ndarray,
    feature_names: list[str],
    seed: int,
) -> dict[str, Any]:
    X_train, X_test, y_train, y_test = train_test_split(
        X,
        y,
        test_size=0.3,
        random_state=seed,
        stratify=y if len(set(y)) > 1 and min(np.bincount(y)) >= 2 else None,
    )
    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s = scaler.transform(X_test)

    results: dict[str, Any] = {
        "n_samples": int(len(y)),
        "n_positive": int(y.sum()),
        "n_negative": int(len(y) - y.sum()),
        "train_size": int(len(y_train)),
        "test_size": int(len(y_test)),
        "feature_names": feature_names,
    }

    models = {
        "logistic_regression": LogisticRegression(max_iter=1000, random_state=seed),
        "random_forest": RandomForestClassifier(n_estimators=100, random_state=seed),
    }

    for name, model in models.items():
        model.fit(X_train_s, y_train)
        y_pred = model.predict(X_test_s)
        y_prob = (
            model.predict_proba(X_test_s)[:, 1]
            if hasattr(model, "predict_proba")
            else None
        )

        prec, rec, f1, _ = precision_recall_fscore_support(
            y_test, y_pred, average="binary", zero_division=0
        )
        entry: dict[str, Any] = {
            "accuracy": float(accuracy_score(y_test, y_pred)),
            "precision": float(prec),
            "recall": float(rec),
            "f1": float(f1),
            "classification_report": classification_report(
                y_test, y_pred, zero_division=0, output_dict=True
            ),
        }
        if y_prob is not None and len(set(y_test)) > 1:
            entry["roc_auc"] = float(roc_auc_score(y_test, y_prob))

        if hasattr(model, "coef_"):
            coefs = model.coef_.ravel()
            entry["feature_importance"] = {
                feature_names[i]: float(coefs[i]) for i in range(len(feature_names))
            }
        elif hasattr(model, "feature_importances_"):
            imps = model.feature_importances_
            entry["feature_importance"] = {
                feature_names[i]: float(imps[i]) for i in range(len(feature_names))
            }
            entry["feature_importance_ranking"] = sorted(
                entry["feature_importance"].items(), key=lambda x: -abs(x[1])
            )

        results[name] = entry

    return results


def run_early_classifier(config: PipelineConfig) -> tuple[Path, dict[str, Any]]:
    settings = phase3_settings(config)
    seed = int(settings.get("random_seed", config.random_seed))
    explosive_cfg = settings.get("explosive", {})
    clf_cfg = settings.get("early_classifier", {})

    mfe_threshold = float(explosive_cfg.get("mfe_threshold_r", 1.0))
    max_bars_to_mfe = int(explosive_cfg.get("max_bars_to_mfe", 15))
    cutoff_bar = int(clf_cfg.get("feature_cutoff_bar", 10))

    r_path = load_r_path_long(config)
    features = load_trade_features(config)

    labels = _post_hoc_explosive_label(
        r_path,
        features[["trade_uuid", "pnl_r"]],
        mfe_threshold=mfe_threshold,
        max_bars_to_mfe=max_bars_to_mfe,
    )
    bar_feats = _bar10_features(r_path, cutoff_bar)
    static = _static_entry_features(features)

    dataset = labels.merge(bar_feats, on="trade_uuid", how="inner")
    dataset = dataset.merge(static, on="trade_uuid", how="left")

    feature_cols = [
        c
        for c in dataset.columns
        if c not in ("trade_uuid", "label_explosive", "reached_mfe_1r_bar", "pnl_r")
    ]
    X_df = dataset[feature_cols].apply(pd.to_numeric, errors="coerce")
    mask = X_df.notna().all(axis=1) & dataset["label_explosive"].notna()
    X_df = X_df.loc[mask]
    y = dataset.loc[mask, "label_explosive"].astype(int).to_numpy()

    model_results = _train_models(
        X_df.to_numpy(dtype=float),
        y,
        feature_cols,
        seed,
    )

    qa: dict[str, Any] = {
        "label_definition": {
            "positive_class": "EXPLOSIVE",
            "rule": f"pnl_r > 0 AND mfe_so_far >= {mfe_threshold}R within bar {max_bars_to_mfe}",
            "feature_cutoff_bar": cutoff_bar,
            "note": "Label is post-hoc; bar features use only bars 1..cutoff_bar.",
        },
        "models": model_results,
        "no_hyperparameter_tuning": True,
    }

    out_dir = _states_dir(config)
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / "early_classifier_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(qa, f, indent=2, sort_keys=True)
        f.write("\n")

    manifest_path = write_manifest(
        config.manifests_dir,
        pipeline_version=config.pipeline_version,
        repo_root=REPO_ROOT,
        config_snapshot=config.raw,
        stage="early_classifier",
        artifacts={"results_json": str(output_path)},
        warnings=[],
        qa=qa,
    )
    return output_path, qa

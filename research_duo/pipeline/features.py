from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from research_duo.config_loader import PipelineConfig
from research_duo.manifest import sha256_file, write_manifest
from research_duo.paths import REPO_ROOT
from research_duo.pipeline.common import load_trades_parquet

TRADE_FEATURES_OUTPUT = "trade_features.parquet"


def _features_settings(config: PipelineConfig) -> dict[str, Any]:
    return config.raw.get("phase2", {}).get("features", {})


def _parse_confirmations_count(raw: str | float | None) -> int:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return 0
    if not isinstance(raw, str) or not raw:
        return 0
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return sum(1 for v in data.values() if v)
    except json.JSONDecodeError:
        pass
    return 0


def _first_bar_at_threshold(group: pd.DataFrame, col: str, threshold: float) -> int | None:
    hits = group.loc[group[col] >= threshold, "bar_index"]
    if hits.empty:
        return None
    return int(hits.iloc[0])


def _linear_slope(y: np.ndarray) -> float:
    if len(y) < 2:
        return 0.0
    x = np.arange(len(y), dtype=float)
    if np.allclose(y, y[0]):
        return 0.0
    coeff = np.polyfit(x, y, 1)
    return float(coeff[0])


def _path_features_for_trade(group: pd.DataFrame, settings: dict[str, Any]) -> dict[str, Any]:
    g = group.sort_values("bar_index")
    stale_eps = float(settings.get("stale_epsilon_r", 0.001))
    thresholds = settings.get("threshold_rs", [0.1, 0.3, 0.5])
    early_end = int(settings.get("early_slope_end_bar", 5))

    max_mfe = float(g["mfe_so_far"].max())
    max_mae = float(g["mae_so_far"].max())
    mae_recovery = float((g["mfe_so_far"] - g["mae_so_far"]).max())

    stale_pct = float((g["delta_r"].abs() < stale_eps).mean()) if len(g) else 0.0

    early = g[g["bar_index"] <= early_end]
    mfe_slope_early = _linear_slope(early["mfe_so_far"].to_numpy(dtype=float))

    n = len(g)
    late_start = max(1, int(np.ceil(n * 0.7)))
    late = g[g["bar_index"] >= late_start]
    mfe_slope_late = _linear_slope(late["mfe_so_far"].to_numpy(dtype=float))

    out: dict[str, Any] = {
        "max_mfe": max_mfe,
        "max_mae": max_mae,
        "mae_recovery": mae_recovery,
        "stale_pct": stale_pct,
        "mfe_slope_early_bars_1_5": mfe_slope_early,
        "mfe_slope_late_30pct": mfe_slope_late,
        "path_bars": n,
    }

    for thr in thresholds:
        key = str(thr).replace(".", "_")
        out[f"bars_to_{key}r_mfe"] = _first_bar_at_threshold(g, "mfe_so_far", thr)
        out[f"bars_to_{key}r_unrealized"] = _first_bar_at_threshold(g, "unrealized_r", thr)

    return out


def _entry_features(row: pd.Series) -> dict[str, Any]:
    entry_price = float(row.get("entry_price") or row.get("te_entry_price") or 0)
    range_high = float(row.get("te_range_high") or 0)
    bd_pct = (
        ((entry_price - range_high) / range_high * 100.0)
        if range_high > 0 and entry_price > 0
        else np.nan
    )

    imb_z = row.get("te_imb_z")
    imb_z_val = float(imb_z) if pd.notna(imb_z) else np.nan
    confirms = row.get("confirmations_trades") or row.get("confirmations") or ""
    n_confirms = _parse_confirmations_count(confirms)
    if "n_confirmations_trades" in row.index and pd.notna(row.get("n_confirmations_trades")):
        n_confirms = int(row["n_confirmations_trades"])

    return {
        "decile": int(row["decile"]) if pd.notna(row.get("decile")) else None,
        "cascade_strength": float(row["te_cascade_strength"])
        if pd.notna(row.get("te_cascade_strength"))
        else np.nan,
        "confirmations_count": n_confirms,
        "session": row.get("session"),
        "post_gate": bool(row.get("post_gate", False)),
        "post_gate_audit_v2": bool(row.get("post_gate_audit_v2", False)),
        "bd_distance_pct": bd_pct,
        "imbalance_z": imb_z_val,
        "imb_fallback_flag": bool(imb_z_val == 0.0) if pd.notna(imb_z_val) else False,
        "symbol": row.get("symbol"),
        "is_experimental": int(row.get("is_experimental", 0))
        if pd.notna(row.get("is_experimental"))
        else 0,
    }


def _build_features(config: PipelineConfig) -> tuple[pd.DataFrame, dict[str, Any]]:
    settings = _features_settings(config)
    r_path_path = config.datasets_dir / "r_path_long.parquet"
    if not r_path_path.exists():
        raise FileNotFoundError(f"Missing {r_path_path}. Run `python -m research_duo r_path` first.")

    trades = load_trades_parquet(config)
    long_df = pd.read_parquet(r_path_path)

    path_feats = []
    for trade_uuid, group in long_df.groupby("trade_uuid", sort=True):
        pf = _path_features_for_trade(group, settings)
        pf["trade_uuid"] = trade_uuid
        path_feats.append(pf)
    path_df = pd.DataFrame(path_feats)

    entry_rows = []
    for _, row in trades.iterrows():
        entry_rows.append({"trade_uuid": row["trade_uuid"], **_entry_features(row)})
    entry_df = pd.DataFrame(entry_rows)

    outcome_cols = [
        "trade_uuid",
        "pnl_r",
        "exit_reason",
        "hold_candles",
        "mae",
        "mfe",
        "is_winner",
    ]
    outcome_df = trades[outcome_cols].copy()
    outcome_df = outcome_df.rename(
        columns={
            "mae": "outcome_mae",
            "mfe": "outcome_mfe",
        }
    )

    features = trades[["trade_uuid", "symbol"]].copy()
    features = features.merge(entry_df, on="trade_uuid", how="left")
    features = features.merge(path_df, on="trade_uuid", how="left")
    features = features.merge(outcome_df, on="trade_uuid", how="left")

    # Stable column order: id, entry, path, outcome
    id_cols = ["trade_uuid", "symbol"]
    entry_cols = [
        "decile",
        "cascade_strength",
        "confirmations_count",
        "session",
        "post_gate",
        "post_gate_audit_v2",
        "bd_distance_pct",
        "imbalance_z",
        "imb_fallback_flag",
        "is_experimental",
    ]
    path_cols = sorted(c for c in path_df.columns if c != "trade_uuid")
    outcome_cols_ordered = [
        "pnl_r",
        "exit_reason",
        "hold_candles",
        "outcome_mae",
        "outcome_mfe",
        "is_winner",
    ]
    ordered = id_cols + entry_cols + path_cols + outcome_cols_ordered
    ordered = [c for c in ordered if c in features.columns]
    features = features[ordered].sort_values("trade_uuid").reset_index(drop=True)

    closed_with_path = set(long_df["trade_uuid"]) & set(trades["trade_uuid"])
    qa: dict[str, Any] = {
        "feature_groups": {
            "entry_context": entry_cols,
            "path_dynamics": path_cols,
            "outcome_anchors": outcome_cols_ordered,
        },
        "trade_count": int(len(features)),
        "closed_trades_with_path_features": len(closed_with_path),
        "trades_without_r_path": int(len(trades) - len(closed_with_path)),
        "deterministic": True,
        "normalization": "none",
        "regime_labels": "none",
        "clustering": "none",
    }

    return features, qa


def run_features(config: PipelineConfig) -> tuple[Path, Path]:
    features_df, qa = _build_features(config)

    features_dir = Path(config.raw["paths"].get("features_dir", "research_duo/features"))
    if not features_dir.is_absolute():
        features_dir = REPO_ROOT / features_dir
    features_dir.mkdir(parents=True, exist_ok=True)

    output_path = features_dir / TRADE_FEATURES_OUTPUT
    features_df.to_parquet(output_path, index=False)

    manifest_path = write_manifest(
        config.manifests_dir,
        pipeline_version=config.pipeline_version,
        repo_root=REPO_ROOT,
        config_snapshot=config.raw,
        stage="features",
        artifacts={
            "trade_features_parquet": str(output_path),
            "trade_features_sha256": sha256_file(output_path),
            "row_count": len(features_df),
            "feature_columns": list(features_df.columns),
        },
        warnings=[],
        qa=qa,
    )
    return output_path, manifest_path

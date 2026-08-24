from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from research.data.sessions import get_session_at_time

from research_duo.config_loader import PipelineConfig
from research_duo.manifest import write_manifest
from research_duo.paths import REPO_ROOT
from research_duo.pipeline.common import load_trades_parquet, open_readonly, parse_iso_to_ms

R_PATH_LONG_OUTPUT = "r_path_long.parquet"


def _phase2_cfg(config: PipelineConfig) -> dict[str, Any]:
    return config.raw.get("phase2", {})


def _r_path_settings(config: PipelineConfig) -> dict[str, Any]:
    return _phase2_cfg(config).get("r_path", {})


def _load_raw_r_path(config: PipelineConfig) -> pd.DataFrame:
    settings = _r_path_settings(config)
    if not config.v6_db.exists() or config.v6_db.stat().st_size == 0:
        raise FileNotFoundError(f"v6 telemetry DB missing or empty: {config.v6_db}")

    with open_readonly(config.v6_db) as conn:
        df = pd.read_sql_query(
            """
            SELECT
                trade_uuid, bar_index, timestamp, price,
                unrealized_r, mae_so_far, mfe_so_far,
                vol_trail_level, struct_trail_level
            FROM r_path
            ORDER BY trade_uuid, bar_index
            """,
            conn,
        )
    return df


def _enrich_trade_path(group: pd.DataFrame, *, velocity_window: int, lag_correction: bool) -> pd.DataFrame:
    g = group.sort_values("bar_index").copy()
    g["delta_r"] = g["unrealized_r"].diff().fillna(0.0)
    g["mfe_velocity"] = g["mfe_so_far"].diff().rolling(velocity_window, min_periods=1).mean()
    g["mae_velocity"] = g["mae_so_far"].diff().rolling(velocity_window, min_periods=1).mean()

    ts_ms = g["timestamp"].map(parse_iso_to_ms)
    g["session"] = ts_ms.map(lambda ms: get_session_at_time(ms) if ms is not None else "unknown")

    if lag_correction:
        g["lag_corrected_mfe"] = g["mfe_so_far"].shift(-1)
        g["lag_corrected_mae"] = g["mae_so_far"].shift(-1)
    else:
        g["lag_corrected_mfe"] = np.nan
        g["lag_corrected_mae"] = np.nan

    if "price" in g.columns:
        g["vol_trail_distance"] = np.where(
            g["vol_trail_level"].fillna(0) > 0,
            g["price"] - g["vol_trail_level"],
            np.nan,
        )
        g["struct_trail_distance"] = np.where(
            g["struct_trail_level"].fillna(0) > 0,
            g["price"] - g["struct_trail_level"],
            np.nan,
        )

    return g


def _bar_continuity_qa(bars: pd.Series) -> dict[str, Any]:
    indices = bars.sort_values().astype(int).tolist()
    if not indices:
        return {"bar_count": 0, "expected_bars": 0, "missing_bar_count": 0, "monotonic": True, "gaps": []}

    expected = list(range(indices[0], indices[-1] + 1))
    missing = [b for b in expected if b not in set(indices)]
    monotonic = indices == sorted(indices) and len(indices) == len(set(indices))

    return {
        "bar_count": len(indices),
        "min_bar": indices[0],
        "max_bar": indices[-1],
        "expected_bars": len(expected),
        "missing_bar_count": len(missing),
        "missing_bars_sample": missing[:20],
        "monotonic": monotonic,
    }


def _build_r_path_long(config: PipelineConfig) -> tuple[pd.DataFrame, dict[str, Any]]:
    settings = _r_path_settings(config)
    velocity_window = int(settings.get("velocity_window", 3))
    lag_correction = bool(settings.get("lag_correction", False))

    raw = _load_raw_r_path(config)
    trades = load_trades_parquet(config)
    closed_uuids = set(trades["trade_uuid"])

    parts = []
    for trade_uuid, group in raw.groupby("trade_uuid", sort=True):
        parts.append(
            _enrich_trade_path(
                group,
                velocity_window=velocity_window,
                lag_correction=lag_correction,
            )
        )
    long_df = pd.concat(parts, ignore_index=True)

    # Per-trade QA
    mfe_deltas: list[float] = []
    continuity_issues: list[dict[str, Any]] = []
    monotonic_violations: list[str] = []

    trades_mfe = trades.set_index("trade_uuid")["mfe"].to_dict()
    for trade_uuid, group in long_df.groupby("trade_uuid", sort=True):
        cont = _bar_continuity_qa(group["bar_index"])
        if cont["missing_bar_count"] > 0 or not cont["monotonic"]:
            continuity_issues.append({"trade_uuid": trade_uuid, **cont})
        if not cont["monotonic"]:
            monotonic_violations.append(trade_uuid)

        if trade_uuid in closed_uuids:
            path_max_mfe = float(group["mfe_so_far"].max())
            trade_mfe = float(trades_mfe.get(trade_uuid, 0.0))
            mfe_deltas.append(path_max_mfe - trade_mfe)

    delta_arr = np.array(mfe_deltas) if mfe_deltas else np.array([])
    qa: dict[str, Any] = {
        "total_r_path_rows": int(len(long_df)),
        "distinct_trade_uuids": int(long_df["trade_uuid"].nunique()),
        "closed_trades_with_r_path": int(
            long_df.loc[long_df["trade_uuid"].isin(closed_uuids), "trade_uuid"].nunique()
        ),
        "lag_correction_applied": lag_correction,
        "velocity_window": velocity_window,
        "mfe_reconciliation": {
            "closed_trades_compared": len(mfe_deltas),
            "delta_mean": float(delta_arr.mean()) if len(delta_arr) else None,
            "delta_std": float(delta_arr.std()) if len(delta_arr) > 1 else None,
            "delta_min": float(delta_arr.min()) if len(delta_arr) else None,
            "delta_p25": float(np.percentile(delta_arr, 25)) if len(delta_arr) else None,
            "delta_p50": float(np.percentile(delta_arr, 50)) if len(delta_arr) else None,
            "delta_p75": float(np.percentile(delta_arr, 75)) if len(delta_arr) else None,
            "delta_max": float(delta_arr.max()) if len(delta_arr) else None,
            "abs_delta_gt_0.1r": int(np.sum(np.abs(delta_arr) > 0.1)) if len(delta_arr) else 0,
            "note": "delta = max(r_path.mfe_so_far) - trades.mfe; known 1-bar telemetry lag on some trades",
        },
        "bar_continuity": {
            "trades_with_missing_bars": sum(
                1 for c in continuity_issues if c.get("missing_bar_count", 0) > 0
            ),
            "trades_with_monotonicity_violation": len(monotonic_violations),
            "issues_sample": continuity_issues[:10],
        },
    }

    export_cols = [
        "trade_uuid",
        "bar_index",
        "timestamp",
        "unrealized_r",
        "mfe_so_far",
        "mae_so_far",
        "delta_r",
        "mfe_velocity",
        "mae_velocity",
        "session",
        "lag_corrected_mfe",
        "lag_corrected_mae",
        "vol_trail_distance",
        "struct_trail_distance",
    ]
    export_cols = [c for c in export_cols if c in long_df.columns]
    return long_df[export_cols], qa


def run_r_path(config: PipelineConfig) -> tuple[Path, Path]:
    long_df, qa = _build_r_path_long(config)

    config.datasets_dir.mkdir(parents=True, exist_ok=True)
    output_path = config.datasets_dir / R_PATH_LONG_OUTPUT
    long_df.to_parquet(output_path, index=False)

    from research_duo.manifest import sha256_file

    manifest_path = write_manifest(
        config.manifests_dir,
        pipeline_version=config.pipeline_version,
        repo_root=REPO_ROOT,
        config_snapshot=config.raw,
        stage="r_path",
        artifacts={
            "r_path_long_parquet": str(output_path),
            "r_path_long_sha256": sha256_file(output_path),
            "row_count": len(long_df),
        },
        warnings=[],
        qa=qa,
    )
    return output_path, manifest_path

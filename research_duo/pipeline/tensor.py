from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from research_duo.config_loader import PipelineConfig
from research_duo.manifest import sha256_file, write_manifest
from research_duo.paths import REPO_ROOT
from research_duo.pipeline.common import load_trades_parquet

TRADE_TENSOR_OUTPUT = "trade_tensor.parquet"

CORE_CHANNELS = [
    "unrealized_r_path",
    "mfe_so_far_path",
    "mae_so_far_path",
    "delta_r_path",
    "mfe_velocity_path",
]

OPTIONAL_CHANNELS = [
    "vol_trail_distance_path",
    "struct_trail_distance_path",
]

SOURCE_TO_CHANNEL = {
    "unrealized_r": "unrealized_r_path",
    "mfe_so_far": "mfe_so_far_path",
    "mae_so_far": "mae_so_far_path",
    "delta_r": "delta_r_path",
    "mfe_velocity": "mfe_velocity_path",
    "vol_trail_distance": "vol_trail_distance_path",
    "struct_trail_distance": "struct_trail_distance_path",
}


def _tensor_settings(config: PipelineConfig) -> dict[str, Any]:
    return config.raw.get("phase2", {}).get("tensor", {})


def _pad_series(values: np.ndarray, max_bars: int) -> list[float]:
    out = np.full(max_bars, np.nan, dtype=np.float32)
    n = min(len(values), max_bars)
    if n > 0:
        out[:n] = values[:n].astype(np.float32)
    return out.tolist()


def _build_tensor(config: PipelineConfig) -> tuple[pd.DataFrame, dict[str, Any]]:
    settings = _tensor_settings(config)
    max_bars = int(settings.get("max_bars", 512))
    include_trails = bool(settings.get("optional_trail_channels", True))

    r_path_path = config.datasets_dir / "r_path_long.parquet"
    if not r_path_path.exists():
        raise FileNotFoundError(f"Missing {r_path_path}. Run `python -m research_duo r_path` first.")

    long_df = pd.read_parquet(r_path_path)
    trades = load_trades_parquet(config)

    source_cols = list(SOURCE_TO_CHANNEL.keys())
    if not include_trails:
        source_cols = [c for c in source_cols if c not in ("vol_trail_distance", "struct_trail_distance")]

    rows: list[dict[str, Any]] = []
    truncated_count = 0

    for trade_uuid, group in long_df.groupby("trade_uuid", sort=True):
        g = group.sort_values("bar_index")
        n_bars = len(g)
        if n_bars > max_bars:
            truncated_count += 1

        row: dict[str, Any] = {"trade_uuid": trade_uuid, "path_length": n_bars, "max_bars": max_bars}
        for src_col in source_cols:
            if src_col not in g.columns:
                continue
            ch_name = SOURCE_TO_CHANNEL[src_col]
            row[ch_name] = _pad_series(g[src_col].to_numpy(dtype=float), max_bars)

        rows.append(row)

    tensor_df = pd.DataFrame(rows)
    merge_cols = ["trade_uuid", "symbol", "entry_time", "exit_time", "pnl_r", "hold_candles", "post_gate"]
    if "post_gate_audit_v2" in trades.columns:
        merge_cols.append("post_gate_audit_v2")
    tensor_df = tensor_df.merge(trades[merge_cols], on="trade_uuid", how="left")
    if "post_gate_audit_v2" in tensor_df.columns:
        tensor_df = tensor_df.rename(columns={"post_gate_audit_v2": "post_gate_audit_v2_aligned"})

    active_channels = [SOURCE_TO_CHANNEL[c] for c in source_cols if c in long_df.columns]
    qa: dict[str, Any] = {
        "max_bars": max_bars,
        "normalization": "none",
        "pad_value": "NaN",
        "channels": active_channels,
        "trade_count": int(len(tensor_df)),
        "truncated_trades": truncated_count,
        "channel_sources": {SOURCE_TO_CHANNEL[k]: k for k in source_cols if k in long_df.columns},
    }

    return tensor_df, qa


def run_tensor(config: PipelineConfig) -> tuple[Path, Path]:
    tensor_df, qa = _build_tensor(config)

    tensors_dir = Path(config.raw["paths"].get("tensors_dir", "research_duo/tensors"))
    if not tensors_dir.is_absolute():
        tensors_dir = REPO_ROOT / tensors_dir
    tensors_dir.mkdir(parents=True, exist_ok=True)

    output_path = tensors_dir / TRADE_TENSOR_OUTPUT
    tensor_df.to_parquet(output_path, index=False)

    manifest_path = write_manifest(
        config.manifests_dir,
        pipeline_version=config.pipeline_version,
        repo_root=REPO_ROOT,
        config_snapshot=config.raw,
        stage="tensor",
        artifacts={
            "trade_tensor_parquet": str(output_path),
            "trade_tensor_sha256": sha256_file(output_path),
            "row_count": len(tensor_df),
            "channels": qa["channels"],
        },
        warnings=[],
        qa=qa,
    )
    return output_path, manifest_path

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from research_duo.config_loader import PipelineConfig
from research_duo.paths import REPO_ROOT
from research_duo.phase3_io import load_trade_features


def phase4_settings(config: PipelineConfig) -> dict[str, Any]:
    return config.raw.get("phase4", {})


def parse_entry_time(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True)


def _load_trades_meta(config: PipelineConfig) -> pd.DataFrame:
    datasets_dir = config.raw.get("paths", {}).get("datasets_dir", "research_duo/datasets")
    path = Path(datasets_dir)
    if not path.is_absolute():
        path = REPO_ROOT / path
    trades_path = path / "trades_reconstructed.parquet"
    if not trades_path.exists():
        raise FileNotFoundError(f"Missing {trades_path}")
    cols = ["trade_uuid", "entry_time", "post_gate", "post_gate_audit_v2"]
    return pd.read_parquet(trades_path, columns=cols)


def cohens_d(a: np.ndarray, b: np.ndarray) -> float | None:
    if len(a) < 2 or len(b) < 2:
        return None
    var_a, var_b = float(a.var(ddof=1)), float(b.var(ddof=1))
    pooled = np.sqrt(((len(a) - 1) * var_a + (len(b) - 1) * var_b) / (len(a) + len(b) - 2))
    if pooled == 0:
        return None
    return float((a.mean() - b.mean()) / pooled)


def bootstrap_expectancy_ci(
    pnls: np.ndarray,
    n_samples: int,
    seed: int,
) -> dict[str, float | None]:
    if len(pnls) < 2:
        return {"low": None, "high": None, "mean": float(pnls.mean()) if len(pnls) else None}
    rng = np.random.default_rng(seed)
    means = []
    for _ in range(n_samples):
        sample = rng.choice(pnls, size=len(pnls), replace=True)
        means.append(sample.mean())
    arr = np.array(means)
    return {
        "mean": float(pnls.mean()),
        "low": float(np.percentile(arr, 2.5)),
        "high": float(np.percentile(arr, 97.5)),
    }


def load_cohort_frame(config: PipelineConfig) -> pd.DataFrame:
    """Closed trades with r_path, sorted by entry_time. Optionally filter post_gate."""
    settings = phase4_settings(config)
    features = load_trade_features(config)
    meta = _load_trades_meta(config)
    merge_cols = [c for c in meta.columns if c != "trade_uuid" and c not in features.columns]
    if merge_cols:
        df = features.merge(meta[["trade_uuid"] + merge_cols], on="trade_uuid", how="left")
    else:
        df = features.merge(meta[["trade_uuid", "entry_time"]], on="trade_uuid", how="left")
    df = df[df["path_bars"].notna() & (df["path_bars"] > 0)].copy()
    df["entry_dt"] = parse_entry_time(df["entry_time"])
    df = df.sort_values("entry_dt").reset_index(drop=True)

    if settings.get("post_gate_only", False):
        df = df[df["post_gate"]].copy().reset_index(drop=True)

    return df


def pathway_mask(df: pd.DataFrame, threshold_r: float, max_bars: int) -> pd.Series:
    col = f"bars_to_{str(threshold_r).replace('.', '_')}r_mfe"
    if col not in df.columns:
        return pd.Series(False, index=df.index)
    return df[col].notna() & (df[col] <= max_bars)


def pathway_metrics(df: pd.DataFrame, mask: pd.Series) -> dict[str, Any]:
    sub = df[mask.fillna(False)]
    rest = df[~mask.fillna(False)]
    pnls_in = sub["pnl_r"].dropna().to_numpy(dtype=float)
    pnls_out = rest["pnl_r"].dropna().to_numpy(dtype=float)
    return {
        "n": int(len(sub)),
        "n_complement": int(len(rest)),
        "win_rate": float((pnls_in > 0).mean()) if len(pnls_in) else None,
        "expectancy_r": float(pnls_in.mean()) if len(pnls_in) else None,
        "total_r": float(pnls_in.sum()) if len(pnls_in) else None,
        "cohens_d_pnl": cohens_d(pnls_in, pnls_out),
    }


def bars_to_column(threshold_r: float) -> str:
    return f"bars_to_{str(threshold_r).replace('.', '_')}r_mfe"

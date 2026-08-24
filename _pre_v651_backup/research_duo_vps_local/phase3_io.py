from __future__ import annotations

from pathlib import Path

import pandas as pd

from research_duo.config_loader import PipelineConfig
from research_duo.paths import REPO_ROOT


def _resolve_path(config: PipelineConfig, key: str, default: str) -> Path:
    raw = config.raw.get("paths", {}).get(key, default)
    path = Path(raw)
    return path if path.is_absolute() else REPO_ROOT / path


def r_path_long_path(config: PipelineConfig) -> Path:
    return _resolve_path(config, "datasets_dir", "research_duo/datasets") / "r_path_long.parquet"


def trade_tensor_path(config: PipelineConfig) -> Path:
    return _resolve_path(config, "tensors_dir", "research_duo/tensors") / "trade_tensor.parquet"


def trade_features_path(config: PipelineConfig) -> Path:
    return _resolve_path(config, "features_dir", "research_duo/features") / "trade_features.parquet"


def load_r_path_long(config: PipelineConfig) -> pd.DataFrame:
    path = r_path_long_path(config)
    if not path.exists():
        raise FileNotFoundError(f"Missing {path}. Run phase2 r_path first.")
    return pd.read_parquet(path)


def load_trade_tensor(config: PipelineConfig) -> pd.DataFrame:
    path = trade_tensor_path(config)
    if not path.exists():
        raise FileNotFoundError(f"Missing {path}. Run phase2 tensor first.")
    return pd.read_parquet(path)


def load_trade_features(config: PipelineConfig) -> pd.DataFrame:
    path = trade_features_path(config)
    if not path.exists():
        raise FileNotFoundError(f"Missing {path}. Run phase2 features first.")
    return pd.read_parquet(path)


def phase3_settings(config: PipelineConfig) -> dict:
    return config.raw.get("phase3", {})

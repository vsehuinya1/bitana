from __future__ import annotations

from typing import Any

import pandas as pd

from research_duo.config_loader import PipelineConfig
from research_duo.experiments.oos_common import phase4_settings


def build_walk_forward_folds(df: pd.DataFrame, config: PipelineConfig) -> dict[str, Any]:
    """
    Expanding-window folds ordered by entry_time.
    Final holdout = last N trades, never used in any train or validation fold.
    """
    settings = phase4_settings(config)
    min_train = int(settings.get("min_train_trades", 30))
    val_chunk = int(settings.get("validation_chunk", 10))
    holdout_n = int(settings.get("final_holdout_trades", 15))

    n = len(df)
    if n <= holdout_n + min_train + val_chunk:
        return {
            "folds": [],
            "holdout_uuids": [],
            "warning": "insufficient trades for walk-forward",
            "n_total": n,
        }

    main = df.iloc[: n - holdout_n].reset_index(drop=True)
    holdout = df.iloc[n - holdout_n :].reset_index(drop=True)
    holdout_uuids = holdout["trade_uuid"].tolist()

    folds: list[dict[str, Any]] = []
    train_end = min_train
    fold_id = 0

    while train_end + val_chunk <= len(main):
        train = main.iloc[:train_end]
        val = main.iloc[train_end : train_end + val_chunk]
        folds.append(
            {
                "fold_id": fold_id,
                "train_n": len(train),
                "val_n": len(val),
                "train_start": str(train["entry_dt"].iloc[0]),
                "train_end": str(train["entry_dt"].iloc[-1]),
                "val_start": str(val["entry_dt"].iloc[0]),
                "val_end": str(val["entry_dt"].iloc[-1]),
                "train_uuids": train["trade_uuid"].tolist(),
                "val_uuids": val["trade_uuid"].tolist(),
            }
        )
        fold_id += 1
        train_end += val_chunk

    return {
        "folds": folds,
        "holdout_uuids": holdout_uuids,
        "holdout_n": holdout_n,
        "n_main": len(main),
        "n_total": n,
        "min_train_trades": min_train,
        "validation_chunk": val_chunk,
    }

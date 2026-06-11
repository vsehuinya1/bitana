from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from research_duo.config_loader import PipelineConfig
from research_duo.experiments.oos_common import cohens_d
from research_duo.paths import REPO_ROOT
from research_duo.phase3_io import load_r_path_long


def phase5_settings(config: PipelineConfig) -> dict[str, Any]:
    return config.raw.get("phase5", {})


def _trades_path(config: PipelineConfig) -> Path:
    raw = config.raw.get("paths", {}).get("datasets_dir", "research_duo/datasets")
    path = Path(raw)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path / "trades_reconstructed.parquet"


def load_labeled_cohort(config: PipelineConfig, post_gate_only: bool = False) -> pd.DataFrame:
    settings = phase5_settings(config)
    explosive_mfe = float(settings.get("explosive_mfe_r", 1.0))
    explosive_bars = int(settings.get("explosive_max_bars", 15))
    early_dead_mfe = float(settings.get("early_dead_max_mfe_r", 0.3))
    early_dead_window = int(settings.get("early_dead_window_bars", 10))

    trades = pd.read_parquet(_trades_path(config))
    r_path = load_r_path_long(config)

    trades = trades[trades["r_path_bars"].fillna(0) > 0].copy()
    if post_gate_only:
        trades = trades[trades["post_gate"]].copy()

    labels = []
    for trade_uuid, grp in r_path.groupby("trade_uuid"):
        g = grp.sort_values("bar_index")
        row = trades[trades["trade_uuid"] == trade_uuid]
        if row.empty:
            continue
        pnl_r = float(row.iloc[0]["pnl_r"])
        early = g[g["bar_index"] <= early_dead_window]
        max_mfe_early = float(early["mfe_so_far"].max()) if not early.empty else 0.0
        max_mfe_early10 = max_mfe_early

        hits = g.loc[g["mfe_so_far"] >= explosive_mfe, "bar_index"]
        reached_bar = int(hits.iloc[0]) if not hits.empty else None

        is_explosive = pnl_r > 0 and reached_bar is not None and reached_bar <= explosive_bars
        is_early_dead = pnl_r <= 0 and max_mfe_early <= early_dead_mfe
        is_survivor = pnl_r > 0 and not is_explosive
        is_late_dead = pnl_r <= 0 and max_mfe_early > early_dead_mfe

        if is_explosive:
            entry_class = "EXPLOSIVE"
        elif is_early_dead:
            entry_class = "EARLY_DEAD"
        elif is_survivor:
            entry_class = "SURVIVOR"
        elif is_late_dead:
            entry_class = "LATE_DEAD"
        else:
            entry_class = "OTHER"

        labels.append(
            {
                "trade_uuid": trade_uuid,
                "entry_class": entry_class,
                "is_explosive": int(is_explosive),
                "is_early_dead": int(is_early_dead),
                "max_mfe_first_10": max_mfe_early10,
                "reached_1r_bar": reached_bar,
            }
        )

    label_df = pd.DataFrame(labels)
    df = trades.merge(label_df, on="trade_uuid", how="inner")
    return df.reset_index(drop=True)


def parse_confirmations(raw: str | float | None) -> dict[str, bool]:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return {}
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return {str(k): bool(v) for k, v in data.items()}
    except json.JSONDecodeError:
        pass
    return {}


def enrich_entry_fields(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    confirms = out["confirmations_trades"].apply(parse_confirmations) if "confirmations_trades" in out.columns else out.get("confirmations", pd.Series([{}]*len(out))).apply(parse_confirmations)
    for key in ("breakout", "imb", "vol", "body", "impulse", "momentum"):
        out[f"confirm_{key}"] = confirms.apply(lambda c: c.get(key, False))

    if "te_aggression" in out.columns:
        out["aggression_score"] = out["te_aggression"]
    elif "aggression" in out.columns:
        out["aggression_score"] = out["aggression"]

    if "te_decile" in out.columns:
        out["entry_decile"] = out["te_decile"]
    elif "decile" in out.columns:
        out["entry_decile"] = out["decile"]

    if "te_cascade_strength" in out.columns:
        out["cascade_strength"] = out["te_cascade_strength"]

    if "bd_distance_pct" not in out.columns or out["bd_distance_pct"].isna().all():
        ep = out.get("te_entry_price", out.get("entry_price"))
        rh = out.get("te_range_high")
        if ep is not None and rh is not None:
            out["bd_distance_pct"] = ((ep - rh) / rh.replace(0, np.nan) * 100.0)

    if "te_imb_z" in out.columns:
        out["imbalance_z"] = out["te_imb_z"]
    if "te_vol_z" in out.columns:
        out["vol_z"] = out["te_vol_z"]

    out["imb_fallback_flag"] = out["imbalance_z"].fillna(0) == 0 if "imbalance_z" in out.columns else False

    if "tier_group" not in out.columns and "config_tier" in out.columns:
        out["tier_group"] = out["config_tier"].apply(
            lambda t: "experimental" if t == "tier_c_experimental" else "proven"
        )

    if "is_experimental" not in out.columns:
        out["is_experimental"] = (out["tier_group"] == "experimental").astype(int)

    return out

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from research.data.sessions import get_session_at_time

from research_duo.config_loader import PipelineConfig, cohort_definitions_for_manifest
from research_duo.manifest import sha256_file, write_manifest
from research_duo.paths import REPO_ROOT


TRADES_OUTPUT = "trades_reconstructed.parquet"


def _open_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _parse_iso_to_ms(value: str | None) -> int | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def _parse_confirmations(raw: str | float | None) -> dict[str, bool]:
    if raw is None:
        return {}
    if isinstance(raw, float) and pd.isna(raw):
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


def _load_symbol_tiers(symbol_config: Path) -> dict[str, str]:
    if not symbol_config.exists():
        return {}
    with open(symbol_config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    symbols = cfg.get("symbols", {})
    tiers: dict[str, str] = {}
    for sym in symbols.get("tier_a", []) or []:
        tiers[str(sym)] = "tier_a"
    for sym in symbols.get("tier_b", []) or []:
        tiers[str(sym)] = "tier_b"
    for sym in symbols.get("tier_c_experimental", []) or []:
        tiers[str(sym)] = "tier_c_experimental"
    return tiers


def _load_trades_frame(config: PipelineConfig) -> tuple[pd.DataFrame, list[str]]:
    warnings: list[str] = []

    with _open_readonly(config.v5_db) as v5:
        trades = pd.read_sql_query("SELECT * FROM trades ORDER BY entry_time, trade_uuid", v5)

    if trades.empty:
        warnings.append("No closed trades found in v5_forward_test.db")
        return trades, warnings

    v6_available = config.v6_db.exists() and config.v6_db.stat().st_size > 0
    entries = pd.DataFrame()
    exits = pd.DataFrame()
    r_path_counts = pd.DataFrame()

    if v6_available:
        with _open_readonly(config.v6_db) as v6:
            entries = pd.read_sql_query("SELECT * FROM trade_entries", v6)
            exits = pd.read_sql_query("SELECT * FROM exit_attribution", v6)
            r_path_counts = pd.read_sql_query(
                """
                SELECT trade_uuid, COUNT(*) AS r_path_bars
                FROM r_path
                GROUP BY trade_uuid
                """,
                v6,
            )
    else:
        warnings.append("v6 telemetry unavailable; skipping entry/exit enrichment and r_path counts")

    if not entries.empty:
        entry_cols = {
            c: f"te_{c}" for c in entries.columns if c != "trade_uuid"
        }
        entries = entries.rename(columns=entry_cols)
        trades = trades.merge(entries, on="trade_uuid", how="left")

    if not exits.empty:
        exit_cols = {
            c: f"ea_{c}" for c in exits.columns if c != "trade_uuid"
        }
        exits = exits.rename(columns=exit_cols)
        trades = trades.merge(exits, on="trade_uuid", how="left")

    if not r_path_counts.empty:
        trades = trades.merge(r_path_counts, on="trade_uuid", how="left")
    else:
        trades["r_path_bars"] = pd.NA

    trades["r_path_bars"] = trades["r_path_bars"].fillna(0).astype(int)

    symbol_tiers = _load_symbol_tiers(config.symbol_config)
    trades["config_tier"] = trades["symbol"].map(symbol_tiers).fillna("unknown")
    trades["tier_group"] = trades["config_tier"].apply(
        lambda t: "experimental" if t == "tier_c_experimental" else ("proven" if t != "unknown" else "unknown")
    )

    post_gate_cutoff = _parse_iso_to_ms(config.post_gate_cutoff)
    entry_ms = trades["entry_time"].apply(_parse_iso_to_ms)
    trades["entry_time_ms"] = entry_ms

    # Primary cohort: bd_gate_live (entry >= BD gate deployment)
    trades["post_gate"] = entry_ms.apply(
        lambda ms: bool(post_gate_cutoff is not None and ms is not None and ms >= post_gate_cutoff)
    )

    # Secondary cohort: audit report v2 aligned (documented discrepancy)
    for cohort_key, col_name in (
        ("post_gate_audit_v2_aligned", "post_gate_audit_v2"),
    ):
        spec = config.cohorts.get(cohort_key)
        if spec and spec.cutoff:
            cutoff_ms = _parse_iso_to_ms(spec.cutoff)
            trades[col_name] = entry_ms.apply(
                lambda ms, c=cutoff_ms: bool(c is not None and ms is not None and ms >= c)
            )

    trades["is_winner"] = trades["pnl_r"] > 0
    trades["session"] = entry_ms.apply(
        lambda ms: get_session_at_time(ms) if ms is not None else "unknown"
    )

    trades["confirmations_trades"] = trades["confirmations"].apply(_parse_confirmations)
    if "te_confirmations" in trades.columns:
        trades["confirmations_telemetry"] = trades["te_confirmations"].apply(_parse_confirmations)
        trades["confirmations_mismatch"] = trades.apply(
            lambda row: row["confirmations_trades"] != row["confirmations_telemetry"]
            if isinstance(row.get("confirmations_telemetry"), dict) and row["confirmations_telemetry"]
            else False,
            axis=1,
        )
    else:
        trades["confirmations_telemetry"] = [{} for _ in range(len(trades))]
        trades["confirmations_mismatch"] = False

    trades["n_confirmations_trades"] = trades["confirmations_trades"].apply(
        lambda c: sum(1 for v in c.values() if v)
    )
    has_entry = (
        trades["te_entry_time"].notna()
        if "te_entry_time" in trades.columns
        else pd.Series(False, index=trades.index)
    )
    has_exit = (
        trades["ea_exit_time"].notna()
        if "ea_exit_time" in trades.columns
        else pd.Series(False, index=trades.index)
    )
    trades["telemetry_complete"] = trades["r_path_bars"].gt(0) & has_entry & has_exit

    trades["confirmations_trades"] = trades["confirmations_trades"].apply(json.dumps)
    trades["confirmations_telemetry"] = trades["confirmations_telemetry"].apply(json.dumps)

    return trades, warnings


def run_trades(config: PipelineConfig) -> tuple[Path, Path]:
    df, warnings = _load_trades_frame(config)

    config.datasets_dir.mkdir(parents=True, exist_ok=True)
    output_path = config.datasets_dir / TRADES_OUTPUT
    df.to_parquet(output_path, index=False)

    qa: dict[str, Any] = {
        "total_trades": int(len(df)),
        "post_gate_trades": int(df["post_gate"].sum()) if not df.empty else 0,
        "with_r_path": int((df["r_path_bars"] > 0).sum()) if not df.empty else 0,
        "telemetry_complete": int(df["telemetry_complete"].sum()) if not df.empty else 0,
        "confirmation_mismatches": int(df["confirmations_mismatch"].sum()) if not df.empty else 0,
        "cohort_counts": {},
        "cohort_definitions": cohort_definitions_for_manifest(config),
    }

    primary = config.cohorts.get("post_gate")
    qa["post_gate_rule"] = {
        "cohort_key": "post_gate",
        "name": primary.name if primary else "bd_gate_live",
        "field": primary.field if primary else "entry_time",
        "cutoff": primary.cutoff if primary else config.post_gate_cutoff,
        "rule": primary.rule if primary else "v5.trades WHERE entry_time >= cutoff AND closed",
        "requires_closed": True,
        "description": (
            "post_gate=true when entry_time >= cutoff. "
            "All rows in v5.trades are closed trades by definition."
        ),
    }

    if not df.empty:
        for cohort_key, col in (
            ("post_gate", "post_gate"),
            ("post_gate_audit_v2_aligned", "post_gate_audit_v2"),
        ):
            if col in df.columns:
                qa["cohort_counts"][cohort_key] = int(df[col].sum())

        if "post_gate_audit_v2" in df.columns:
            gap = df[df["post_gate_audit_v2"] & ~df["post_gate"]]
        else:
            gap = df.iloc[0:0]
        qa["post_gate_gap_vs_audit"] = {
            "count": int(len(gap)),
            "explanation": (
                "Trades in audit-aligned cohort (entry >= May 27 10:00 UTC) but NOT in bd_gate_live "
                "(entry >= May 27 21:00 UTC). These entered after an audit-era boundary but before "
                "the documented BD gate deployment time."
            ),
            "sample": gap[["trade_uuid", "symbol", "entry_time", "exit_time", "pnl_r"]]
            .head(8)
            .to_dict(orient="records")
            if len(gap) > 0
            else [],
        }

    manifest_path = write_manifest(
        config.manifests_dir,
        pipeline_version=config.pipeline_version,
        repo_root=REPO_ROOT,
        config_snapshot=config.raw,
        stage="trades",
        artifacts={
            "trades_parquet": str(output_path),
            "trades_parquet_sha256": sha256_file(output_path),
            "row_count": len(df),
            "cohort_definitions": cohort_definitions_for_manifest(config),
        },
        warnings=warnings,
        qa=qa,
    )
    return output_path, manifest_path

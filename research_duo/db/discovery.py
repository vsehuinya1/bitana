from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from research_duo.config_loader import PipelineConfig
from research_duo.manifest import sha256_file, write_manifest
from research_duo.paths import REPO_ROOT

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


@dataclass
class TableInfo:
    name: str
    columns: list[dict[str, Any]]
    row_count: int | None = None


@dataclass
class DiscoveryResult:
    v5_path: Path
    v6_path: Path
    v5_tables: dict[str, TableInfo] = field(default_factory=dict)
    v6_tables: dict[str, TableInfo] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    qa: dict[str, Any] = field(default_factory=dict)


def _open_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _list_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [row["name"] for row in rows]


def _table_info(conn: sqlite3.Connection, table: str) -> TableInfo:
    cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
    columns = [
        {
            "cid": row["cid"],
            "name": row["name"],
            "type": row["type"],
            "notnull": bool(row["notnull"]),
            "pk": bool(row["pk"]),
        }
        for row in cols
    ]
    try:
        count = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
    except sqlite3.Error:
        count = None
    return TableInfo(name=table, columns=columns, row_count=count)


def _validate_db_path(path: Path, label: str, required: bool) -> list[str]:
    warnings: list[str] = []
    if not path.exists():
        msg = f"{label} database not found: {path}"
        if required:
            raise FileNotFoundError(msg)
        warnings.append(msg)
        return warnings
    if path.stat().st_size == 0:
        msg = f"{label} database is empty (0 bytes): {path}. Sync from VPS before analysis."
        if required:
            raise ValueError(msg)
        warnings.append(msg)
    return warnings


def _investigate_orphan_uuid(
    v5_conn: sqlite3.Connection,
    v6_conn: sqlite3.Connection,
    trade_uuid: str,
) -> dict[str, Any]:
    """Classify an r_path UUID absent from v5.trades."""
    investigation: dict[str, Any] = {
        "trade_uuid": trade_uuid,
        "uuid_format_valid": bool(_UUID_RE.match(trade_uuid)),
    }

    closed = v5_conn.execute(
        "SELECT trade_uuid, symbol, entry_time, exit_time FROM trades WHERE trade_uuid = ?",
        (trade_uuid,),
    ).fetchone()
    open_pos = v5_conn.execute(
        "SELECT trade_uuid, symbol, side, entry_time, candles_held, entry_price FROM open_positions WHERE trade_uuid = ?",
        (trade_uuid,),
    ).fetchone()

    investigation["in_v5_trades"] = closed is not None
    investigation["in_v5_open_positions"] = open_pos is not None

    if open_pos:
        investigation["classification"] = "open_position"
        investigation["explanation"] = (
            "r_path telemetry for a live position not yet written to v5.trades. "
            "Expected: trade_entries + r_path logged at entry; trades row appears only on close."
        )
        investigation["open_position"] = dict(open_pos)
    elif closed:
        investigation["classification"] = "closed_trade_missing_join"
        investigation["explanation"] = "Unexpected: row in trades but excluded from UUID set used for QA."
    else:
        investigation["classification"] = "deleted_or_never_persisted"
        investigation["explanation"] = (
            "No row in v5.trades or open_positions. Possible deleted trade or telemetry-only UUID."
        )

    entry = v6_conn.execute(
        "SELECT symbol, entry_time, side FROM trade_entries WHERE trade_uuid = ?",
        (trade_uuid,),
    ).fetchone()
    exit_row = v6_conn.execute(
        "SELECT exit_time, exit_reason, pnl_r FROM exit_attribution WHERE trade_uuid = ?",
        (trade_uuid,),
    ).fetchone()
    r_path_stats = v6_conn.execute(
        """
        SELECT COUNT(*) AS bars, MIN(bar_index) AS min_bar, MAX(bar_index) AS max_bar,
               MIN(timestamp) AS first_ts, MAX(timestamp) AS last_ts
        FROM r_path WHERE trade_uuid = ?
        """,
        (trade_uuid,),
    ).fetchone()

    investigation["v6_trade_entry"] = dict(entry) if entry else None
    investigation["v6_exit_attribution"] = dict(exit_row) if exit_row else None
    investigation["v6_r_path"] = dict(r_path_stats) if r_path_stats else None

    if investigation["classification"] == "deleted_or_never_persisted" and entry and not exit_row:
        investigation["classification"] = "telemetry_before_close_or_deleted"
        investigation["explanation"] = (
            "Has trade_entries and r_path but no v5 row and no exit_attribution. "
            "Consistent with open position OR a trade removed from v5 before close was recorded."
        )

    return investigation


def _cross_db_qa(v5_conn: sqlite3.Connection, v6_conn: sqlite3.Connection | None) -> dict[str, Any]:
    qa: dict[str, Any] = {}
    trade_uuids = {
        row["trade_uuid"]
        for row in v5_conn.execute("SELECT trade_uuid FROM trades WHERE trade_uuid IS NOT NULL")
    }
    open_uuids = {
        row["trade_uuid"]
        for row in v5_conn.execute("SELECT trade_uuid FROM open_positions WHERE trade_uuid IS NOT NULL")
    }
    qa["v5_closed_trades"] = len(trade_uuids)
    qa["v5_open_positions"] = len(open_uuids)

    if v6_conn is None:
        qa["v6_available"] = False
        return qa

    qa["v6_available"] = True
    r_path_uuids = {
        row["trade_uuid"]
        for row in v6_conn.execute("SELECT DISTINCT trade_uuid FROM r_path")
    }
    qa["v6_r_path_trades"] = len(r_path_uuids)
    orphan_uuids = sorted(r_path_uuids - trade_uuids)
    qa["orphan_r_path_uuids"] = orphan_uuids
    qa["orphan_r_path_count"] = len(orphan_uuids)

    investigations = []
    for uuid in orphan_uuids:
        inv = _investigate_orphan_uuid(v5_conn, v6_conn, uuid)
        investigations.append(inv)
    qa["orphan_r_path_investigations"] = investigations

    if investigations:
        qa["orphan_r_path_summary"] = {
            inv["classification"]: sum(
                1 for i in investigations if i["classification"] == inv["classification"]
            )
            for inv in investigations
        }

    trades_without_r_path = sorted(trade_uuids - r_path_uuids)
    qa["trades_without_r_path_count"] = len(trades_without_r_path)
    qa["trades_without_r_path_sample"] = trades_without_r_path[:10]

    # r_path on open positions (expected orphans)
    qa["open_positions_with_r_path"] = sorted(open_uuids & r_path_uuids)

    return qa


def discover(config: PipelineConfig) -> DiscoveryResult:
    warnings: list[str] = []
    warnings.extend(_validate_db_path(config.v5_db, "v5", required=True))

    v6_available = config.v6_db.exists() and config.v6_db.stat().st_size > 0
    if not v6_available:
        warnings.append(
            f"v6 telemetry database missing or empty: {config.v6_db}. "
            "Trade reconstruction will use v5 trades only."
        )

    result = DiscoveryResult(v5_path=config.v5_db, v6_path=config.v6_db, warnings=warnings)

    with _open_readonly(config.v5_db) as v5_conn:
        actual_v5 = set(_list_tables(v5_conn))
        for table in config.expected_v5_tables:
            if table not in actual_v5:
                warnings.append(f"Expected v5 table missing: {table}")
        for table in sorted(actual_v5):
            result.v5_tables[table] = _table_info(v5_conn, table)

        v6_conn = None
        if v6_available:
            v6_conn = _open_readonly(config.v6_db)
        try:
            if v6_conn:
                actual_v6 = set(_list_tables(v6_conn))
                for table in config.expected_v6_tables:
                    if table not in actual_v6:
                        warnings.append(f"Expected v6 table missing: {table}")
                for table in sorted(actual_v6):
                    result.v6_tables[table] = _table_info(v6_conn, table)
                result.qa = _cross_db_qa(v5_conn, v6_conn)
            else:
                result.qa = _cross_db_qa(v5_conn, None)
        finally:
            if v6_conn:
                v6_conn.close()

    for inv in result.qa.get("orphan_r_path_investigations", []):
        if inv.get("classification") == "open_position":
            warnings.append(
                f"Orphan r_path UUID {inv['trade_uuid'][:8]}… is an open position "
                f"({inv.get('open_position', {}).get('symbol', '?')}) — harmless, not a data bug."
            )

    return result


def _tables_to_dict(tables: dict[str, TableInfo]) -> dict[str, Any]:
    return {
        name: {
            "row_count": info.row_count,
            "columns": info.columns,
        }
        for name, info in tables.items()
    }


def run_discovery(config: PipelineConfig) -> Path:
    from research_duo.config_loader import cohort_definitions_for_manifest

    result = discover(config)

    manifest_path = write_manifest(
        config.manifests_dir,
        pipeline_version=config.pipeline_version,
        repo_root=REPO_ROOT,
        config_snapshot=config.raw,
        stage="discovery",
        artifacts={
            "v5_db": str(config.v5_db),
            "v6_db": str(config.v6_db),
            "v5_db_sha256": sha256_file(config.v5_db),
            "v6_db_sha256": sha256_file(config.v6_db) if config.v6_db.exists() else None,
            "v5_schema": _tables_to_dict(result.v5_tables),
            "v6_schema": _tables_to_dict(result.v6_tables),
            "cohort_definitions": cohort_definitions_for_manifest(config),
        },
        warnings=result.warnings,
        qa=result.qa,
    )
    return manifest_path

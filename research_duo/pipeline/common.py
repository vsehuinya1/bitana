from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


def open_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def parse_iso_to_ms(value: str | None) -> int | None:
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


def load_trades_parquet(config) -> pd.DataFrame:
    path = config.datasets_dir / "trades_reconstructed.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"Missing {path}. Run `python -m research_duo trades` first."
        )
    return pd.read_parquet(path)


def sha256_file(path: Path) -> str | None:
    from research_duo.manifest import sha256_file as _sha

    return _sha(path)

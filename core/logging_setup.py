"""
Bitana Structured Logging Setup

- structlog JSON + console output
- RotatingFileHandler with gzip compression
- Separate trade JSONL log
- Retention cleanup on startup
"""
from __future__ import annotations

import gzip
import json
import logging
import logging.handlers
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import structlog


def _gzip_rotator(source: str, dest: str) -> None:
    with open(source, "rb") as f_in:
        with gzip.open(f"{dest}.gz", "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
    os.remove(source)


def _gzip_namer(name: str) -> str:
    return name + ".gz"


def _cleanup_old_logs(log_dir: Path, retention_days: int) -> None:
    if not log_dir.exists():
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    for f in log_dir.glob("*.gz"):
        try:
            if datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc) < cutoff:
                f.unlink()
        except OSError:
            pass


def setup_logging(
    level: str = "INFO",
    log_file: str = "logs/bitana.log",
    max_bytes: int = 52_428_800,
    backup_count: int = 10,
    retention_days: int = 30,
) -> None:
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _cleanup_old_logs(log_path.parent, retention_days)

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers = [h for h in root.handlers if not isinstance(h, (logging.StreamHandler, logging.FileHandler))]

    console = logging.StreamHandler()
    console.setLevel(getattr(logging, level.upper(), logging.INFO))

    fh = logging.handlers.RotatingFileHandler(
        str(log_path), maxBytes=max_bytes,
        backupCount=backup_count, encoding="utf-8",
    )
    fh.rotator = _gzip_rotator  # type: ignore
    fh.namer = _gzip_namer  # type: ignore
    fh.setLevel(logging.DEBUG)

    root.addHandler(console)
    root.addHandler(fh)

    shared = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.format_exc_info,
    ]
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    console.setFormatter(structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(colors=True),
        ],
    ))
    fh.setFormatter(structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    ))


def get_logger(name: str = "", **kwargs) -> structlog.stdlib.BoundLogger:
    log = structlog.get_logger(name)
    if kwargs:
        log = log.bind(**kwargs)
    return log


class TradeLogger:
    """Append-only JSONL logger for trade records."""

    _MAX_SIZE = 50 * 1024 * 1024  # 50 MB

    def __init__(self, path: str = "logs/trades.jsonl") -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _rotate_if_needed(self) -> None:
        if self._path.exists() and self._path.stat().st_size > self._MAX_SIZE:
            rotated = self._path.with_suffix(".jsonl.1")
            if rotated.exists():
                rotated.unlink()
            self._path.rename(rotated)

    def log_trade(self, trade_data: dict) -> None:
        self._rotate_if_needed()
        trade_data["_logged_at"] = datetime.now(timezone.utc).isoformat()
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(trade_data, default=str) + "\n")

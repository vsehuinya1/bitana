from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def get_git_commit(repo_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def sha256_file(path: Path) -> str | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(
    manifests_dir: Path,
    *,
    pipeline_version: str,
    repo_root: Path,
    config_snapshot: dict[str, Any],
    stage: str,
    artifacts: dict[str, Any],
    warnings: list[str],
    qa: dict[str, Any] | None = None,
) -> Path:
    manifests_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    manifest_path = manifests_dir / f"run_{ts}_{stage}.json"

    payload = {
        "pipeline_version": pipeline_version,
        "stage": stage,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": get_git_commit(repo_root),
        "config": config_snapshot,
        "artifacts": artifacts,
        "qa": qa or {},
        "warnings": warnings,
    }

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")

    return manifest_path

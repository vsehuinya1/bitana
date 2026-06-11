from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from research_duo.paths import DEFAULT_CONFIG_PATH, REPO_ROOT


@dataclass(frozen=True)
class CohortDefinition:
    name: str
    description: str
    rule: str
    field: str
    cutoff: str
    reference: str = ""
    note: str = ""


@dataclass(frozen=True)
class PipelineConfig:
    pipeline_version: str
    random_seed: int
    v5_db: Path
    v6_db: Path
    symbol_config: Path
    datasets_dir: Path
    manifests_dir: Path
    cohorts: dict[str, CohortDefinition]
    post_gate_cutoff: str  # primary cohort cutoff (bd_gate_live)
    expected_v5_tables: tuple[str, ...]
    expected_v6_tables: tuple[str, ...]
    raw: dict[str, Any]


def _resolve(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def _parse_cohorts(raw_cohorts: dict[str, Any]) -> dict[str, CohortDefinition]:
    cohorts: dict[str, CohortDefinition] = {}
    for key, spec in (raw_cohorts or {}).items():
        if not isinstance(spec, dict):
            continue
        cohorts[key] = CohortDefinition(
            name=str(spec.get("name", key)),
            description=str(spec.get("description", "")).strip(),
            rule=str(spec.get("rule", "")),
            field=str(spec.get("field", "entry_time")),
            cutoff=str(spec.get("cutoff", "")),
            reference=str(spec.get("reference", "")),
            note=str(spec.get("note", "")).strip(),
        )
    return cohorts


def load_config(config_path: Path | None = None) -> PipelineConfig:
    path = config_path or DEFAULT_CONFIG_PATH
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    paths = raw["paths"]
    expected = raw.get("expected_tables", {})
    cohorts = _parse_cohorts(raw.get("cohorts", {}))
    post_gate_cutoff = cohorts.get("post_gate", CohortDefinition("", "", "", "", "")).cutoff
    if not post_gate_cutoff:
        post_gate_cutoff = str(raw.get("cohorts", {}).get("post_gate_cutoff", ""))

    return PipelineConfig(
        pipeline_version=str(raw.get("pipeline_version", "0.0.0")),
        random_seed=int(raw.get("random_seed", 42)),
        v5_db=_resolve(paths["v5_db"]),
        v6_db=_resolve(paths["v6_db"]),
        symbol_config=_resolve(paths["symbol_config"]),
        datasets_dir=_resolve(paths["datasets_dir"]),
        manifests_dir=_resolve(paths["manifests_dir"]),
        cohorts=cohorts,
        post_gate_cutoff=post_gate_cutoff,
        expected_v5_tables=tuple(expected.get("v5", ())),
        expected_v6_tables=tuple(expected.get("v6", ())),
        raw=raw,
    )


def cohort_definitions_for_manifest(config: PipelineConfig) -> dict[str, Any]:
    return {
        key: {
            "name": c.name,
            "description": c.description,
            "rule": c.rule,
            "field": c.field,
            "cutoff": c.cutoff,
            "reference": c.reference,
            "note": c.note,
        }
        for key, c in config.cohorts.items()
    }

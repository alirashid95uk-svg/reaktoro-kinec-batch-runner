"""Minimal reproducibility identities for DoE designs, samples, and runs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

DESIGN_HASH_SCHEMA = "design_spec_hash_v1"
SAMPLE_HASH_SCHEMA = "design_point_fingerprint_v1"


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(value: Any) -> Any:
    if is_dataclass(value):
        value = asdict(value)
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("canonical mappings require string keys")
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [_canonical(item) for item in value]
        return sorted(items, key=lambda item: canonical_json_bytes(item))
    if isinstance(value, Enum):
        return _canonical(value.value)
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("canonical Decimal must be finite")
        return "0" if value.is_zero() else format(value.normalize(), "f")
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise ValueError("canonical float must be finite")
        return 0.0 if value == 0.0 else value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"unsupported canonical value type: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        _canonical(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def hash_payload(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def design_spec_hash_v1(resolved_spec: dict[str, Any]) -> str:
    return hash_payload({"hash_schema": DESIGN_HASH_SCHEMA, "resolved_spec": resolved_spec})


def database_identity(case: Any) -> dict[str, Any]:
    database = case.config.database
    if database.source == "embedded":
        return {"source": "embedded", "name": database.name}
    if case.database_path is None:
        raise ValueError("resolved local database path is missing")
    return {"source": "local", "sha256": file_sha256(case.database_path)}


def kinetics_identity(case: Any) -> dict[str, Any]:
    kinetics = case.config.kinetics
    if not kinetics.enabled:
        return {"enabled": False}
    if case.kinetics_path is None:
        raise ValueError("resolved kinetics path is missing")
    return {
        "enabled": True,
        "model": kinetics.model,
        "sha256": file_sha256(case.kinetics_path),
    }


def design_point_payload(case: Any) -> dict[str, Any]:
    config = case.config
    return {
        "hash_schema": SAMPLE_HASH_SCHEMA,
        "database_identity": database_identity(case),
        "activity_models": config.activity_models.model_dump(mode="json"),
        "physical": config.physical.model_dump(mode="json"),
        "brine": config.brine.model_dump(mode="json"),
        "co2": config.co2.model_dump(mode="json"),
        "redox": config.redox.model_dump(mode="json"),
        "kinetics_identity": kinetics_identity(case),
        "minerals": [item.model_dump(mode="json") for item in config.minerals],
        "solver": {
            "workflow": config.solver.workflow.model_dump(mode="json"),
            "timestep": config.solver.timestep.model_dump(mode="json"),
            "resolved_time_seconds": {
                "duration_s": case.duration_s,
                "dt_s": case.dt_s,
                "dt_initial_s": case.dt_initial_s,
                "dt_min_s": case.dt_min_s,
                "dt_max_s": case.dt_max_s,
                "hard_event_time_tolerance_s": case.hard_event_time_tolerance_s,
                "hard_event_restart_dt_s": case.hard_event_restart_dt_s,
            },
        },
    }


def design_point_fingerprint_v1(case: Any) -> str:
    return hash_payload(design_point_payload(case))


def batch_runner_source_sha256(project_root: str | Path) -> str:
    root = Path(project_root).resolve()
    files = [root / "runner.py"]
    batch_root = root / "batch_runner"
    if batch_root.is_dir():
        files.extend(batch_root.rglob("*.py"))
    inventory = []
    for path in sorted(
        {item.resolve() for item in files if item.is_file()}, key=lambda p: p.as_posix()
    ):
        inventory.append(
            {"path": path.relative_to(root).as_posix(), "sha256": file_sha256(path)}
        )
    return hash_payload({"schema": "batch_runner_source_v1", "files": inventory})

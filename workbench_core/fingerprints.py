"""Canonical SHA-256 fingerprints for validated scientific and operational inputs."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping

from pydantic import BaseModel

from workbench_core.schemas.common import CodeIdentity, DependencyIdentity, EnvironmentIdentity


FINGERPRINT_SCHEMA_VERSION = "1.0"
APPROVED_OPERATIONAL_PATHS: frozenset[tuple[str, ...]] = frozenset(
    {
        ("case", "name"),
        ("paths", "output_dir"),
        ("output_dir",),
        ("run_directory",),
        ("run_id",),
        ("queue_id",),
        ("study_id",),
        ("log_path",),
        ("logging",),
        ("process_control",),
        ("operational_metadata",),
        ("display_preferences",),
        ("report_preferences",),
    }
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    normalised = _normalise(value)
    return json.dumps(
        normalised,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def scientific_fingerprint(
    resolved_configuration: Mapping[str, Any] | BaseModel,
    *,
    dependency_identities: Iterable[DependencyIdentity],
    code_identity: CodeIdentity,
    environment_identity: EnvironmentIdentity,
    configuration_schema_version: str,
) -> str:
    if not configuration_schema_version:
        raise ValueError("configuration_schema_version cannot be empty")
    configuration = _normalise(resolved_configuration)
    if not isinstance(configuration, dict):
        raise TypeError("resolved_configuration must be a mapping or Pydantic model")
    for path in APPROVED_OPERATIONAL_PATHS:
        _remove_path(configuration, path)
    dependencies = sorted(
        dependency_identities,
        key=lambda item: (item.logical_name, item.source, item.version or ""),
    )
    return canonical_sha256(
        {
            "fingerprint_schema_version": FINGERPRINT_SCHEMA_VERSION,
            "resolved_scientific_configuration": configuration,
            "dependency_identities": dependencies,
            "code_identity": code_identity,
            "environment_identity": environment_identity,
            "configuration_schema_version": configuration_schema_version,
        }
    )


def operational_fingerprint(operational_metadata: Mapping[str, Any] | BaseModel) -> str:
    return canonical_sha256(
        {
            "fingerprint_schema_version": FINGERPRINT_SCHEMA_VERSION,
            "operational_metadata": operational_metadata,
        }
    )


def _remove_path(mapping: dict[str, Any], path: tuple[str, ...]) -> None:
    if not path:
        return
    current: Any = mapping
    for segment in path[:-1]:
        if not isinstance(current, dict) or segment not in current:
            return
        current = current[segment]
    if isinstance(current, dict):
        current.pop(path[-1], None)


def _normalise(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _normalise(value.model_dump(mode="python"))
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical mappings require string keys")
            result[key] = _normalise(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_normalise(item) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [_normalise(item) for item in value]
        return sorted(items, key=lambda item: canonical_json_bytes(item))
    if isinstance(value, Enum):
        return _normalise(value.value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("canonical datetime must be timezone-aware")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("canonical Decimal must be finite")
        return format(value.normalize(), "f")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical float must be finite")
        return value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"unsupported canonical value type: {type(value).__name__}")

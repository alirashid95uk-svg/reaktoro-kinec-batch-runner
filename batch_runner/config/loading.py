"""Load source-case YAML into the validated and resolved runtime contract.

This module owns duplicate-key rejection, unresolved-template detection, and
the transition from raw YAML to :class:`CaseConfig`. It delegates filesystem,
unit, and schedule resolution to :mod:`batch_runner.config.resolution` and
never constructs Reaktoro objects.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

from .case import CaseConfig
from .resolution import ResolvedCase, resolve_case


_PLACEHOLDER = re.compile(
    r"^(?:REQUIRED|OPTIONAL|TBD_SOURCE_REQUIRED|REQUIRED_IF_[A-Z0-9_]+)$"
)


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as error:
            raise ValueError(
                f"YAML mapping key is not hashable at line {key_node.start_mark.line + 1}"
            ) from error
        if duplicate:
            raise ValueError(
                f"duplicate YAML key {key!r} at line {key_node.start_mark.line + 1}"
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def _artifact_root_from_argument(value: str | Path | None) -> Path | None:
    if value is None:
        environment_value = os.environ.get("BATCH_RUNNER_ARTIFACT_ROOT")
        if not environment_value:
            return None
        value = environment_value
    root = Path(value).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"artifact root does not exist: {root}")
    return root


def _resolve_packaged_dependency(root: Path, value: str, label: str) -> str:
    path = Path(value)
    if path.is_absolute():
        return str(path.resolve())
    resolved = (root / path).resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"{label} escapes the DoE artifact root: {value}")
    return str(resolved)


def _apply_artifact_root(raw: dict[str, Any], root: Path) -> None:
    """Resolve only packaged local scientific dependencies against *root*.

    Normal batch-runner configuration retains project-root path semantics. DoE
    launchers opt into this projection explicitly (or through the documented
    worker environment variable) so package-relative database and kinetics
    files remain portable without changing output or validation-script paths.
    """
    database = raw.get("database")
    if isinstance(database, dict) and database.get("source") == "local":
        value = database.get("path")
        if not isinstance(value, str) or not value:
            raise ValueError("local database requires a path")
        database["path"] = _resolve_packaged_dependency(root, value, "database.path")

    kinetics = raw.get("kinetics")
    if isinstance(kinetics, dict) and kinetics.get("enabled"):
        value = kinetics.get("path")
        if value is not None:
            if not isinstance(value, str) or not value:
                raise ValueError("enabled kinetics path must be a non-empty string")
            kinetics["path"] = _resolve_packaged_dependency(root, value, "kinetics.path")


def load_case(
    config_path: str | Path,
    *,
    output_dir_override: str | Path | None = None,
    artifact_root: str | Path | None = None,
) -> ResolvedCase:
    """Read, validate, and resolve one runnable YAML case.

    Args:
        config_path: Source YAML path. Relative paths are resolved by
            :class:`pathlib.Path` before project-relative values inside the
            case are processed.
        output_dir_override: Optional operational output location used by
            preflight and managed-run preparation. It changes only the
            in-memory raw mapping; the source file is not rewritten.
        artifact_root: Optional DoE package root. When provided, only relative
            local database and kinetics paths are resolved against this root;
            all normal non-DoE path semantics remain unchanged. Worker
            subprocesses may supply the same value with
            ``BATCH_RUNNER_ARTIFACT_ROOT``.

    Returns:
        A frozen :class:`ResolvedCase` containing the validated source model,
        canonical paths, time values in seconds, schedules, and source hash.

    Raises:
        FileNotFoundError: If the source or a required resolved file is absent.
        ValueError: If YAML structure, placeholders, duplicate keys, units,
            paths, or resolved schedules are invalid.
        pydantic.ValidationError: If fields or cross-feature combinations do
            not satisfy :class:`CaseConfig`.
        FileExistsError: If the resolved output directory already exists.

    Side Effects:
        Reads the source YAML and referenced filesystem paths. It creates no
        directories and does not modify the source case.
    """

    path = Path(config_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"case config does not exist: {path}")

    source_bytes = path.read_bytes()
    raw = yaml.load(source_bytes.decode("utf-8"), Loader=_UniqueKeyLoader)
    if not isinstance(raw, dict):
        raise ValueError(f"case config must contain a YAML mapping: {path}")
    placeholders = list(_placeholder_paths(raw))
    if placeholders:
        raise ValueError(
            "case config contains unresolved placeholder sentinel(s): "
            + ", ".join(placeholders)
        )

    resolved_artifact_root = _artifact_root_from_argument(artifact_root)
    if resolved_artifact_root is not None:
        _apply_artifact_root(raw, resolved_artifact_root)

    if output_dir_override is not None:
        raw["paths"]["output_dir"] = str(output_dir_override)

    config = CaseConfig.model_validate(raw)
    return resolve_case(
        config,
        path,
        source_config_sha256=hashlib.sha256(source_bytes).hexdigest(),
    )


def _placeholder_paths(value: Any, path: str = "$") -> Iterator[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            key_path = f"{path}.{key}"
            if isinstance(key, str) and _PLACEHOLDER.fullmatch(key):
                yield f"{key_path} (key)"
            yield from _placeholder_paths(child, key_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _placeholder_paths(child, f"{path}[{index}]")
    elif isinstance(value, str) and _PLACEHOLDER.fullmatch(value):
        yield path

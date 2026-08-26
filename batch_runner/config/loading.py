"""Load source-case YAML into the validated and resolved runtime contract.

This module owns duplicate-key rejection, unresolved-template detection, and
the transition from raw YAML to :class:`CaseConfig`. It delegates filesystem,
unit, and schedule resolution to :mod:`batch_runner.config.resolution` and
never constructs Reaktoro objects.
"""

from __future__ import annotations

import hashlib
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


def load_case(
    config_path: str | Path,
    *,
    output_dir_override: str | Path | None = None,
) -> ResolvedCase:
    """Read, validate, and resolve one runnable YAML case.

    Args:
        config_path: Source YAML path. Relative paths are resolved by
            :class:`pathlib.Path` before project-relative values inside the
            case are processed.
        output_dir_override: Optional operational output location used by
            preflight and managed-run preparation. It changes only the
            in-memory raw mapping; the source file is not rewritten.

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
    if output_dir_override is not None:
        raw["paths"]["output_dir"] = str(output_dir_override)

    config = CaseConfig.model_validate(raw)
    return resolve_case(config, path, source_config_sha256=hashlib.sha256(source_bytes).hexdigest())


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

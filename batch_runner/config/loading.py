"""Strict YAML case loading."""

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

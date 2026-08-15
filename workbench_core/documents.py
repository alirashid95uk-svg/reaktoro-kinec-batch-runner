"""Round-trip YAML case documents with transactional edits and safe saving."""

from __future__ import annotations

import copy
import difflib
import hashlib
import re
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any, Iterable, TypeAlias

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.constructor import DuplicateKeyError
from ruamel.yaml.error import YAMLError

from workbench_core.persistence import atomic_write_bytes


_CONFLICT_MARKER = re.compile(r"^(?:<{7}|={7}|>{7}|\|{7})(?: .*)?$", re.MULTILINE)
_TEMPLATE_SENTINEL = re.compile(
    r"^(?:REQUIRED|REQUIRED_[A-Z0-9_]+|OPTIONAL|TBD_SOURCE_REQUIRED)$"
)


class CaseDocumentError(ValueError):
    pass


class MergeConflictError(CaseDocumentError):
    pass


class TemplateSentinelError(CaseDocumentError):
    pass


class ExternalModificationError(CaseDocumentError):
    pass


@dataclass(frozen=True)
class NamedListItem:
    """Stable list selector, for example a mineral row selected by exact name."""

    value: str
    identity_key: str = "name"


YamlPathSegment: TypeAlias = str | int | NamedListItem
YamlPath: TypeAlias = tuple[YamlPathSegment, ...]


@dataclass(frozen=True)
class SavedCaseRevision:
    path: Path
    sha256: str
    saved_bytes: bytes


class CaseDocument:
    def __init__(
        self,
        data: CommentedMap,
        *,
        source_path: Path | None = None,
        source_sha256: str | None = None,
    ) -> None:
        self._data = data
        self._source_path = source_path
        self._source_sha256 = source_sha256
        self._undo: list[str] = []
        self._redo: list[str] = []
        self._saved_text = self.to_text()

    @classmethod
    def from_text(cls, text: str) -> "CaseDocument":
        return cls(_parse_yaml(text))

    @classmethod
    def load(cls, path: str | Path) -> "CaseDocument":
        source = Path(path).resolve()
        raw = source.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CaseDocumentError(f"case YAML must be UTF-8: {source}") from exc
        return cls(
            _parse_yaml(text),
            source_path=source,
            source_sha256=hashlib.sha256(raw).hexdigest(),
        )

    @property
    def source_path(self) -> Path | None:
        return self._source_path

    @property
    def data(self) -> CommentedMap:
        return copy.deepcopy(self._data)

    @property
    def is_dirty(self) -> bool:
        return self.to_text() != self._saved_text

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def to_text(self) -> str:
        buffer = StringIO()
        _yaml().dump(self._data, buffer)
        return buffer.getvalue()

    def apply_text(self, text: str) -> None:
        self._commit(_parse_yaml(text))

    def patch(self, path: Iterable[YamlPathSegment], value: Any, *, create: bool = False) -> None:
        segments = tuple(path)
        if not segments:
            raise CaseDocumentError("YAML patch path cannot be empty")
        candidate = copy.deepcopy(self._data)
        parent = candidate
        for segment in segments[:-1]:
            parent = _select(parent, segment)
        _assign(parent, segments[-1], value, create=create)
        self._commit(candidate)

    def remove(self, path: Iterable[YamlPathSegment]) -> None:
        segments = tuple(path)
        if not segments:
            raise CaseDocumentError("YAML removal path cannot be empty")
        candidate = copy.deepcopy(self._data)
        parent = candidate
        for segment in segments[:-1]:
            parent = _select(parent, segment)
        final = segments[-1]
        try:
            del parent[final]
        except (KeyError, IndexError, TypeError) as error:
            raise CaseDocumentError(f"YAML removal path not found: {_format_path(segments)}") from error
        self._commit(candidate)

    def rename_mapping_key(self, path: Iterable[YamlPathSegment], replacement: str) -> None:
        segments = tuple(path)
        if not replacement or not isinstance(segments[-1] if segments else None, str):
            raise CaseDocumentError("mapping-key rename requires a non-empty replacement")
        candidate = copy.deepcopy(self._data)
        parent = candidate
        for segment in segments[:-1]:
            parent = _select(parent, segment)
        original = segments[-1]
        if not isinstance(parent, (dict, CommentedMap)) or original not in parent:
            raise CaseDocumentError(f"mapping key not found: {_format_path(segments)}")
        if replacement in parent:
            raise CaseDocumentError(f"mapping key already exists: {replacement}")
        index = list(parent).index(original)
        value = parent[original]
        comment = parent.ca.items.pop(original, None) if isinstance(parent, CommentedMap) else None
        del parent[original]
        if isinstance(parent, CommentedMap):
            parent.insert(index, replacement, value)
            if comment is not None:
                parent.ca.items[replacement] = comment
        else:
            parent[replacement] = value
        self._commit(candidate)

    def sentinel_paths(self) -> tuple[YamlPath, ...]:
        found: list[YamlPath] = []
        _find_sentinels(self._data, (), found)
        return tuple(found)

    def assert_runnable(self) -> None:
        sentinels = self.sentinel_paths()
        if sentinels:
            rendered = ", ".join(_format_path(path) for path in sentinels)
            raise TemplateSentinelError(f"runnable case contains template sentinels at: {rendered}")

    def diff_from_saved(self) -> str:
        """Return the exact unsaved document delta without changing either revision."""
        return "".join(
            difflib.unified_diff(
                self._saved_text.splitlines(keepends=True),
                self.to_text().splitlines(keepends=True),
                fromfile="saved case",
                tofile="current document",
            )
        )

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self.to_text())
        self._data = _parse_yaml(self._undo.pop())
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self.to_text())
        self._data = _parse_yaml(self._redo.pop())
        return True

    def save(self, path: str | Path | None = None) -> SavedCaseRevision:
        target = Path(path).resolve() if path is not None else self._source_path
        if target is None:
            raise CaseDocumentError("save path is required for an unsaved document")
        if (
            self._source_path == target
            and self._source_sha256 is not None
            and not target.exists()
        ):
            raise ExternalModificationError(f"case file was removed outside the workbench: {target}")
        if target.exists():
            if self._source_path != target or self._source_sha256 is None:
                raise ExternalModificationError(f"refusing to overwrite unrelated existing file: {target}")
            current_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
            if current_sha256 != self._source_sha256:
                raise ExternalModificationError(f"case file changed outside the workbench: {target}")
        saved_bytes = self.to_text().encode("utf-8")
        atomic_write_bytes(target, saved_bytes)
        saved_sha256 = hashlib.sha256(saved_bytes).hexdigest()
        self._source_path = target
        self._source_sha256 = saved_sha256
        self._saved_text = self.to_text()
        return SavedCaseRevision(target, saved_sha256, saved_bytes)

    def _commit(self, candidate: CommentedMap) -> None:
        previous = self.to_text()
        candidate_text = _dump_yaml(candidate)
        parsed = _parse_yaml(candidate_text)
        if candidate_text == previous:
            return
        self._undo.append(previous)
        self._redo.clear()
        self._data = parsed


def _yaml() -> YAML:
    yaml = YAML(typ="rt")
    yaml.allow_duplicate_keys = False
    yaml.preserve_quotes = True
    yaml.default_flow_style = False
    return yaml


def _parse_yaml(text: str) -> CommentedMap:
    if _CONFLICT_MARKER.search(text):
        raise MergeConflictError("case YAML contains unresolved merge-conflict markers")
    try:
        data = _yaml().load(text)
    except DuplicateKeyError as exc:
        raise CaseDocumentError(f"case YAML contains a duplicate key: {exc}") from exc
    except YAMLError as exc:
        raise CaseDocumentError(f"invalid case YAML: {exc}") from exc
    if not isinstance(data, CommentedMap):
        raise CaseDocumentError("case YAML root must be a mapping")
    return data


def _dump_yaml(data: CommentedMap) -> str:
    buffer = StringIO()
    _yaml().dump(data, buffer)
    return buffer.getvalue()


def _select(parent: Any, segment: YamlPathSegment) -> Any:
    if isinstance(segment, NamedListItem):
        if not isinstance(parent, (list, CommentedSeq)):
            raise CaseDocumentError("named list selector requires a YAML sequence")
        matches = [
            item
            for item in parent
            if isinstance(item, (dict, CommentedMap))
            and item.get(segment.identity_key) == segment.value
        ]
        if len(matches) != 1:
            raise CaseDocumentError(
                f"expected one list item with {segment.identity_key}={segment.value!r}, found {len(matches)}"
            )
        return matches[0]
    try:
        if isinstance(parent, (dict, CommentedMap)) and isinstance(segment, str):
            return parent[segment]
        if isinstance(parent, (list, CommentedSeq)) and isinstance(segment, int):
            return parent[segment]
    except (KeyError, IndexError) as exc:
        raise CaseDocumentError(f"YAML path segment not found: {segment!r}") from exc
    raise CaseDocumentError(f"invalid YAML path segment {segment!r} for {type(parent).__name__}")


def _assign(parent: Any, segment: YamlPathSegment, value: Any, *, create: bool) -> None:
    if isinstance(segment, NamedListItem):
        raise CaseDocumentError("named list selector cannot be the final assignment segment")
    if isinstance(parent, (dict, CommentedMap)) and isinstance(segment, str):
        if segment not in parent and not create:
            raise CaseDocumentError(f"YAML path key not found: {segment!r}")
        parent[segment] = value
        return
    if isinstance(parent, (list, CommentedSeq)) and isinstance(segment, int):
        if create:
            raise CaseDocumentError("create is not supported for sequence indices")
        try:
            parent[segment] = value
        except IndexError as exc:
            raise CaseDocumentError(f"YAML sequence index out of range: {segment}") from exc
        return
    raise CaseDocumentError(f"invalid YAML assignment segment {segment!r}")


def _find_sentinels(value: Any, path: YamlPath, found: list[YamlPath]) -> None:
    if isinstance(value, (dict, CommentedMap)):
        for key, child in value.items():
            child_path = (*path, str(key))
            if isinstance(key, str) and _TEMPLATE_SENTINEL.fullmatch(key):
                found.append(child_path)
            _find_sentinels(child, child_path, found)
    elif isinstance(value, (list, CommentedSeq)):
        for index, child in enumerate(value):
            _find_sentinels(child, (*path, index), found)
    elif isinstance(value, str) and _TEMPLATE_SENTINEL.fullmatch(value):
        found.append(path)


def _format_path(path: YamlPath) -> str:
    parts: list[str] = []
    for segment in path:
        if isinstance(segment, int):
            parts.append(f"[{segment}]")
        elif not parts:
            parts.append(segment)
        else:
            parts.append(f".{segment}")
    return "".join(parts)

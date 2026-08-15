"""Fault-tolerant parser that never discards unsupported or malformed JSONL."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Iterator

from pydantic import ValidationError

from workbench_core.schemas.protocol import (
    KNOWN_EVENT_TYPES,
    PROTOCOL_VERSION,
    ProtocolEvent,
)


class ProtocolLineStatus(str, Enum):
    EVENT = "event"
    UNSUPPORTED_VERSION = "unsupported_version"
    UNSUPPORTED_EVENT = "unsupported_event"
    INVALID_EVENT = "invalid_event"
    MALFORMED = "malformed"


@dataclass(frozen=True)
class ParsedProtocolLine:
    status: ProtocolLineStatus
    raw_line: str
    raw_record: Any = None
    event: ProtocolEvent | None = None
    error: str | None = None


def parse_protocol_line(line: str) -> ParsedProtocolLine:
    try:
        raw = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return ParsedProtocolLine(
            ProtocolLineStatus.MALFORMED,
            line,
            error=f"invalid JSON: {exc}",
        )
    if not isinstance(raw, dict):
        return ParsedProtocolLine(
            ProtocolLineStatus.MALFORMED,
            line,
            raw_record=raw,
            error="protocol line must contain a JSON object",
        )
    if raw.get("protocol_version") != PROTOCOL_VERSION:
        return ParsedProtocolLine(
            ProtocolLineStatus.UNSUPPORTED_VERSION,
            line,
            raw_record=raw,
            error=f"unsupported protocol version: {raw.get('protocol_version')!r}",
        )
    if raw.get("event_type") not in KNOWN_EVENT_TYPES:
        return ParsedProtocolLine(
            ProtocolLineStatus.UNSUPPORTED_EVENT,
            line,
            raw_record=raw,
            error=f"unsupported event type: {raw.get('event_type')!r}",
        )
    try:
        event = ProtocolEvent.model_validate_json(line)
    except ValidationError as exc:
        return ParsedProtocolLine(
            ProtocolLineStatus.INVALID_EVENT,
            line,
            raw_record=raw,
            error=str(exc),
        )
    return ParsedProtocolLine(
        ProtocolLineStatus.EVENT,
        line,
        raw_record=raw,
        event=event,
    )


def parse_protocol_lines(lines: Iterable[str]) -> Iterator[ParsedProtocolLine]:
    for line in lines:
        yield parse_protocol_line(line)

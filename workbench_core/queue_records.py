"""Validated queue/entry transitions and atomic persistence."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from workbench_core.persistence import atomic_write_json, load_json_model
from workbench_core.schemas.common import utc_now
from workbench_core.schemas.queue_record import (
    QUEUE_ENTRY_STATE_TRANSITIONS,
    QUEUE_STATE_TRANSITIONS,
    QueueEntry,
    QueueEntryState,
    QueueRecord,
    QueueState,
)


class InvalidQueueTransition(ValueError):
    pass


_UNCHANGED = object()


def transition_queue(
    record: QueueRecord,
    target_state: QueueState,
    *,
    at_utc: datetime | None = None,
) -> QueueRecord:
    if target_state not in QUEUE_STATE_TRANSITIONS[record.queue_state]:
        raise InvalidQueueTransition(
            f"invalid queue transition: {record.queue_state.value} -> {target_state.value}"
        )
    timestamp = _transition_time(record, at_utc)
    return QueueRecord.model_validate(
        {
            **record.model_dump(mode="python"),
            "queue_state": target_state,
            "updated_at_utc": timestamp,
        }
    )


def transition_queue_entry(
    record: QueueRecord,
    entry_id: str,
    target_state: QueueEntryState,
    *,
    status_reason: str | None | object = _UNCHANGED,
    at_utc: datetime | None = None,
) -> QueueRecord:
    matching = [entry for entry in record.entries if entry.entry_id == entry_id]
    if not matching:
        raise KeyError(f"unknown queue entry: {entry_id}")
    entry = matching[0]
    if target_state not in QUEUE_ENTRY_STATE_TRANSITIONS[entry.entry_state]:
        raise InvalidQueueTransition(
            f"invalid queue-entry transition: {entry.entry_state.value} -> {target_state.value}"
        )
    replacement = QueueEntry.model_validate(
        {
            **entry.model_dump(mode="python"),
            "entry_state": target_state,
            "status_reason": (
                entry.status_reason if status_reason is _UNCHANGED else status_reason
            ),
        }
    )
    entries = tuple(replacement if item.entry_id == entry_id else item for item in record.entries)
    return QueueRecord.model_validate(
        {
            **record.model_dump(mode="python"),
            "entries": entries,
            "updated_at_utc": _transition_time(record, at_utc),
        }
    )


def save_queue_record(path: str | Path, record: QueueRecord) -> None:
    atomic_write_json(path, record)


def load_queue_record(path: str | Path) -> QueueRecord:
    return load_json_model(path, QueueRecord)


def _transition_time(record: QueueRecord, at_utc: datetime | None) -> datetime:
    timestamp = at_utc or utc_now()
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("transition timestamp must be timezone-aware")
    if timestamp < record.updated_at_utc:
        raise ValueError("transition timestamp cannot move backwards")
    return timestamp

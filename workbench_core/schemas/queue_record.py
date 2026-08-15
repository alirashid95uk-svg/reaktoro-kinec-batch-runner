"""Strict queue records and explicit queue/entry state machines."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from .common import NonEmptyStr, Sha256, StrictModel


QUEUE_RECORD_SCHEMA_VERSION = "1.0"


class QueueState(str, Enum):
    CREATED = "created"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class QueueEntryState(str, Enum):
    PLANNED = "planned"
    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    PAUSE_AFTER_CURRENT_REQUESTED = "pause_after_current_requested"
    CANCEL_AFTER_CURRENT_REQUESTED = "cancel_after_current_requested"
    CANCELLED_BEFORE_START = "cancelled_before_start"
    FINISHED = "finished"


QUEUE_STATE_TRANSITIONS: dict[QueueState, frozenset[QueueState]] = {
    QueueState.CREATED: frozenset({QueueState.READY}),
    QueueState.READY: frozenset({QueueState.RUNNING, QueueState.PAUSED}),
    QueueState.RUNNING: frozenset(
        {QueueState.PAUSED, QueueState.COMPLETED, QueueState.FAILED}
    ),
    QueueState.PAUSED: frozenset(
        {QueueState.READY, QueueState.RUNNING, QueueState.COMPLETED, QueueState.FAILED}
    ),
    QueueState.COMPLETED: frozenset(),
    QueueState.FAILED: frozenset(),
}

QUEUE_ENTRY_STATE_TRANSITIONS: dict[QueueEntryState, frozenset[QueueEntryState]] = {
    QueueEntryState.PLANNED: frozenset(
        {QueueEntryState.QUEUED, QueueEntryState.CANCELLED_BEFORE_START}
    ),
    QueueEntryState.QUEUED: frozenset(
        {
            QueueEntryState.STARTING,
            QueueEntryState.PAUSE_AFTER_CURRENT_REQUESTED,
            QueueEntryState.CANCEL_AFTER_CURRENT_REQUESTED,
            QueueEntryState.CANCELLED_BEFORE_START,
        }
    ),
    QueueEntryState.STARTING: frozenset(
        {
            QueueEntryState.RUNNING,
            QueueEntryState.PAUSE_AFTER_CURRENT_REQUESTED,
            QueueEntryState.CANCEL_AFTER_CURRENT_REQUESTED,
            QueueEntryState.FINISHED,
        }
    ),
    QueueEntryState.RUNNING: frozenset(
        {
            QueueEntryState.PAUSE_AFTER_CURRENT_REQUESTED,
            QueueEntryState.CANCEL_AFTER_CURRENT_REQUESTED,
            QueueEntryState.FINISHED,
        }
    ),
    QueueEntryState.PAUSE_AFTER_CURRENT_REQUESTED: frozenset(
        {QueueEntryState.CANCEL_AFTER_CURRENT_REQUESTED, QueueEntryState.FINISHED}
    ),
    QueueEntryState.CANCEL_AFTER_CURRENT_REQUESTED: frozenset(
        {QueueEntryState.FINISHED}
    ),
    QueueEntryState.CANCELLED_BEFORE_START: frozenset(),
    QueueEntryState.FINISHED: frozenset(),
}


class WorkerPolicy(StrictModel):
    max_workers: int = Field(ge=1)


class QueueEntry(StrictModel):
    entry_id: NonEmptyStr
    order: int = Field(ge=0)
    run_id: NonEmptyStr
    snapshot_path: NonEmptyStr
    snapshot_sha256: Sha256
    scientific_fingerprint: Sha256
    validation_receipt_id: NonEmptyStr
    entry_state: QueueEntryState
    status_reason: str | None = None


class QueueRecord(StrictModel):
    queue_schema_version: Literal["1.0"]
    queue_id: NonEmptyStr
    created_at_utc: AwareDatetime
    updated_at_utc: AwareDatetime
    failure_policy: Literal[
        "stop_after_failure", "continue_after_failure", "pause_for_decision"
    ]
    worker_policy: WorkerPolicy
    queue_state: QueueState
    entries: tuple[QueueEntry, ...]

    @model_validator(mode="after")
    def require_unique_entries(self) -> "QueueRecord":
        if self.updated_at_utc < self.created_at_utc:
            raise ValueError("updated_at_utc cannot precede created_at_utc")
        for field_name in ("entry_id", "order", "run_id"):
            values = [getattr(entry, field_name) for entry in self.entries]
            if len(values) != len(set(values)):
                raise ValueError(f"queue entry {field_name} values must be unique")
        return self

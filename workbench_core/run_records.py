"""Validated run-record transitions and atomic persistence."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from workbench_core.persistence import atomic_write_json, load_json_model
from workbench_core.schemas.common import utc_now
from workbench_core.schemas.run_record import (
    RUN_STATE_TRANSITIONS,
    TERMINAL_RUN_STATES,
    RunRecord,
    RunState,
    RunTerminationCategory,
)


class InvalidRunTransition(ValueError):
    pass


_IMMUTABLE_RUN_FIELDS = frozenset(
    {
        "run_schema_version",
        "run_id",
        "case_id",
        "source_case",
        "snapshot_path",
        "snapshot_sha256",
        "scientific_fingerprint",
        "operational_fingerprint",
        "created_at_utc",
        "queue_id",
        "study_id",
        "sample_id",
        "replicate_of_run_id",
        "result_package_path",
        "state",
        "updated_at_utc",
        "started_at_utc",
        "finished_at_utc",
    }
)


def transition_run(
    record: RunRecord,
    target_state: RunState,
    *,
    at_utc: datetime | None = None,
    status_reason: str | None = None,
    updates: Mapping[str, Any] | None = None,
) -> RunRecord:
    if target_state not in RUN_STATE_TRANSITIONS[record.state]:
        raise InvalidRunTransition(f"invalid run transition: {record.state.value} -> {target_state.value}")
    timestamp = at_utc or utc_now()
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("transition timestamp must be timezone-aware")
    if timestamp < record.updated_at_utc:
        raise ValueError("transition timestamp cannot move backwards")
    changes = dict(updates or {})
    forbidden = set(_IMMUTABLE_RUN_FIELDS.intersection(changes))
    if (
        "scientific_fingerprint" in forbidden
        and record.scientific_fingerprint is None
        and target_state in {RunState.READY, RunState.BLOCKED_PREFLIGHT}
    ):
        forbidden.remove("scientific_fingerprint")
    if forbidden:
        raise ValueError(f"transition updates immutable fields: {sorted(forbidden)}")
    changes.update(
        {
            "state": target_state,
            "updated_at_utc": timestamp,
            "status_reason": status_reason,
        }
    )
    if target_state is RunState.RUNNING and record.started_at_utc is None:
        changes["started_at_utc"] = timestamp
    if target_state in TERMINAL_RUN_STATES:
        changes["finished_at_utc"] = timestamp
        changes.setdefault("termination_category", RunTerminationCategory(target_state.value))
    return RunRecord.model_validate({**record.model_dump(mode="python"), **changes})


def save_run_record(path: str | Path, record: RunRecord) -> None:
    atomic_write_json(path, record)


def load_run_record(path: str | Path) -> RunRecord:
    return load_json_model(path, RunRecord)

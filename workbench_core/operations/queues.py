"""Sequential queue creation, execution, control, and recovery."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable
from uuid import uuid4

from workbench_core.queue_records import (
    load_queue_record,
    save_queue_record,
    transition_queue,
    transition_queue_entry,
)
from workbench_core.run_records import load_run_record, save_run_record
from workbench_core.schemas.common import utc_now
from workbench_core.schemas.queue_record import (
    QueueEntry,
    QueueEntryState,
    QueueRecord,
    QueueState,
    WorkerPolicy,
)
from workbench_core.schemas.run_record import RunRecord, RunState
from workbench_core.schemas.validation_receipt import ValidationReceipt

from .execution import execute_run


def create_queue(
    run_record_paths: Iterable[str | Path],
    queue_path: str | Path,
    *,
    failure_policy: str = "stop_after_failure",
    project_root: str | Path | None = None,
) -> QueueRecord:
    """Persist an immutable-snapshot, sequential queue with duplicate evidence."""
    records = [load_run_record(path) for path in run_record_paths]
    if not records or any(record.state is not RunState.READY for record in records):
        raise ValueError("queue entries must all reference ready run records")
    queue_id = str(uuid4())
    seen = _existing_scientific_fingerprints(
        project_root, {record.run_id for record in records}
    )
    entries = []
    for order, record in enumerate(records):
        fingerprint = str(record.scientific_fingerprint)
        duplicate = seen.get(fingerprint)
        if duplicate is None:
            seen[fingerprint] = record.run_id
        elif record.replicate_of_run_id != duplicate:
            status_reason = f"duplicate scientific fingerprint of {duplicate}; not an approved replicate"
        else:
            status_reason = f"approved replicate of {duplicate}"
        entries.append(
            QueueEntry(
                entry_id=str(uuid4()),
                order=order,
                run_id=record.run_id,
                snapshot_path=record.snapshot_path,
                snapshot_sha256=record.snapshot_sha256,
                scientific_fingerprint=fingerprint,
                validation_receipt_id=ValidationReceipt.model_validate_json(
                    Path(str(record.validation_receipt_path)).read_bytes()
                ).receipt_id,
                entry_state=QueueEntryState.PLANNED,
                status_reason=(status_reason if duplicate is not None else None),
            )
        )
    now = utc_now()
    queue = QueueRecord(
        queue_schema_version="1.0",
        queue_id=queue_id,
        created_at_utc=now,
        updated_at_utc=now,
        failure_policy=failure_policy,
        worker_policy=WorkerPolicy(max_workers=1),
        queue_state=QueueState.CREATED,
        entries=tuple(entries),
    )
    queue = transition_queue(queue, QueueState.READY)
    for entry in tuple(queue.entries):
        queue = transition_queue_entry(queue, entry.entry_id, QueueEntryState.QUEUED)
    save_queue_record(queue_path, queue)
    return queue


def _existing_scientific_fingerprints(
    project_root: str | Path | None, excluded_run_ids: set[str]
) -> dict[str, str]:
    if project_root is None:
        return {}
    root = Path(project_root).resolve()
    known: dict[str, str] = {}
    # ponytail: repository-scale scan; replace with the rebuildable SQLite index only if profiling warrants it.
    for path in (root / "runs").rglob("run_record.json"):
        try:
            record = load_run_record(path)
        except (OSError, ValueError):
            continue
        if record.scientific_fingerprint and record.run_id not in excluded_run_ids:
            known.setdefault(str(record.scientific_fingerprint), record.run_id)
    from workbench_core.schemas.study_spec import StudyManifest

    for path in root.rglob("study_manifest.json"):
        try:
            manifest = StudyManifest.model_validate_json(path.read_bytes())
        except (OSError, ValueError):
            continue
        for sample in manifest.samples:
            if sample.scientific_fingerprint:
                known.setdefault(
                    str(sample.scientific_fingerprint),
                    sample.run_id or f"study sample {manifest.study_id}/{sample.sample_id}",
                )
    return known


def execute_queue(
    queue_path: str | Path,
    project_root: str | Path,
    solver_prefix: str | Path,
    *,
    conda_executable: str | Path | None = None,
) -> QueueRecord:
    """Execute the verified sequential policy; stop/pause policies remain explicit."""
    path = Path(queue_path).resolve()
    queue = load_queue_record(path)
    queue = transition_queue(queue, QueueState.RUNNING)
    save_queue_record(path, queue)
    for planned_entry in tuple(sorted(queue.entries, key=lambda item: item.order)):
        queue = load_queue_record(path)
        entry = next(item for item in queue.entries if item.entry_id == planned_entry.entry_id)
        if entry.entry_state is not QueueEntryState.QUEUED:
            continue
        queue = transition_queue_entry(queue, entry.entry_id, QueueEntryState.STARTING)
        save_queue_record(path, queue)
        record_path = Path(entry.snapshot_path).parent / "run_record.json"
        record = load_run_record(record_path)
        if record.queue_id is None:
            record = record.model_copy(update={"queue_id": queue.queue_id})
            save_run_record(record_path, record)
        queue = transition_queue_entry(queue, entry.entry_id, QueueEntryState.RUNNING)
        save_queue_record(path, queue)
        result = execute_run(
            record_path,
            project_root,
            solver_prefix,
            conda_executable=conda_executable,
        )
        queue = load_queue_record(path)
        entry = next(item for item in queue.entries if item.entry_id == entry.entry_id)
        requested_state = entry.entry_state
        queue = transition_queue_entry(queue, entry.entry_id, QueueEntryState.FINISHED)
        save_queue_record(path, queue)
        if requested_state is QueueEntryState.PAUSE_AFTER_CURRENT_REQUESTED:
            queue = transition_queue(queue, QueueState.PAUSED)
            save_queue_record(path, queue)
            return queue
        if requested_state is QueueEntryState.CANCEL_AFTER_CURRENT_REQUESTED:
            for pending in tuple(queue.entries):
                if pending.entry_state is QueueEntryState.QUEUED:
                    queue = transition_queue_entry(
                        queue, pending.entry_id, QueueEntryState.CANCELLED_BEFORE_START
                    )
            queue = transition_queue(queue, QueueState.COMPLETED)
            save_queue_record(path, queue)
            return queue
        if result.state is not RunState.COMPLETED:
            if queue.failure_policy == "stop_after_failure":
                queue = transition_queue(queue, QueueState.FAILED)
                save_queue_record(path, queue)
                return queue
            if queue.failure_policy == "pause_for_decision":
                queue = transition_queue(queue, QueueState.PAUSED)
                save_queue_record(path, queue)
                return queue
    queue = transition_queue(queue, QueueState.COMPLETED)
    save_queue_record(path, queue)
    return queue


def request_queue_pause(queue_path: str | Path) -> QueueRecord:
    """Persist pause-after-current; never claim to pause an active solver call."""
    path = Path(queue_path).resolve()
    queue = load_queue_record(path)
    active = next(
        (
            entry
            for entry in queue.entries
            if entry.entry_state in {QueueEntryState.STARTING, QueueEntryState.RUNNING}
        ),
        None,
    )
    if active is None:
        if queue.queue_state in {QueueState.READY, QueueState.RUNNING}:
            queue = transition_queue(queue, QueueState.PAUSED)
    else:
        queue = transition_queue_entry(
            queue, active.entry_id, QueueEntryState.PAUSE_AFTER_CURRENT_REQUESTED
        )
    save_queue_record(path, queue)
    return queue


def request_queue_cancel_after_current(queue_path: str | Path) -> QueueRecord:
    """Persist cancel-after-current and leave the active solver untouched."""
    path = Path(queue_path).resolve()
    queue = load_queue_record(path)
    active = next(
        (
            entry
            for entry in queue.entries
            if entry.entry_state in {QueueEntryState.STARTING, QueueEntryState.RUNNING}
        ),
        None,
    )
    if active is not None:
        queue = transition_queue_entry(
            queue, active.entry_id, QueueEntryState.CANCEL_AFTER_CURRENT_REQUESTED
        )
    else:
        for pending in tuple(queue.entries):
            if pending.entry_state is QueueEntryState.QUEUED:
                queue = transition_queue_entry(
                    queue, pending.entry_id, QueueEntryState.CANCELLED_BEFORE_START
                )
    save_queue_record(path, queue)
    return queue


def begin_external_queue_entry(queue_path: str | Path) -> tuple[QueueRecord, RunRecord | None]:
    """Persist the next Starting entry before a GUI-owned solver QProcess is launched."""
    path = Path(queue_path).resolve()
    queue = load_queue_record(path)
    if queue.queue_state in {QueueState.READY, QueueState.PAUSED}:
        queue = transition_queue(queue, QueueState.RUNNING)
    entry = next(
        (item for item in sorted(queue.entries, key=lambda item: item.order) if item.entry_state is QueueEntryState.QUEUED),
        None,
    )
    if entry is None:
        if queue.queue_state is QueueState.RUNNING:
            queue = transition_queue(queue, QueueState.COMPLETED)
            save_queue_record(path, queue)
        return queue, None
    queue = transition_queue_entry(queue, entry.entry_id, QueueEntryState.STARTING)
    record_path = Path(entry.snapshot_path).parent / "run_record.json"
    record = load_run_record(record_path)
    if record.queue_id is None:
        record = record.model_copy(update={"queue_id": queue.queue_id})
        save_run_record(record_path, record)
    save_queue_record(path, queue)
    return queue, record


def mark_external_queue_entry_running(queue_path: str | Path, run_id: str) -> QueueRecord:
    path = Path(queue_path).resolve()
    queue = load_queue_record(path)
    entry = next(item for item in queue.entries if item.run_id == run_id)
    queue = transition_queue_entry(queue, entry.entry_id, QueueEntryState.RUNNING)
    save_queue_record(path, queue)
    return queue


def finish_external_queue_entry(
    queue_path: str | Path, run_id: str, run_state: RunState
) -> QueueRecord:
    """Apply persisted after-current and failure policies after the owned process exits."""
    path = Path(queue_path).resolve()
    queue = load_queue_record(path)
    entry = next(item for item in queue.entries if item.run_id == run_id)
    requested_state = entry.entry_state
    queue = transition_queue_entry(queue, entry.entry_id, QueueEntryState.FINISHED)
    if requested_state is QueueEntryState.CANCEL_AFTER_CURRENT_REQUESTED:
        for pending in tuple(queue.entries):
            if pending.entry_state is QueueEntryState.QUEUED:
                queue = transition_queue_entry(
                    queue, pending.entry_id, QueueEntryState.CANCELLED_BEFORE_START
                )
        queue = transition_queue(queue, QueueState.COMPLETED)
    elif requested_state is QueueEntryState.PAUSE_AFTER_CURRENT_REQUESTED:
        queue = transition_queue(queue, QueueState.PAUSED)
    elif run_state is not RunState.COMPLETED and queue.failure_policy != "continue_after_failure":
        queue = transition_queue(
            queue,
            QueueState.PAUSED if queue.failure_policy == "pause_for_decision" else QueueState.FAILED,
        )
    elif not any(item.entry_state is QueueEntryState.QUEUED for item in queue.entries):
        queue = transition_queue(queue, QueueState.COMPLETED)
    save_queue_record(path, queue)
    return queue


def recover_queue_record(queue_path: str | Path) -> QueueRecord:
    """Reconcile stale active queue entries only from terminal run records."""
    path = Path(queue_path).resolve()
    queue = load_queue_record(path)
    active = next(
        (
            item
            for item in queue.entries
            if item.entry_state
            in {
                QueueEntryState.STARTING,
                QueueEntryState.RUNNING,
                QueueEntryState.PAUSE_AFTER_CURRENT_REQUESTED,
                QueueEntryState.CANCEL_AFTER_CURRENT_REQUESTED,
            }
        ),
        None,
    )
    if active is None:
        return queue
    record = load_run_record(Path(active.snapshot_path).parent / "run_record.json")
    if record.state not in {
        RunState.COMPLETED,
        RunState.PARTIAL_NUMERICAL_FAILURE,
        RunState.SOLVER_FAILURE_AT_START,
        RunState.CANCELLED_CLEANLY,
        RunState.FORCE_TERMINATED,
        RunState.NATIVE_CRASH,
        RunState.CONTROLLER_FAILURE,
        RunState.CHEMISTRY_COMPLETED_OUTPUT_INCOMPLETE,
        RunState.INTERRUPTED_BY_HOST,
        RunState.INDETERMINATE,
    }:
        return queue
    return finish_external_queue_entry(path, active.run_id, record.state)

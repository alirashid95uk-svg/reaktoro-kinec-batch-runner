"""Run evidence classification and conservative orphan recovery."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable

from workbench_core.protocol_reader import ProtocolLineStatus, parse_protocol_lines
from workbench_core.run_records import load_run_record, save_run_record, transition_run
from workbench_core.schemas.run_record import OutputCompleteness, RunRecord, RunState


def recover_orphaned_runs(
    runs_root: str | Path, *, owned_child_pids: Iterable[int] = ()
) -> list[RunRecord]:
    """Conservatively finalise active records; never attach to or kill an old PID."""
    recovered = []
    owned = set(owned_child_pids)
    active = {
        RunState.CREATED,
        RunState.VALIDATING,
        RunState.STARTING,
        RunState.RUNNING,
        RunState.CANCEL_REQUESTED_SOLVER_UNRESPONSIVE,
    }
    for path in Path(runs_root).rglob("run_record.json"):
        record = load_run_record(path)
        if record.state not in active:
            continue
        if record.child_process and record.child_process.pid in owned:
            continue
        completeness = output_completeness(Path(record.result_package_path))
        target, reason = _recovery_classification(
            record, Path(record.result_package_path), completeness
        )
        record = transition_run(
            record,
            target,
            status_reason=reason,
            updates={"output_completeness": completeness},
        )
        save_run_record(path, record)
        recovered.append(record)
    return recovered


def _recovery_classification(record, results: Path, completeness: OutputCompleteness):
    if record.state in {RunState.CREATED, RunState.VALIDATING}:
        return (
            RunState.INTERRUPTED_BY_HOST,
            "controller stopped before preflight completed; stored PID ownership was not reused",
        )
    diagnostics_path = results / "diagnostics.json"
    try:
        diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        diagnostics = {}
    try:
        manifest = json.loads((results / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        manifest = {}
    event_completion, force_confirmed = _recovery_event_evidence(
        results.parent / "events.jsonl", record
    )
    if force_confirmed:
        return RunState.FORCE_TERMINATED, "recovered verified process-tree termination evidence"
    if record.child_process and _pid_exists(record.child_process.pid):
        return (
            RunState.INDETERMINATE,
            "stored worker PID is live but is not owned by this workbench session; PID identity was not reused",
        )
    completion_evidence = [
        value
        for value in (
            diagnostics.get("simulation_completed"),
            manifest.get("run_identity", {}).get("simulation_completed"),
            event_completion,
        )
        if isinstance(value, bool)
    ]
    if len(set(completion_evidence)) > 1:
        return RunState.INDETERMINATE, "durable completion evidence disagrees across outputs and events"
    chemistry_completed = bool(completion_evidence) and all(completion_evidence)
    if chemistry_completed:
        if completeness.status == "complete":
            return RunState.COMPLETED, "recovered from complete durable output evidence"
        return (
            RunState.CHEMISTRY_COMPLETED_OUTPUT_INCOMPLETE,
            "durable diagnostics show completed chemistry but incomplete output writing",
        )
    if diagnostics.get("termination_reason") == "cancelled_cleanly":
        return RunState.CANCELLED_CLEANLY, "recovered clean-cancellation diagnostics"
    if diagnostics:
        return RunState.PARTIAL_NUMERICAL_FAILURE, "recovered partial or failed simulation diagnostics"
    return (
        RunState.INTERRUPTED_BY_HOST,
        "recovered after host interruption; stored PID ownership was not reused",
    )


def _recovery_event_evidence(path: Path, record: RunRecord) -> tuple[bool | None, bool]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None, False
    completion: bool | None = None
    force_confirmed = False
    for parsed in parse_protocol_lines(lines):
        event = parsed.event
        if (
            parsed.status is not ProtocolLineStatus.EVENT
            or event is None
            or event.run_id != record.run_id
            or event.case_id != record.case_id
        ):
            continue
        if event.event_type.value == "simulation_finished":
            value = event.payload.get("simulation_completed")
            completion = value if isinstance(value, bool) else completion
        elif event.event_type.value == "kill_confirmed":
            force_confirmed = True
    return completion, force_confirmed


def _pid_exists(pid: int) -> bool:
    if pid < 1:
        return False
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def classify_result(
    record,
    returncode,
    events,
    results,
    timed_out,
    protocol_errors,
    *,
    force_termination_verified: bool | None = None,
):
    completeness = output_completeness(results, events)
    try:
        diagnostics = json.loads((results / "diagnostics.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        diagnostics = {}
    finish = next(
        (event for event in reversed(events) if event.event_type.value == "simulation_finished"),
        None,
    )
    controller_exit = next(
        (event for event in reversed(events) if event.event_type.value == "process_exited"),
        None,
    )
    if controller_exit is not None and controller_exit.payload.get("classification") == "controller_failure":
        return RunState.CONTROLLER_FAILURE, "controller could not verify worker process-tree termination"
    if timed_out and finish is None:
        if force_termination_verified:
            return RunState.FORCE_TERMINATED, "worker exceeded timeout and its process tree was terminated"
        if force_termination_verified is False:
            return RunState.CONTROLLER_FAILURE, "worker exceeded timeout and process-tree termination was not verified"
        return RunState.INDETERMINATE, "worker exceeded timeout without verified terminal process evidence"
    if finish is not None:
        payload = finish.payload
        if payload.get("termination_reason") == "cancelled_cleanly":
            return RunState.CANCELLED_CLEANLY, "cooperative cancellation completed at a safe boundary"
        if (
            payload.get("simulation_completed") is True
            and payload.get("package_complete") is True
            and completeness.status == "complete"
        ):
            return RunState.COMPLETED, None
        if payload.get("simulation_completed") is True:
            return RunState.CHEMISTRY_COMPLETED_OUTPUT_INCOMPLETE, "chemistry completed but output package is incomplete"
        return RunState.PARTIAL_NUMERICAL_FAILURE, str(payload.get("termination_reason") or "simulation incomplete")
    if diagnostics.get("simulation_completed") is True:
        if completeness.status != "complete":
            return (
                RunState.CHEMISTRY_COMPLETED_OUTPUT_INCOMPLETE,
                "durable diagnostics show completed chemistry but incomplete output writing",
            )
        return RunState.INDETERMINATE, "complete diagnostics exist without a completion protocol event"
    if diagnostics:
        return RunState.PARTIAL_NUMERICAL_FAILURE, "partial or failed diagnostics exist without a completion event"
    if any(event.event_type.value == "simulation_started" for event in events):
        return RunState.NATIVE_CRASH, f"worker terminated after simulation start ({returncode})"
    if protocol_errors:
        return RunState.CONTROLLER_FAILURE, "; ".join(protocol_errors)
    if returncode < 0:
        return RunState.NATIVE_CRASH, f"worker exited from signal/status {returncode}"
    if returncode != 0:
        return RunState.SOLVER_FAILURE_AT_START, f"worker exited before simulation completion ({returncode})"
    return RunState.INDETERMINATE, "worker exited zero without a completion event"


def output_completeness(results: Path, events=()) -> OutputCompleteness:
    files = tuple(
        sorted(path.relative_to(results).as_posix() for path in results.rglob("*") if path.is_file())
    ) if results.is_dir() else ()
    diagnostics_path = results / "diagnostics.json"
    status = "not_written"
    missing: tuple[str, ...] = ()
    if diagnostics_path.is_file():
        try:
            diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
            evidence = diagnostics.get("output_completeness", {})
            status = evidence.get("status", "partial")
            missing = tuple(evidence.get("missing_files", ()))
        except (OSError, ValueError, TypeError):
            status = "partial"
    elif files:
        manifest_path = results / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            manifest = {}
        declared = set(manifest.get("output_files", ()))
        if manifest.get("run_identity", {}).get("simulation_completed") is True and declared == set(files):
            status = "complete"
        else:
            written_event = next(
                (event for event in reversed(tuple(events)) if event.event_type.value == "output_written"),
                None,
            )
            event_status = (
                written_event.payload.get("output_completeness", {}).get("status")
                if written_event is not None
                and isinstance(written_event.payload.get("output_completeness"), dict)
                else None
            )
            status = event_status if event_status in {"partial", "complete"} else "partial"
    if status not in {"not_written", "partial", "complete"}:
        status = "partial"
    return OutputCompleteness(status=status, files_written=files, missing_files=missing)

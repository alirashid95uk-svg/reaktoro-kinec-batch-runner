"""Headless and externally owned run execution lifecycle."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Iterable

from workbench_core.fingerprints import sha256_file
from workbench_core.persistence import append_jsonl, atomic_write_text
from workbench_core.protocol_reader import ProtocolLineStatus, parse_protocol_lines
from workbench_core.run_records import load_run_record, save_run_record, transition_run
from workbench_core.schemas.common import utc_now
from workbench_core.schemas.protocol import PROTOCOL_VERSION
from workbench_core.schemas.run_record import ProcessMetadata, RunRecord, RunState
from workbench_core.schemas.validation_receipt import ValidationReceipt
from workbench_core.validation import verify_prelaunch

from .preparation import synchronise_study_sample
from .recovery import classify_result, output_completeness


def execute_run(
    run_record_path: str | Path,
    project_root: str | Path,
    solver_prefix: str | Path,
    *,
    conda_executable: str | Path | None = None,
    timeout_s: float | None = None,
) -> RunRecord:
    """Recheck the receipt, execute one runner process, and classify saved evidence."""
    path = Path(run_record_path).resolve()
    root = Path(project_root).resolve()
    initial = load_run_record(path)
    conda = str(conda_executable or shutil.which("conda") or "conda")
    command = [
        conda,
        "run",
        "--no-capture-output",
        "-p",
        str(Path(solver_prefix).resolve()),
        "python",
        str(root / "runner.py"),
        initial.snapshot_path,
        "--events-jsonl",
        "--operation-id",
        initial.run_id,
        "--run-id",
        initial.run_id,
        "--case-id",
        initial.case_id,
        "--cancel-file",
        str(path.parent / "cancel.requested"),
    ]
    controller = ProcessMetadata(
        pid=os.getpid(),
        created_at_utc=utc_now(),
        executable=sys.executable,
        command=tuple(sys.argv),
    )
    record = _authorise_run(
        path,
        root,
        solver_prefix,
        conda_executable=conda_executable,
        controller_process=controller,
    )
    if record.state is not RunState.STARTING:
        return record
    run_dir = path.parent
    events_path = run_dir / "events.jsonl"
    log_path = run_dir / "worker_stderr.log"
    cancel_path = run_dir / "cancel.requested"
    cancel_path.unlink(missing_ok=True)
    _controller_event(events_path, record, 1, "process_created", {"command": command})
    try:
        process = subprocess.Popen(
            command,
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as error:
        record = transition_run(
            record, RunState.CONTROLLER_FAILURE, status_reason=f"worker failed to start: {error}"
        )
        save_run_record(path, record)
        return record
    child = ProcessMetadata(
        pid=process.pid,
        created_at_utc=utc_now(),
        executable=conda,
        command=tuple(command),
    )
    controller = ProcessMetadata(
        pid=os.getpid(),
        created_at_utc=utc_now(),
        executable=sys.executable,
        command=tuple(sys.argv),
    )
    record = transition_run(
        record,
        RunState.RUNNING,
        updates={"child_process": child, "controller_process": controller},
    )
    save_run_record(path, record)
    _controller_event(events_path, record, 2, "process_started", {"pid": process.pid})
    atomic_write_text(log_path, "")
    parsed_events = []
    protocol_errors = []
    worker_sequence = 0
    force_termination_verified: bool | None = None

    def read_events() -> None:
        nonlocal worker_sequence
        assert process.stdout is not None
        for line in process.stdout:
            parsed = next(parse_protocol_lines((line.rstrip("\r\n"),)))
            if parsed.status is ProtocolLineStatus.EVENT and parsed.event is not None:
                event = parsed.event
                if event.run_id != record.run_id or event.case_id != record.case_id:
                    protocol_errors.append("worker event identity disagrees with the run record")
                elif event.sequence_number <= worker_sequence:
                    protocol_errors.append("worker event sequence is not strictly increasing")
                else:
                    worker_sequence = event.sequence_number
                    parsed_events.append(event)
                    append_jsonl(events_path, event)
                    continue
            else:
                protocol_errors.append(parsed.error or parsed.status.value)
            _controller_event(
                events_path,
                record,
                1000 + len(protocol_errors),
                "protocol_error",
                {
                    "status": parsed.status.value,
                    "error": protocol_errors[-1],
                    "raw_line": parsed.raw_line,
                },
            )

    def read_log() -> None:
        assert process.stderr is not None
        with log_path.open("a", encoding="utf-8", newline="") as stream:
            for line in process.stderr:
                stream.write(line)
                stream.flush()

    readers = [threading.Thread(target=read_events), threading.Thread(target=read_log)]
    for reader in readers:
        reader.start()
    try:
        process.wait(timeout=timeout_s)
        timed_out = False
    except subprocess.TimeoutExpired:
        timed_out = True
        cancel_path.touch()
        _controller_event(events_path, record, 3, "cancel_signal_sent", {"reason": "timeout"})
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            record = transition_run(
                record,
                RunState.CANCEL_REQUESTED_SOLVER_UNRESPONSIVE,
                status_reason="cooperative cancellation did not return from the native solver",
            )
            save_run_record(path, record)
            _controller_event(events_path, record, 4, "terminate_sent", {"pid": process.pid})
            _controller_event(events_path, record, 5, "kill_sent", {"pid": process.pid})
            force_termination_verified, kill_detail = _terminate_process_tree(process)
            _controller_event(
                events_path,
                record,
                6,
                "kill_confirmed" if force_termination_verified else "kill_failed",
                {"pid": process.pid, "detail": kill_detail},
            )
            if not force_termination_verified and process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                force_termination_verified = False
                protocol_errors.append("worker remained live after termination attempts")
    for reader in readers:
        reader.join(timeout=10)
    result_state, reason = classify_result(
        record,
        process.returncode,
        parsed_events,
        Path(record.result_package_path),
        timed_out,
        protocol_errors,
        force_termination_verified=force_termination_verified,
    )
    completeness = output_completeness(Path(record.result_package_path), parsed_events)
    record = transition_run(
        record,
        result_state,
        status_reason=reason,
        updates={"output_completeness": completeness},
    )
    save_run_record(path, record)
    try:
        synchronise_study_sample(root, record)
    except Exception as error:
        _controller_event(
            events_path,
            record,
            1999,
            "study_manifest_sync_failed",
            {"error": str(error)},
        )
    _controller_event(
        events_path,
        record,
        2000,
        "process_exited",
        {"exit_code": process.returncode, "classification": result_state.value},
    )
    return record


def authorise_external_run(
    run_record_path: str | Path,
    project_root: str | Path,
    solver_prefix: str | Path,
    *,
    conda_executable: str | Path | None = None,
) -> RunRecord:
    """Bind a ready record to fresh prelaunch evidence before a GUI-owned QProcess starts."""
    return _authorise_run(
        Path(run_record_path).resolve(),
        Path(project_root).resolve(),
        solver_prefix,
        conda_executable=conda_executable,
    )


def _authorise_run(
    path: Path,
    root: Path,
    solver_prefix: str | Path,
    *,
    conda_executable: str | Path | None,
    controller_process: ProcessMetadata | None = None,
) -> RunRecord:
    record = load_run_record(path)
    if record.state is not RunState.READY:
        raise ValueError(f"only a ready run can execute; current state={record.state.value}")
    if record.validation_receipt_path is None:
        raise ValueError("ready run has no validation receipt")
    receipt = ValidationReceipt.model_validate_json(
        Path(record.validation_receipt_path).read_bytes()
    )
    evidence_path = Path(record.validation_receipt_path).parent / receipt.environment_evidence.path
    if not evidence_path.is_file() or sha256_file(evidence_path) != receipt.environment_evidence.sha256:
        record = transition_run(
            record,
            RunState.CONTROLLER_FAILURE,
            status_reason="stored solver-environment evidence is missing or changed",
        )
        save_run_record(path, record)
        return record
    recheck = verify_prelaunch(
        receipt,
        record.snapshot_path,
        record.snapshot_sha256,
        root,
        solver_prefix,
        conda_executable=conda_executable,
    )
    if not recheck["ready"]:
        record = transition_run(
            record,
            RunState.CONTROLLER_FAILURE,
            status_reason="prelaunch identity check failed: " + "; ".join(recheck["mismatches"]),
        )
    else:
        record = transition_run(
            record,
            RunState.STARTING,
            updates={"controller_process": controller_process} if controller_process else None,
        )
    save_run_record(path, record)
    return record


def mark_external_run_running(
    run_record_path: str | Path,
    *,
    child_pid: int,
    executable: str,
    command: Iterable[str],
) -> RunRecord:
    path = Path(run_record_path).resolve()
    record = load_run_record(path)
    child = ProcessMetadata(
        pid=child_pid,
        created_at_utc=utc_now(),
        executable=executable,
        command=tuple(command),
    )
    controller = ProcessMetadata(
        pid=os.getpid(),
        created_at_utc=utc_now(),
        executable=sys.executable,
        command=tuple(sys.argv),
    )
    record = transition_run(
        record,
        RunState.RUNNING,
        updates={"child_process": child, "controller_process": controller},
    )
    save_run_record(path, record)
    return record


def mark_external_run_unresponsive(run_record_path: str | Path) -> RunRecord:
    path = Path(run_record_path).resolve()
    record = load_run_record(path)
    if record.state is RunState.CANCEL_REQUESTED_SOLVER_UNRESPONSIVE:
        return record
    record = transition_run(
        record,
        RunState.CANCEL_REQUESTED_SOLVER_UNRESPONSIVE,
        status_reason="cooperative cancellation did not return from the native solver",
    )
    save_run_record(path, record)
    return record


def finalise_external_run(
    run_record_path: str | Path,
    event_path: str | Path,
    *,
    return_code: int,
    force_requested: bool = False,
) -> RunRecord:
    """Classify a GUI-owned QProcess from durable worker/controller evidence."""
    path = Path(run_record_path).resolve()
    record = load_run_record(path)
    events = []
    protocol_errors = []
    worker_sequence = 0
    try:
        lines = Path(event_path).read_text(encoding="utf-8").splitlines()
    except OSError as error:
        lines = []
        protocol_errors.append(f"event stream unavailable: {error}")
    for parsed in parse_protocol_lines(lines):
        if parsed.status is not ProtocolLineStatus.EVENT or parsed.event is None:
            protocol_errors.append(parsed.error or parsed.status.value)
            continue
        event = parsed.event
        if event.run_id != record.run_id or event.case_id != record.case_id:
            protocol_errors.append("worker event identity disagrees with the run record")
            continue
        if event.producer == "worker":
            if event.sequence_number <= worker_sequence:
                protocol_errors.append("worker event sequence is not strictly increasing")
                continue
            worker_sequence = event.sequence_number
        events.append(event)
    completeness = output_completeness(Path(record.result_package_path), events)
    kill_confirmed = any(event.event_type.value == "kill_confirmed" for event in events)
    if force_requested and kill_confirmed:
        target, reason = RunState.FORCE_TERMINATED, "process tree was force terminated by the workbench"
    elif force_requested:
        target, reason = RunState.CONTROLLER_FAILURE, "force termination was requested but not verified"
    else:
        target, reason = classify_result(
            record,
            return_code,
            events,
            Path(record.result_package_path),
            False,
            protocol_errors,
        )
    record = transition_run(
        record,
        target,
        status_reason=reason,
        updates={"output_completeness": completeness},
    )
    save_run_record(path, record)
    return record


def fail_external_run_controller(
    run_record_path: str | Path, reason: str
) -> RunRecord:
    path = Path(run_record_path).resolve()
    record = load_run_record(path)
    completeness = output_completeness(Path(record.result_package_path))
    record = transition_run(
        record,
        RunState.CONTROLLER_FAILURE,
        status_reason=reason,
        updates={"output_completeness": completeness},
    )
    save_run_record(path, record)
    return record


def _controller_event(path, record, sequence, event_type, payload):
    append_jsonl(
        path,
        {
            "protocol_version": PROTOCOL_VERSION,
            "event_type": event_type,
            "timestamp_utc": utc_now().isoformat(),
            "run_id": record.run_id,
            "case_id": record.case_id,
            "sequence_number": sequence,
            "producer": "controller",
            "payload": payload,
        },
    )


def _terminate_process_tree(process: subprocess.Popen) -> tuple[bool, str]:
    if process.poll() is not None:
        return False, "process exited before process-tree termination could be verified"
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return False, f"{type(error).__name__}: {error}"
        detail = (result.stdout or result.stderr or f"exit code {result.returncode}").strip()
        return result.returncode == 0, detail
    process.kill()
    return True, "direct process kill sent"

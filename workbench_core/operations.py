"""Headless validated run preparation, execution, queueing, and recovery."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from workbench_core.documents import CaseDocument
from workbench_core.fingerprints import operational_fingerprint, sha256_file
from workbench_core.persistence import append_jsonl, atomic_write_json, atomic_write_text
from workbench_core.protocol_reader import ProtocolLineStatus, parse_protocol_lines
from workbench_core.queue_records import (
    save_queue_record,
    transition_queue,
    transition_queue_entry,
)
from workbench_core.run_records import load_run_record, save_run_record, transition_run
from workbench_core.schemas.common import utc_now
from workbench_core.schemas.protocol import PROTOCOL_VERSION
from workbench_core.schemas.queue_record import (
    QueueEntry,
    QueueEntryState,
    QueueRecord,
    QueueState,
    WorkerPolicy,
)
from workbench_core.schemas.run_record import (
    OutputCompleteness,
    ProcessMetadata,
    RunRecord,
    RunState,
    SourceCaseIdentity,
)
from workbench_core.schemas.validation_receipt import ValidationReceipt
from workbench_core.validation import validate_case, verify_prelaunch


class ProjectControlLock:
    """One OS-released controller lock for a project; no stale PID ownership."""

    def __init__(self, project_root: str | Path):
        self.path = Path(project_root).resolve() / ".workbench" / "control.lock"
        self.token = str(uuid4())
        self._stream = None
        self._borrowed = False

    def __enter__(self) -> "ProjectControlLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        delegated = os.environ.get("REAKTORO_PROJECT_CONTROL_TOKEN")
        if delegated:
            try:
                with self.path.open("rb") as owner_stream:
                    owner_stream.seek(1)
                    owner = json.loads(owner_stream.read().decode("utf-8"))
            except (OSError, ValueError, TypeError):
                owner = {}
            if owner.get("token") == delegated:
                self.token = delegated
                self._borrowed = True
                return self
        stream = self.path.open("a+b")
        try:
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            stream.close()
            raise RuntimeError(
                f"another workbench controller owns the project lock: {self.path}"
            ) from error
        payload = json.dumps(
            {"pid": os.getpid(), "token": self.token, "created_at_utc": utc_now().isoformat()}
        ).encode("utf-8")
        stream.seek(1)
        stream.truncate()
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
        stream.seek(0)
        self._stream = stream
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        if self._borrowed:
            self._borrowed = False
            return
        if self._stream is None:
            return
        try:
            self._stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
        finally:
            self._stream.close()
            self._stream = None


def prepare_run(
    source_case: str | Path,
    project_root: str | Path,
    solver_prefix: str | Path,
    *,
    conda_executable: str | Path | None = None,
    study_id: str | None = None,
    scenario_group: str | None = None,
    sample_id: str | None = None,
    replicate_of_run_id: str | None = None,
) -> RunRecord:
    """Create the final snapshot, preflight it, and persist a ready or blocked run."""
    source = Path(source_case).resolve()
    root = Path(project_root).resolve()
    run_id = str(uuid4())
    case_id = source.stem
    run_dir = root / "runs" / _slug(case_id) / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    snapshot = run_dir / "run_case.yaml"
    document = CaseDocument.load(source)
    document.assert_runnable()
    document.patch(("paths", "output_dir"), str((run_dir / "results").resolve()))
    revision = document.save(snapshot)
    now = utc_now()
    record = RunRecord(
        run_schema_version="1.0",
        run_id=run_id,
        case_id=case_id,
        source_case=SourceCaseIdentity(path=_logical_path(source, root), sha256=sha256_file(source)),
        snapshot_path=str(snapshot),
        snapshot_sha256=revision.sha256,
        scientific_fingerprint=None,
        operational_fingerprint=operational_fingerprint(
            {
                "run_id": run_id,
                "run_directory": str(run_dir),
                "output_dir": str(run_dir / "results"),
                "study_id": study_id,
                "scenario_group": scenario_group,
            }
        ),
        state=RunState.CREATED,
        created_at_utc=now,
        updated_at_utc=now,
        study_id=study_id,
        scenario_group=scenario_group,
        sample_id=sample_id,
        replicate_of_run_id=replicate_of_run_id,
        result_package_path=str(run_dir / "results"),
        output_completeness=OutputCompleteness(status="not_written"),
    )
    record_path = run_dir / "run_record.json"
    save_run_record(record_path, record)
    record = transition_run(record, RunState.VALIDATING)
    save_run_record(record_path, record)
    try:
        receipt, receipt_path = validate_case(
            snapshot,
            root,
            solver_prefix,
            root / ".workbench" / "validations",
            conda_executable=conda_executable,
        )
        local_receipt = _copy_validation_evidence(receipt_path, run_dir)
        if receipt.validated_snapshot_sha256 != record.snapshot_sha256:
            raise ValueError("validation receipt snapshot hash does not match the final run snapshot")
        target = RunState.READY if receipt.ready else RunState.BLOCKED_PREFLIGHT
        record = transition_run(
            record,
            target,
            status_reason=None if receipt.ready else "; ".join(receipt.errors),
            updates={
                "scientific_fingerprint": receipt.scientific_fingerprint,
                "validation_receipt_path": str(local_receipt),
            },
        )
    except Exception as error:
        record = transition_run(
            record,
            RunState.CONTROLLER_FAILURE,
            status_reason=f"preflight controller failure: {error}",
        )
    save_run_record(record_path, record)
    return record


def prepare_study_sample(
    manifest_path: str | Path,
    sample_id: str,
    project_root: str | Path,
    solver_prefix: str | Path,
    *,
    conda_executable: str | Path | None = None,
    expected_case: str | Path | None = None,
    scenario_group: str | None = None,
) -> RunRecord:
    """Prepare one generated study sample with durable study/run lineage."""
    from workbench_core.schemas.study_spec import StudyManifest
    from workbench_core.studies import update_sample_status

    path = Path(manifest_path).resolve()
    manifest = StudyManifest.model_validate_json(path.read_bytes())
    sample = next((item for item in manifest.samples if item.sample_id == sample_id), None)
    if sample is None:
        raise KeyError(f"unknown study sample: {sample_id}")
    if sample.generation_outcome != "generated" or sample.validation_status != "ready":
        raise ValueError("only generated, preflight-ready study samples can be prepared")
    if sample.run_id is not None:
        raise ValueError(f"study sample already has run_id {sample.run_id}")
    if sample.case_path is None:
        raise ValueError("study sample has no generated case path")
    source_case = Path(sample.case_path)
    if not source_case.is_absolute():
        source_case = path.parent / source_case
    source_case = source_case.resolve()
    if expected_case is not None and source_case != Path(expected_case).resolve():
        raise ValueError("selected case does not match the study sample record")
    record = prepare_run(
        source_case,
        project_root,
        solver_prefix,
        conda_executable=conda_executable,
        study_id=manifest.study_id,
        scenario_group=scenario_group,
        sample_id=sample.sample_id,
    )
    update_sample_status(
        path,
        sample.sample_id,
        run_id=record.run_id,
        completion_state=record.state.value,
        qc_state="preflight_ready" if record.state is RunState.READY else "preflight_blocked",
    )
    return record


def synchronise_study_sample(project_root: str | Path, record: RunRecord) -> Path | None:
    """Project one terminal run classification back into its unique study manifest."""
    if not record.study_id or not record.sample_id:
        return None
    from workbench_core.schemas.study_spec import StudyManifest
    from workbench_core.studies import update_sample_status

    matches = []
    for path in Path(project_root).resolve().rglob("study_manifest.json"):
        try:
            manifest = StudyManifest.model_validate_json(path.read_bytes())
        except (OSError, ValueError):
            continue
        sample = next(
            (item for item in manifest.samples if item.sample_id == record.sample_id), None
        )
        if manifest.study_id == record.study_id and sample and sample.run_id == record.run_id:
            matches.append(path)
    if len(matches) != 1:
        raise ValueError(
            f"expected one study manifest for {record.study_id}/{record.sample_id}; found {len(matches)}"
        )
    complete = (
        record.state is RunState.COMPLETED
        and record.output_completeness.status == "complete"
    )
    update_sample_status(
        matches[0],
        record.sample_id,
        run_id=record.run_id,
        completion_state=record.state.value,
        qc_state="complete" if complete else "excluded_from_valid_dataset",
    )
    return matches[0]


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
    result_state, reason = _classify_result(
        record,
        process.returncode,
        parsed_events,
        Path(record.result_package_path),
        timed_out,
        protocol_errors,
        force_termination_verified=force_termination_verified,
    )
    completeness = _output_completeness(Path(record.result_package_path), parsed_events)
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
    completeness = _output_completeness(Path(record.result_package_path), events)
    kill_confirmed = any(event.event_type.value == "kill_confirmed" for event in events)
    if force_requested and kill_confirmed:
        target, reason = RunState.FORCE_TERMINATED, "process tree was force terminated by the workbench"
    elif force_requested:
        target, reason = RunState.CONTROLLER_FAILURE, "force termination was requested but not verified"
    else:
        target, reason = _classify_result(
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
    completeness = _output_completeness(Path(record.result_package_path))
    record = transition_run(
        record,
        RunState.CONTROLLER_FAILURE,
        status_reason=reason,
        updates={"output_completeness": completeness},
    )
    save_run_record(path, record)
    return record


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
    from workbench_core.queue_records import load_queue_record

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
    from workbench_core.queue_records import load_queue_record

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
    from workbench_core.queue_records import load_queue_record

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
    from workbench_core.queue_records import load_queue_record

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
    from workbench_core.queue_records import load_queue_record

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
    from workbench_core.queue_records import load_queue_record

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
    from workbench_core.queue_records import load_queue_record

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
        completeness = _output_completeness(Path(record.result_package_path))
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


def _copy_validation_evidence(receipt_path: Path, run_dir: Path) -> Path:
    destination = run_dir / "validation_receipt.json"
    for source in receipt_path.parent.iterdir():
        if source.is_file():
            shutil.copy2(source, destination if source == receipt_path else run_dir / source.name)
    return destination


def _classify_result(
    record,
    returncode,
    events,
    results,
    timed_out,
    protocol_errors,
    *,
    force_termination_verified: bool | None = None,
):
    completeness = _output_completeness(results, events)
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


def _output_completeness(results: Path, events=()) -> OutputCompleteness:
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


def _logical_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _slug(value: str) -> str:
    import re

    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.") or "case"

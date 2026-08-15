"""Non-blocking solver and preflight ownership through ``QProcess``."""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PySide6.QtCore import QElapsedTimer, QObject, QProcess, QTimer, Signal

from workbench_core.protocol_reader import ProtocolLineStatus, parse_protocol_line
from workbench_core.schemas.protocol import PROTOCOL_VERSION

from workbench.services.platform_windows import force_kill_process_tree


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _kill_tree_async(pid: int, finished_signal: Signal) -> None:
    def run() -> None:
        try:
            result = force_kill_process_tree(pid)
            detail = (result.stdout or result.stderr or f"exit code {result.returncode}").strip()
            finished_signal.emit(result.returncode == 0, detail)
        except Exception as error:
            finished_signal.emit(False, f"{type(error).__name__}: {error}")

    threading.Thread(target=run, daemon=True).start()


class ProcessController(QObject):
    """Own exactly one child process; queue-level concurrency remains sequential."""

    event_received = Signal(dict)
    protocol_problem = Signal(str)
    log_received = Signal(str)
    status_changed = Signal(str)
    started = Signal(int)
    finished = Signal(int, str)
    cancel_unresponsive = Signal()
    _tree_kill_finished = Signal(bool, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.started.connect(self._started)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._process_error)
        self._stdout = b""
        self._stderr = b""
        self._run_id = "operation"
        self._case_id = "case"
        self._sequence = 0
        self._worker_sequence = 0
        self._owned_pid: int | None = None
        self._run_dir: Path | None = None
        self._events_path: Path | None = None
        self._log_path: Path | None = None
        self._cancel_path: Path | None = None
        self._force_requested = False
        self._elapsed = QElapsedTimer()
        self._last_worker_event: dict[str, Any] | None = None
        self._completion_emitted = False
        self._preflight = False
        self._simulation_finished_payload: dict[str, Any] | None = None
        self._force_confirmed = False
        self._force_failed = False
        self._pending_exit: tuple[int, QProcess.ExitStatus] | None = None
        self._tree_kill_finished.connect(self._tree_kill_done)

    @property
    def is_active(self) -> bool:
        return self.process.state() != QProcess.ProcessState.NotRunning

    @property
    def elapsed_seconds(self) -> float:
        return self._elapsed.elapsed() / 1000 if self._elapsed.isValid() else 0.0

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def event_path(self) -> Path | None:
        return self._events_path

    @property
    def force_requested(self) -> bool:
        return self._force_requested

    @property
    def force_confirmed(self) -> bool:
        return self._force_confirmed

    @property
    def command(self) -> tuple[str, ...]:
        return (self.process.program(), *self.process.arguments())

    def launch_solver(
        self,
        *,
        project_root: str | Path,
        solver_prefix: str | Path,
        case_path: str | Path,
        run_dir: str | Path,
        run_id: str,
        case_id: str,
        preflight: bool = False,
        conda_executable: str | Path | None = None,
    ) -> None:
        root = Path(project_root).resolve()
        conda = str(conda_executable or shutil.which("conda") or "conda")
        arguments = [
            "run",
            "--no-capture-output",
            "-p",
            str(Path(solver_prefix).resolve()),
            "python",
            str(root / "runner.py"),
        ]
        if preflight:
            arguments.append("--preflight")
        arguments.extend(
            [
                str(Path(case_path).resolve()),
                "--events-jsonl",
                "--operation-id",
                run_id,
                "--run-id",
                run_id,
                "--case-id",
                case_id,
                "--cancel-file",
                str(Path(run_dir).resolve() / "cancel.requested"),
            ]
        )
        self.start_program(
            program=conda,
            arguments=arguments,
            working_directory=root,
            run_dir=run_dir,
            run_id=run_id,
            case_id=case_id,
            preflight=preflight,
        )

    def start_program(
        self,
        *,
        program: str | Path,
        arguments: list[str],
        working_directory: str | Path,
        run_dir: str | Path,
        run_id: str,
        case_id: str,
        preflight: bool = False,
    ) -> None:
        if self.is_active:
            raise RuntimeError("a worker is already active; sequential execution is the default")
        self._run_id, self._case_id = run_id, case_id
        self._preflight = preflight
        self._run_dir = Path(run_dir).resolve()
        self._run_dir.mkdir(parents=True, exist_ok=True)
        self._events_path = self._run_dir / "events.jsonl"
        self._log_path = self._run_dir / "launch_log.txt"
        self._cancel_path = self._run_dir / "cancel.requested"
        self._cancel_path.unlink(missing_ok=True)
        self._stdout = b""
        self._stderr = b""
        self._sequence = 0
        self._worker_sequence = 0
        self._owned_pid = None
        self._force_requested = False
        self._force_confirmed = False
        self._force_failed = False
        self._pending_exit = None
        self._last_worker_event = None
        self._completion_emitted = False
        self._simulation_finished_payload = None
        self.process.setWorkingDirectory(str(Path(working_directory).resolve()))
        self.process.setProgram(str(program))
        self.process.setArguments(arguments)
        self._controller_event(
            "process_created", {"program": str(program), "arguments": arguments}
        )
        self.status_changed.emit("Starting")
        self.process.start()

    def request_cancel(self, *, unresponsive_after_ms: int = 10_000) -> None:
        if not self.is_active or self._cancel_path is None:
            return
        temporary = self._cancel_path.with_suffix(".tmp")
        temporary.write_text(_utc_now(), encoding="utf-8")
        temporary.replace(self._cancel_path)
        self._controller_event("cancel_signal_sent", {"sentinel": str(self._cancel_path)})
        self.status_changed.emit("Graceful cancellation requested; waiting for a safe boundary")
        QTimer.singleShot(unresponsive_after_ms, self._mark_unresponsive)

    def force_terminate(self, *, kill_after_ms: int = 1_500) -> None:
        if not self.is_active:
            return
        self._force_requested = True
        self._controller_event("force_requested", {"pid": self._owned_pid})
        self.status_changed.emit("Force termination requested; result cannot be complete")
        if os.name == "nt":
            # Terminating only the conda/Python parent can orphan native descendants.
            QTimer.singleShot(kill_after_ms, self._kill_owned_tree)
        else:
            self._controller_event("terminate_sent", {"pid": self._owned_pid})
            self.process.terminate()
            QTimer.singleShot(kill_after_ms, self._kill_owned_tree)

    def _read_stdout(self) -> None:
        self._stdout += bytes(self.process.readAllStandardOutput())
        while b"\n" in self._stdout:
            raw, self._stdout = self._stdout.split(b"\n", 1)
            self._handle_protocol_line(raw.decode("utf-8", errors="replace"))

    def _read_stderr(self) -> None:
        data = bytes(self.process.readAllStandardError())
        if not data:
            return
        self._stderr += data
        text = data.decode("utf-8", errors="replace")
        self._append_text(self._log_path, text)
        self.log_received.emit(text)

    def _handle_protocol_line(self, line: str) -> None:
        if not line:
            return
        parsed = parse_protocol_line(line)
        if parsed.status is ProtocolLineStatus.EVENT and parsed.event is not None:
            event = parsed.event
            if event.run_id != self._run_id or event.case_id != self._case_id:
                self._controller_event(
                    "protocol_error",
                    {"status": "identity_mismatch", "error": "worker event identity disagrees with the active run", "raw_line": line},
                )
                self.protocol_problem.emit("worker event identity disagrees with the active run")
                return
            if event.sequence_number <= self._worker_sequence:
                self._controller_event(
                    "protocol_error",
                    {"status": "sequence_error", "error": "worker event sequence is not strictly increasing", "raw_line": line},
                )
                self.protocol_problem.emit("worker event sequence is not strictly increasing")
                return
            self._worker_sequence = event.sequence_number
            record = event.model_dump(mode="json")
            self._append_json(record)
            self._last_worker_event = record
            if record.get("event_type") == "simulation_finished":
                self._simulation_finished_payload = record.get("payload", {})
            self.event_received.emit(record)
            return
        if parsed.status in {
            ProtocolLineStatus.UNSUPPORTED_EVENT,
            ProtocolLineStatus.UNSUPPORTED_VERSION,
        }:
            self._append_text(self._events_path, line + "\n")
        self._controller_event(
            "protocol_error",
            {"status": parsed.status.value, "error": parsed.error or "", "raw_line": line},
        )
        self.protocol_problem.emit(parsed.error or parsed.status.value)

    def _started(self) -> None:
        self._elapsed.start()
        self._owned_pid = int(self.process.processId())
        self._controller_event("process_started", {"pid": self._owned_pid})
        self.status_changed.emit("Running")
        self.started.emit(self._owned_pid)

    def _finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        if self._completion_emitted:
            return
        self._drain_buffers()
        if self._force_requested and not (self._force_confirmed or self._force_failed):
            self._pending_exit = (exit_code, exit_status)
            self.status_changed.emit("Process exited; verifying Windows process-tree termination")
            return
        self._complete_exit(exit_code, exit_status)

    def _complete_exit(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        classification = self._classify(exit_code, exit_status)
        self._controller_event(
            "process_exited",
            {
                "exit_code": exit_code,
                "exit_status": exit_status.name,
                "classification": classification,
                "pid": self._owned_pid,
            },
        )
        self.status_changed.emit(classification.replace("_", " ").title())
        self._owned_pid = None
        self._completion_emitted = True
        self.finished.emit(exit_code, classification)

    def _process_error(self, error: QProcess.ProcessError) -> None:
        self._controller_event(
            "controller_error", {"process_error": error.name, "detail": self.process.errorString()}
        )
        self.status_changed.emit(f"Controller error: {self.process.errorString()}")
        if error is QProcess.ProcessError.FailedToStart and not self._completion_emitted:
            self._controller_event(
                "process_exited",
                {
                    "exit_code": None,
                    "exit_status": "FailedToStart",
                    "classification": "controller_failure",
                    "pid": self._owned_pid,
                },
            )
            self._completion_emitted = True
            self.finished.emit(-1, "controller_failure")

    def _mark_unresponsive(self) -> None:
        if self.is_active and self._cancel_path is not None and self._cancel_path.exists():
            self._controller_event(
                "cancel_unresponsive",
                {"detail": "solver has not returned to a verified safe boundary"},
            )
            self.status_changed.emit("Cancel requested; solver has not returned to a safe boundary")
            self.cancel_unresponsive.emit()

    def _kill_owned_tree(self) -> None:
        if not self.is_active:
            self._tree_kill_done(False, "process exited before process-tree termination could be verified")
            return
        current_pid = int(self.process.processId())
        if self._owned_pid is None or current_pid != self._owned_pid:
            self._controller_event(
                "controller_error", {"detail": "refused process-tree kill: PID ownership changed"}
            )
            self._tree_kill_done(False, "PID ownership changed")
            return
        self._controller_event("kill_sent", {"pid": current_pid})
        if os.name == "nt":
            _kill_tree_async(current_pid, self._tree_kill_finished)
        else:
            self._force_confirmed = True
            self._controller_event("kill_confirmed", {"pid": current_pid, "detail": "direct kill sent"})
            self.process.kill()

    def _tree_kill_done(self, succeeded: bool, detail: str) -> None:
        self._force_confirmed = succeeded
        self._force_failed = not succeeded
        self._controller_event(
            "kill_confirmed" if succeeded else "kill_failed",
            {"pid": self._owned_pid, "detail": detail},
        )
        if not succeeded and self.is_active:
            self.process.kill()
        pending, self._pending_exit = self._pending_exit, None
        if pending is not None:
            self._complete_exit(*pending)

    def _kill_if_still_active(self) -> None:
        if self.is_active:
            self.process.kill()

    def _drain_buffers(self) -> None:
        self._read_stdout()
        self._read_stderr()
        if self._stdout:
            line = self._stdout.decode("utf-8", errors="replace")
            self._stdout = b""
            self._handle_protocol_line(line)

    def _classify(self, exit_code: int, exit_status: QProcess.ExitStatus) -> str:
        if self._force_confirmed:
            return "force_terminated"
        if self._force_requested:
            return "controller_failure"
        if self._preflight and exit_code == 0:
            return "preflight_ready"
        if self._simulation_finished_payload is not None:
            payload = self._simulation_finished_payload
            reason = payload.get("termination_reason")
            if reason == "cancelled_cleanly":
                return "cancelled_cleanly"
            if payload.get("simulation_completed") and payload.get("package_complete"):
                return "completed"
            if payload.get("simulation_completed"):
                return "chemistry_completed_output_incomplete"
            return "partial_numerical_failure"
        if exit_status is QProcess.ExitStatus.CrashExit:
            return "native_crash"
        if exit_code == 0:
            return "indeterminate"
        return "controller_failure" if self._last_worker_event is None else "solver_failure_at_start"

    def _controller_event(self, event_type: str, payload: dict[str, Any]) -> None:
        self._sequence += 1
        record = {
            "protocol_version": PROTOCOL_VERSION,
            "event_type": event_type,
            "timestamp_utc": _utc_now(),
            "run_id": self._run_id,
            "case_id": self._case_id,
            "sequence_number": self._sequence,
            "producer": "controller",
            "payload": payload,
        }
        self._append_json(record)
        self.event_received.emit(record)

    def _append_json(self, value: dict[str, Any]) -> None:
        self._append_text(self._events_path, json.dumps(value, separators=(",", ":")) + "\n")

    @staticmethod
    def _append_text(path: Path | None, text: str) -> None:
        if path is None:
            return
        with path.open("a", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()


class HeadlessTaskController(QObject):
    """Run one workbench CLI operation without blocking the GUI thread."""

    succeeded = Signal(str, object)
    failed = Signal(str, str)
    log_received = Signal(str)
    event_received = Signal(dict)
    status_changed = Signal(str)
    _tree_kill_finished = Signal(bool, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.started.connect(self._started)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)
        self._poller = QTimer(self)
        self._poller.setInterval(250)
        self._poller.timeout.connect(self._tail_events)
        self._stdout = b""
        self._stderr = b""
        self._operation = "task"
        self._event_file: Path | None = None
        self._event_offset = 0
        self._queue_path: Path | None = None
        self._owned_pid: int | None = None
        self._force_requested = False
        self._force_confirmed = False
        self._force_failed = False
        self._pending_finish: tuple[int, QProcess.ExitStatus] | None = None
        self._elapsed = QElapsedTimer()
        self._tree_kill_finished.connect(self._tree_kill_done)

    @property
    def is_active(self) -> bool:
        return self.process.state() != QProcess.ProcessState.NotRunning

    @property
    def elapsed_seconds(self) -> float:
        return self._elapsed.elapsed() / 1000 if self._elapsed.isValid() else 0.0

    @property
    def can_request_cancel(self) -> bool:
        return self.is_active and self._current_cancel_file() is not None

    def start(
        self,
        operation: str,
        project_root: str | Path,
        arguments: list[str],
        *,
        event_file: str | Path | None = None,
        queue_path: str | Path | None = None,
    ) -> None:
        if self.is_active:
            raise RuntimeError("another headless workbench operation is active")
        root = Path(project_root).resolve()
        self._operation = operation
        self._stdout = self._stderr = b""
        self._event_file = Path(event_file).resolve() if event_file else None
        self._event_offset = self._event_file.stat().st_size if self._event_file and self._event_file.exists() else 0
        self._queue_path = Path(queue_path).resolve() if queue_path else None
        self._owned_pid = None
        self._force_requested = False
        self._force_confirmed = False
        self._force_failed = False
        self._pending_finish = None
        self.process.setProgram(sys.executable)
        self.process.setArguments(
            [str(root / "workbench_cli.py"), "--project-root", str(root), *arguments]
        )
        self.process.setWorkingDirectory(str(root))
        self.status_changed.emit(operation.replace("_", " ").title())
        self.process.start()
        self._poller.start()

    def _started(self) -> None:
        self._owned_pid = int(self.process.processId())
        self._elapsed.start()

    def request_cancel(self) -> None:
        cancel = self._current_cancel_file()
        if self.is_active and cancel is not None:
            cancel.parent.mkdir(parents=True, exist_ok=True)
            temporary = cancel.with_suffix(".tmp")
            temporary.write_text(_utc_now(), encoding="utf-8")
            temporary.replace(cancel)
            self.status_changed.emit("Graceful cancellation requested; waiting for a safe boundary")

    def force_terminate(self) -> None:
        if not self.is_active:
            return
        self._force_requested = True
        pid = int(self.process.processId())
        if self._owned_pid is None:
            self._owned_pid = pid
        if pid != self._owned_pid:
            self.failed.emit(self._operation, "refused force termination: PID ownership changed")
            return
        if os.name == "nt":
            _kill_tree_async(pid, self._tree_kill_finished)
        else:
            self._force_confirmed = True
            self.process.kill()

    def _read_stdout(self) -> None:
        self._stdout += bytes(self.process.readAllStandardOutput())

    def _read_stderr(self) -> None:
        data = bytes(self.process.readAllStandardError())
        self._stderr += data
        if data:
            self.log_received.emit(data.decode("utf-8", errors="replace"))

    def _finished(self, code: int, _status: QProcess.ExitStatus) -> None:
        self._read_stdout()
        self._read_stderr()
        self._tail_events()
        self._poller.stop()
        if self._force_requested and not (self._force_confirmed or self._force_failed):
            self._pending_finish = (code, _status)
            return
        self._complete_finish(code)

    def _complete_finish(self, code: int) -> None:
        if self._force_requested:
            detail = "force_terminated" if self._force_confirmed else "process-tree termination could not be verified"
            self.failed.emit(self._operation, detail)
            return
        if code != 0:
            detail = self._stderr.decode("utf-8", errors="replace").strip() or f"exit code {code}"
            self.failed.emit(self._operation, detail)
            return
        try:
            result = json.loads(self._stdout.decode("utf-8"))
        except ValueError as error:
            self.failed.emit(self._operation, f"invalid headless response: {error}")
            return
        self.succeeded.emit(self._operation, result)

    def _tree_kill_done(self, succeeded: bool, detail: str) -> None:
        self._force_confirmed = succeeded
        self._force_failed = not succeeded
        self.status_changed.emit(
            "Process-tree termination confirmed" if succeeded else f"Process-tree termination failed: {detail}"
        )
        if not succeeded and self.is_active:
            self.process.kill()
        pending, self._pending_finish = self._pending_finish, None
        if pending is not None:
            self._complete_finish(pending[0])

    def _error(self, error: QProcess.ProcessError) -> None:
        if error is QProcess.ProcessError.FailedToStart:
            self._poller.stop()
            self.failed.emit(self._operation, self.process.errorString())

    def _tail_events(self) -> None:
        candidate = self._queue_event_file() if self._queue_path else self._event_file
        if candidate is None or not candidate.exists():
            return
        if candidate != self._event_file:
            self._event_file, self._event_offset = candidate, 0
        with candidate.open("rb") as stream:
            stream.seek(self._event_offset)
            data = stream.read()
            self._event_offset = stream.tell()
        for line in data.decode("utf-8", errors="replace").splitlines():
            parsed = parse_protocol_line(line)
            if parsed.event is not None:
                self.event_received.emit(parsed.event.model_dump(mode="json"))

    def _queue_event_file(self) -> Path | None:
        if self._queue_path is None or not self._queue_path.is_file():
            return None
        try:
            queue = json.loads(self._queue_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        entries = queue.get("entries", [])
        active = next(
            (
                entry
                for entry in entries
                if entry.get("entry_state")
                in {"starting", "running", "pause_after_current_requested", "cancel_after_current_requested"}
            ),
            None,
        )
        # Both supported controllers persist the authoritative stream as events.jsonl.
        return Path(active["snapshot_path"]).parent / "events.jsonl" if active else self._event_file

    def _current_cancel_file(self) -> Path | None:
        event_file = self._queue_event_file() if self._queue_path else self._event_file
        return event_file.parent / "cancel.requested" if event_file else None

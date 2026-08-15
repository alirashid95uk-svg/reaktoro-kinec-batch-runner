from __future__ import annotations

import io
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from batch_runner.protocol_events import PROTOCOL_VERSION, ProtocolEmitter


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNNER = PROJECT_ROOT / "runner.py"
KINEC_CASE = PROJECT_ROOT / "cases" / "source_supported_kinetic_case.yaml"


class _FlushCountingStream(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.flush_count = 0

    def flush(self) -> None:
        self.flush_count += 1
        super().flush()


def test_protocol_envelope_sequence_and_flush() -> None:
    stream = _FlushCountingStream()
    emitter = ProtocolEmitter(
        enabled=True,
        run_id="run-1",
        case_id="case-1",
        stream=stream,
    )

    emitter.emit("worker_ready", {"operation_id": "operation-1"})
    emitter.emit("stage_started", {"stage": "configuration_validation"})

    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [event["sequence_number"] for event in events] == [1, 2]
    assert stream.flush_count == 2
    assert all(event["protocol_version"] == PROTOCOL_VERSION for event in events)
    assert all(event["producer"] == "worker" for event in events)
    assert all(event["run_id"] == "run-1" for event in events)
    assert all(event["case_id"] == "case-1" for event in events)
    assert all(datetime.fromisoformat(event["timestamp_utc"]).tzinfo for event in events)


def test_legacy_preflight_stdout_and_exit_code_remain_compatible(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, str(RUNNER), "--preflight", str(tmp_path / "missing.yaml")],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 2
    assert completed.stdout.startswith("PREFLIGHT_RESULT:")
    assert '"ready":false' in completed.stdout


def test_event_mode_stdout_contains_jsonl_protocol_only(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--preflight",
            str(tmp_path / "missing.yaml"),
            "--events-jsonl",
            "--operation-id",
            "operation-1",
            "--run-id",
            "run-1",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    events = [json.loads(line) for line in completed.stdout.splitlines()]
    assert completed.returncode == 2
    assert events
    assert [event["sequence_number"] for event in events] == list(range(1, len(events) + 1))
    assert all(event["run_id"] == "run-1" for event in events)
    assert events[0]["event_type"] == "worker_ready"
    assert events[-1]["event_type"] == "worker_failure_reported"
    assert "PREFLIGHT_RESULT:" not in completed.stdout
    assert "PREFLIGHT_RESULT:" in completed.stderr


def test_cancel_file_stops_before_configuration_at_clean_boundary(tmp_path: Path) -> None:
    cancel_file = tmp_path / "cancel.requested"
    cancel_file.touch()
    completed = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            str(tmp_path / "not-read.yaml"),
            "--events-jsonl",
            "--run-id",
            "cancelled-run",
            "--cancel-file",
            str(cancel_file),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    events = [json.loads(line) for line in completed.stdout.splitlines()]
    assert completed.returncode == 1
    assert [event["event_type"] for event in events] == [
        "worker_ready",
        "simulation_finished",
    ]
    assert events[-1]["payload"]["termination_reason"] == "cancelled_cleanly"
    assert events[-1]["payload"]["cancellation_boundary"] == (
        "before_configuration_validation"
    )


def test_kinec_preflight_flushes_final_event_before_immediate_exit() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--preflight",
            str(KINEC_CASE),
            "--events-jsonl",
            "--run-id",
            "kinec-preflight",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    events = [json.loads(line) for line in completed.stdout.splitlines()]
    assert completed.returncode == 0, completed.stderr
    assert events[-1]["event_type"] == "stage_completed"
    assert events[-1]["payload"]["stage"] == "preflight"
    assert events[-1]["payload"]["ready"] is True
    assert events[-1]["payload"]["database_sha256"]
    assert events[-1]["payload"]["kinetic_parameter_sha256"]
    assert any(event["event_type"] == "mapping_result" for event in events)

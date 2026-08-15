from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtTest import QSignalSpy

from workbench.controllers.processes import HeadlessTaskController, ProcessController


def _event(event_type: str, sequence: int, payload: dict | None = None) -> str:
    return json.dumps(
        {
            "protocol_version": "1.0",
            "event_type": event_type,
            "timestamp_utc": "2026-08-05T12:00:00+00:00",
            "run_id": "run-1",
            "case_id": "case-1",
            "sequence_number": sequence,
            "producer": "worker",
            "payload": payload or {},
        }
    )


def _start(controller: ProcessController, script: Path, run_dir: Path, arguments: list[str] | None = None) -> None:
    controller.start_program(
        program=sys.executable,
        arguments=[str(script), *(arguments or [])],
        working_directory=script.parent,
        run_dir=run_dir,
        run_id="run-1",
        case_id="case-1",
    )


def test_qprocess_keeps_protocol_and_human_logs_separate(qtbot, tmp_path: Path) -> None:
    script = tmp_path / "worker.py"
    script.write_text(
        "import sys,time\n"
        f"line={_event('worker_ready', 1)!r}\n"
        "sys.stdout.write(line[:20]);sys.stdout.flush();time.sleep(.03)\n"
        "sys.stdout.write(line[20:]+'\\n');sys.stdout.flush()\n"
        "sys.stderr.write('human diagnostic\\n');sys.stderr.flush()\n",
        encoding="utf-8",
    )
    controller = ProcessController()
    events = QSignalSpy(controller.event_received)
    finished = QSignalSpy(controller.finished)
    _start(controller, script, tmp_path / "run")
    qtbot.waitUntil(lambda: finished.count() > 0, timeout=5000)

    records = [json.loads(line) for line in (tmp_path / "run/events.jsonl").read_text().splitlines()]
    assert any(record["event_type"] == "worker_ready" for record in records)
    assert records[-1]["event_type"] == "process_exited"
    assert (tmp_path / "run/launch_log.txt").read_text() == "human diagnostic\n"
    assert events.count() >= 4  # created, started, worker event, exited


def test_solver_launch_passes_stable_case_identity(monkeypatch, tmp_path: Path) -> None:
    controller = ProcessController()
    captured = {}
    monkeypatch.setattr(controller, "start_program", lambda **values: captured.update(values))
    controller.launch_solver(
        project_root=tmp_path,
        solver_prefix=tmp_path / "solver",
        case_path=tmp_path / "snapshot-name.yaml",
        run_dir=tmp_path / "run",
        run_id="run-1",
        case_id="stable-source-case-id",
        conda_executable=tmp_path / "conda.exe",
    )
    arguments = captured["arguments"]
    assert arguments[arguments.index("--case-id") + 1] == "stable-source-case-id"
    assert captured["case_id"] == "stable-source-case-id"


def test_malformed_jsonl_and_worker_failure_are_conservative(qtbot, tmp_path: Path) -> None:
    script = tmp_path / "bad_worker.py"
    script.write_text(
        "import os,sys\n"
        "sys.stdout.write('{bad json}\\n');sys.stdout.flush()\n"
        "os._exit(7)\n",
        encoding="utf-8",
    )
    controller = ProcessController()
    problems = QSignalSpy(controller.protocol_problem)
    finished = QSignalSpy(controller.finished)
    _start(controller, script, tmp_path / "run")
    qtbot.waitUntil(lambda: finished.count() > 0, timeout=5000)
    assert problems.count() == 1
    assert finished.at(0)[1] in {"controller_failure", "native_crash"}
    records = [json.loads(line) for line in (tmp_path / "run/events.jsonl").read_text().splitlines()]
    assert any(record["event_type"] == "protocol_error" for record in records)
    assert records[-1]["payload"]["classification"] != "completed"


def test_cooperative_cancellation_uses_sentinel_and_classifies_cleanly(qtbot, tmp_path: Path) -> None:
    cancel = tmp_path / "run" / "cancel.requested"
    script = tmp_path / "cancel_worker.py"
    script.write_text(
        "import json,pathlib,sys,time\n"
        "cancel=pathlib.Path(sys.argv[1])\n"
        "base={'protocol_version':'1.0','timestamp_utc':'2026-08-05T12:00:00+00:00','run_id':'run-1','case_id':'case-1','producer':'worker'}\n"
        "print(json.dumps(base|{'event_type':'worker_ready','sequence_number':1,'payload':{}}),flush=True)\n"
        "while not cancel.exists(): time.sleep(.02)\n"
        "print(json.dumps(base|{'event_type':'simulation_finished','sequence_number':2,'payload':{'termination_reason':'cancelled_cleanly'}}),flush=True)\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    controller = ProcessController()
    started = QSignalSpy(controller.started)
    finished = QSignalSpy(controller.finished)
    _start(controller, script, tmp_path / "run", [str(cancel)])
    qtbot.waitUntil(lambda: started.count() > 0, timeout=5000)
    controller.request_cancel(unresponsive_after_ms=1000)
    qtbot.waitUntil(lambda: finished.count() > 0, timeout=5000)
    assert cancel.exists()
    assert finished.at(0)[1] == "cancelled_cleanly"
    records = [json.loads(line) for line in (tmp_path / "run/events.jsonl").read_text().splitlines()]
    assert any(record["event_type"] == "cancel_signal_sent" for record in records)


def test_force_termination_records_intent_and_never_completes(qtbot, tmp_path: Path) -> None:
    child_pid_file = tmp_path / "child.pid"
    script = tmp_path / "sleep_worker.py"
    script.write_text(
        "import pathlib,subprocess,sys,time\n"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\n"
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid),encoding='utf-8')\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    controller = ProcessController()
    started = QSignalSpy(controller.started)
    finished = QSignalSpy(controller.finished)
    _start(controller, script, tmp_path / "run", [str(child_pid_file)])
    qtbot.waitUntil(lambda: started.count() > 0, timeout=5000)
    qtbot.waitUntil(child_pid_file.exists, timeout=5000)
    child_pid = int(child_pid_file.read_text(encoding="utf-8"))
    controller.force_terminate(kill_after_ms=50)
    qtbot.waitUntil(lambda: finished.count() > 0, timeout=5000)
    assert finished.at(0)[1] == "force_terminated"
    records = [json.loads(line) for line in (tmp_path / "run/events.jsonl").read_text().splitlines()]
    assert any(record["event_type"] == "force_requested" for record in records)
    assert any(record["event_type"] == "kill_sent" for record in records)
    assert any(record["event_type"] == "kill_confirmed" for record in records)
    assert records[-1]["payload"]["classification"] == "force_terminated"
    if os.name == "nt":
        import ctypes

        def child_still_exists() -> bool:
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, child_pid)
            if not handle:
                return False
            exit_code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            ctypes.windll.kernel32.CloseHandle(handle)
            return exit_code.value == 259

        qtbot.waitUntil(lambda: not child_still_exists(), timeout=5000)


def test_failed_tree_kill_is_not_classified_as_force_terminated(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    script = tmp_path / "sleep_worker.py"
    script.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    monkeypatch.setattr(
        "workbench.controllers.processes.force_kill_process_tree",
        lambda pid: subprocess.CompletedProcess(["taskkill", str(pid)], 1, "", "denied"),
    )
    controller = ProcessController()
    started = QSignalSpy(controller.started)
    finished = QSignalSpy(controller.finished)
    _start(controller, script, tmp_path / "run")
    qtbot.waitUntil(lambda: started.count() > 0, timeout=5000)
    controller.force_terminate(kill_after_ms=10)
    qtbot.waitUntil(lambda: finished.count() > 0, timeout=5000)

    assert finished.at(0)[1] == "controller_failure"
    records = [json.loads(line) for line in (tmp_path / "run/events.jsonl").read_text().splitlines()]
    assert any(record["event_type"] == "kill_failed" for record in records)
    assert records[-1]["payload"]["classification"] == "controller_failure"


def test_failed_start_is_finalised_as_controller_failure(qtbot, tmp_path: Path) -> None:
    controller = ProcessController()
    finished = QSignalSpy(controller.finished)
    controller.start_program(
        program=tmp_path / "missing-program.exe",
        arguments=[],
        working_directory=tmp_path,
        run_dir=tmp_path / "run",
        run_id="run-1",
        case_id="case-1",
    )
    qtbot.waitUntil(lambda: finished.count() > 0, timeout=5000)
    assert finished.at(0)[1] == "controller_failure"
    records = [json.loads(line) for line in (tmp_path / "run/events.jsonl").read_text().splitlines()]
    assert records[-1]["event_type"] == "process_exited"
    assert records[-1]["payload"]["classification"] == "controller_failure"


def test_failure_event_after_simulation_finished_does_not_erase_partial_evidence(qtbot, tmp_path: Path) -> None:
    script = tmp_path / "partial_worker.py"
    script.write_text(
        "import sys\n"
        f"print({_event('simulation_finished', 1, {'simulation_completed': False, 'package_complete': True, 'termination_reason': 'solver_failure'})!r},flush=True)\n"
        f"print({_event('worker_failure_reported', 2, {'failed_stage': 'kinetics'})!r},flush=True)\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    controller = ProcessController()
    finished = QSignalSpy(controller.finished)
    _start(controller, script, tmp_path / "run")
    qtbot.waitUntil(lambda: finished.count() > 0, timeout=5000)
    assert finished.at(0)[1] == "partial_numerical_failure"


def test_headless_cli_task_is_nonblocking_and_tails_worker_events(qtbot, tmp_path: Path) -> None:
    event_path = tmp_path / "events.jsonl"
    cli = tmp_path / "workbench_cli.py"
    cli.write_text(
        "import json,pathlib,sys,time\n"
        f"path=pathlib.Path({str(event_path)!r})\n"
        f"path.write_text({_event('progress_summary', 1, {'accepted_time_s': 1.0})!r}+'\\n',encoding='utf-8')\n"
        "time.sleep(.4)\n"
        "print(json.dumps({'ok':True,'arguments':sys.argv[1:]}))\n",
        encoding="utf-8",
    )
    controller = HeadlessTaskController()
    succeeded = QSignalSpy(controller.succeeded)
    events = QSignalSpy(controller.event_received)
    controller.start("fake_task", tmp_path, ["anything"], event_file=event_path)

    qtbot.waitUntil(lambda: succeeded.count() > 0, timeout=5000)
    assert succeeded.at(0)[0] == "fake_task"
    assert succeeded.at(0)[1]["ok"] is True
    assert events.count() == 1
    assert events.at(0)[0]["event_type"] == "progress_summary"


def test_headless_queue_task_tails_authoritative_run_event_stream(qtbot, tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "case-1" / "run-1"
    run_dir.mkdir(parents=True)
    snapshot_path = run_dir / "run_record.json"
    snapshot_path.write_text("{}", encoding="utf-8")
    queue_path = tmp_path / "queue.json"
    queue_path.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "entry_state": "running",
                        "snapshot_path": str(snapshot_path),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    event_path = run_dir / "events.jsonl"
    cli = tmp_path / "workbench_cli.py"
    cli.write_text(
        "import json,pathlib,time\n"
        f"path=pathlib.Path({str(event_path)!r})\n"
        f"path.write_text({_event('progress_summary', 1, {'accepted_time_s': 2.0})!r}+'\\n',encoding='utf-8')\n"
        "time.sleep(.4)\n"
        "print(json.dumps({'ok':True}))\n",
        encoding="utf-8",
    )
    controller = HeadlessTaskController()
    succeeded = QSignalSpy(controller.succeeded)
    events = QSignalSpy(controller.event_received)
    controller.start("queue_run", tmp_path, [], queue_path=queue_path)

    qtbot.waitUntil(lambda: succeeded.count() > 0, timeout=5000)
    assert events.count() == 1
    assert events.at(0)[0]["event_type"] == "progress_summary"

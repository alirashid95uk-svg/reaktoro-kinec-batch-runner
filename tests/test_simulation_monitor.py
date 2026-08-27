from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import reaktoro as rkt
import yaml

from batch_runner.config import load_case
from batch_runner.monitor import SimulationMonitor
from batch_runner.simulator import run_simulation


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_CASE = (
    PROJECT_ROOT / "tests" / "fixtures" / "cases" / "synthetic_kinec_case.yaml"
)
RUNNER = PROJECT_ROOT / "runner.py"


class _Clock:
    value = 0.0

    def __call__(self) -> float:
        return self.value


class _TtyStream(io.StringIO):
    def isatty(self) -> bool:
        return True


def _case(
    tmp_path: Path,
    name: str,
    monitor: dict | None = None,
    *,
    duration_s: float = 100.0,
    native_kinetics: bool = False,
):
    raw = yaml.safe_load(SOURCE_CASE.read_text(encoding="utf-8"))
    raw["paths"]["output_dir"] = str(tmp_path / f"output-{name}")
    raw["solver"]["timestep"]["time"] = {
        "duration_value": duration_s,
        "duration_unit": "seconds",
    }
    raw["solver"]["timestep"]["step_size"] = {
        "dt": {"value": 10.0, "unit": "seconds"}
    }
    if native_kinetics:
        raw["kinetics"] = {"enabled": True, "model": "palandri_kharaka"}
    if monitor is not None:
        raw["monitor"] = monitor
    path = tmp_path / f"{name}.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return load_case(path)


def _progress(
    accepted_time_s: float,
    *,
    accepted: bool = True,
    accepted_attempts: int = 1,
    rejected_attempts: int = 0,
    current_dt_s: float = 10.0,
    next_dt_s: float | None = None,
) -> dict:
    return {
        "accepted_time_s": accepted_time_s,
        "requested_duration_s": 100.0,
        "current_dt_s": current_dt_s,
        "next_dt_s": next_dt_s,
        "accepted_attempts": accepted_attempts,
        "rejected_attempts": rejected_attempts,
        "latest_accepted": accepted,
        "solver_succeeded": accepted,
        "latest_reason": None if accepted else "Reaktoro solve returned failure",
        "solver_iterations": 4,
        "stage": "kinetic_step",
    }


def _result(*, completed: bool, final_time_s: float, warning: str | None = None):
    return SimpleNamespace(
        diagnostics={
            "simulation_completed": completed,
            "output_completeness": {"status": "complete" if completed else "partial"},
            "final_time_reached_s": final_time_s,
            "number_of_accepted_steps": int(final_time_s / 10),
            "number_of_rejected_steps": 1 if not completed else 0,
            "failed_stage": None if completed else "solver_execution",
            "error_message": None if completed else "retry limit exhausted",
            "warnings": [warning] if warning else [],
            "cancellation_requested": False,
        }
    )


def test_progress_retry_recovery_eta_and_non_tty_output(tmp_path: Path) -> None:
    case = _case(tmp_path, "progress")
    stream = io.StringIO()
    clock = _Clock()
    monitor = SimulationMonitor(case, display_enabled=True, stream=stream, clock=clock)
    monitor.start(python_version="3.11", reaktoro_version="2.13.0")

    assert monitor.progress_percent == 0.0
    assert monitor.eta_s is None

    clock.value = 1.0
    monitor.handle_progress(_progress(10.0))
    clock.value = 2.0
    monitor.handle_progress(_progress(20.0, accepted_attempts=2))
    clock.value = 3.0
    monitor.handle_progress(_progress(30.0, accepted_attempts=3))

    assert monitor.progress_percent == 30.0
    assert monitor.eta_s == pytest.approx(7.0)

    clock.value = 4.0
    failed = _progress(
        30.0,
        accepted=False,
        accepted_attempts=3,
        rejected_attempts=1,
        current_dt_s=10.0,
        next_dt_s=5.0,
    )
    failed["pH"] = 999.0
    monitor.handle_progress(failed)

    assert monitor.progress_percent == 30.0
    assert monitor.eta_s is None
    assert "WARNING Reaktoro solve failed at 30 s, attempted dt 10 s" in stream.getvalue()
    assert "State restored; retrying from 30 s with dt 5 s" in stream.getvalue()
    assert "ERROR" not in stream.getvalue()
    assert "999" not in stream.getvalue()

    clock.value = 5.0
    monitor.handle_progress(
        _progress(35.0, accepted_attempts=4, rejected_attempts=1, current_dt_s=5.0)
    )
    assert "Solver recovered at attempted dt 5 s after 1 retry" in stream.getvalue()
    assert "\x1b" not in stream.getvalue()


def test_selected_results_are_emitted_only_from_accepted_rows(tmp_path: Path) -> None:
    case = _case(
        tmp_path,
        "results",
        {
            "enabled": True,
            "refresh_interval_s": 0.5,
            "scalars": ["pH"],
            "species": ["HCO3-"],
            "minerals": ["Calcite"],
            "result_times": [{"value": 20, "unit": "seconds"}],
        },
    )
    stream = io.StringIO()
    monitor = SimulationMonitor(case, display_enabled=True, stream=stream)
    failed = _progress(10.0, accepted=False, rejected_attempts=1, next_dt_s=5.0)
    failed.update({"pH": 99.0, "species_molality_mol_kgw::HCO3-": 88.0})
    monitor.handle_progress(failed)
    assert "99" not in stream.getvalue()
    assert "88" not in stream.getvalue()

    monitor.handle_accepted_row(
        {
            "time_s": 20.0,
            "pH": 5.34,
            "species_molality_mol_kgw::HCO3-": 0.002,
            "mineral_amount_mol::Calcite": 0.095,
            "mineral_delta_mol::Calcite": -0.005,
        }
    )

    text = stream.getvalue()
    assert "RESULT  Accepted scientific result at 20 s" in text
    assert "pH=5.34" in text
    assert "HCO3-=0.002 mol/kgw" in text
    assert "Calcite=0.095 mol (delta -0.005 mol)" in text


def test_monitor_result_times_must_already_be_solver_output_times(tmp_path: Path) -> None:
    raw = yaml.safe_load(SOURCE_CASE.read_text(encoding="utf-8"))
    raw["paths"]["output_dir"] = str(tmp_path / "invalid-output")
    raw["solver"]["timestep"]["time"] = {
        "duration_value": 100,
        "duration_unit": "seconds",
    }
    raw["solver"]["timestep"]["step_size"] = {
        "dt": {"value": 10, "unit": "seconds"}
    }
    raw["solver"]["timestep"]["output_schedule"] = {
        "mode": "explicit",
        "include_initial": True,
        "include_final": True,
        "explicit_times": [{"value": 20, "unit": "seconds"}],
        "logarithmic": None,
    }
    raw["monitor"] = {
        "enabled": True,
        "refresh_interval_s": 0.5,
        "scalars": ["pH"],
        "species": [],
        "minerals": [],
        "result_times": [{"value": 25, "unit": "seconds"}],
    }
    path = tmp_path / "invalid-monitor-time.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError, match="monitor times do not create solver targets"):
        load_case(path)

    raw["paths"]["output_dir"] = str(tmp_path / "valid-output")
    raw["monitor"]["result_times"] = [{"value": 20, "unit": "seconds"}]
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    case = load_case(path)
    assert case.monitor_result_times_s == (20.0,)
    assert case.extra_solver_targets_s == ()


def test_simulation_log_is_concise_and_keeps_terminal_diagnosis(tmp_path: Path) -> None:
    case = _case(tmp_path, "failure-log")
    monitor = SimulationMonitor(case, display_enabled=False)
    monitor.start(python_version="3.11", reaktoro_version="2.13.0")
    monitor.activate_log()
    monitor.handle_progress(_progress(20.0, accepted_attempts=2))
    monitor.handle_progress(
        _progress(
            20.0,
            accepted=False,
            accepted_attempts=2,
            rejected_attempts=1,
            next_dt_s=5.0,
        )
    )
    monitor.finish(_result(completed=False, final_time_s=20.0, warning="partial run"), case.output_dir)

    text = monitor.log_path.read_text(encoding="utf-8")
    assert "[WARNING] Reaktoro solve failed" in text
    assert "[ERROR] FAILED | stage=solver_execution | last accepted=20 s" in text
    assert "retry limit exhausted" in text
    assert "[WARNING] partial run" in text
    assert str(case.output_dir / "diagnostics.json") in text


def test_routine_successes_do_not_flood_simulation_log(tmp_path: Path) -> None:
    case = _case(tmp_path, "success-log")
    monitor = SimulationMonitor(case, display_enabled=False)
    monitor.start(python_version="3.11", reaktoro_version="2.13.0")
    monitor.activate_log()
    for index in range(1, 11):
        monitor.handle_progress(_progress(index * 10.0, accepted_attempts=index))
    monitor.finish(_result(completed=True, final_time_s=100.0), case.output_dir)

    lines = monitor.log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) < 12
    assert sum("Simulation completed" in line for line in lines) == 1
    assert not any("last SUCCESS" in line for line in lines)


def test_machine_display_is_silent(tmp_path: Path) -> None:
    case = _case(tmp_path, "machine")
    stream = io.StringIO()
    monitor = SimulationMonitor(case, display_enabled=False, stream=stream)
    monitor.start(python_version="3.11", reaktoro_version="2.13.0")
    monitor.handle_progress(_progress(10.0))
    assert stream.getvalue() == ""


def test_tty_dashboard_updates_in_place_and_leaves_final_status(tmp_path: Path) -> None:
    case = _case(tmp_path, "tty")
    stream = _TtyStream()
    clock = _Clock()
    monitor = SimulationMonitor(case, display_enabled=True, stream=stream, clock=clock)
    monitor.start(python_version="3.11", reaktoro_version="2.13.0")
    clock.value = 1.0
    monitor.handle_progress(_progress(10.0))
    monitor.finish(_result(completed=True, final_time_s=100.0), case.output_dir)

    text = stream.getvalue()
    assert "\x1b[3A\x1b[J" in text
    assert "RESULT  Simulation completed" in text
    assert text.rstrip().endswith(str(case.output_dir))


def test_full_runner_keeps_machine_stdout_clean_and_writes_accounted_log(tmp_path: Path) -> None:
    machine_case = _case(tmp_path, "runner-machine", duration_s=20.0, native_kinetics=True)
    completed = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            str(machine_case.config_path),
            "--events-jsonl",
            "--run-id",
            "monitor-machine-test",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    events = [json.loads(line) for line in completed.stdout.splitlines()]
    assert completed.returncode == 0, completed.stderr
    assert events[0]["event_type"] == "worker_ready"
    assert events[-1]["event_type"] == "simulation_finished"
    assert "Reaktoro Batch Runner" not in completed.stdout
    assert "\x1b" not in completed.stdout
    assert (machine_case.output_dir / "simulation.log").is_file()
    manifest = json.loads((machine_case.output_dir / "manifest.json").read_text(encoding="utf-8"))
    diagnostics = json.loads(
        (machine_case.output_dir / "diagnostics.json").read_text(encoding="utf-8")
    )
    assert "simulation.log" in manifest["output_files"]
    assert "simulation.log" in diagnostics["output_completeness"]["files_written"]


def test_full_runner_non_tty_human_mode_is_readable(tmp_path: Path) -> None:
    human_case = _case(tmp_path, "runner-human", duration_s=20.0, native_kinetics=True)
    completed = subprocess.run(
        [sys.executable, str(RUNNER), str(human_case.config_path)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Reaktoro Batch Runner | RUNNING" in completed.stdout
    assert "Progress [" in completed.stdout
    assert "RESULT  Simulation completed" in completed.stdout
    assert "\x1b" not in completed.stdout
    assert (human_case.output_dir / "simulation.log").is_file()


def test_monitor_enabled_and_disabled_produce_identical_solver_outputs(tmp_path: Path) -> None:
    disabled_case = _case(
        tmp_path,
        "science-off",
        {
            "enabled": False,
            "refresh_interval_s": 0.5,
            "scalars": ["pH"],
            "species": [],
            "minerals": [],
            "result_times": [],
        },
        duration_s=20.0,
        native_kinetics=True,
    )
    enabled_case = _case(
        tmp_path,
        "science-on",
        {
            "enabled": True,
            "refresh_interval_s": 0.5,
            "scalars": ["pH"],
            "species": [],
            "minerals": [],
            "result_times": [],
        },
        duration_s=20.0,
        native_kinetics=True,
    )

    disabled_result = run_simulation(disabled_case)
    monitor = SimulationMonitor(enabled_case, display_enabled=False)
    enabled_result = run_simulation(
        enabled_case,
        progress_ready=monitor.handle_progress,
        accepted_row_ready=monitor.handle_accepted_row,
    )
    disabled_rows = list(disabled_result.iter_rows())
    enabled_rows = list(enabled_result.iter_rows())
    disabled_history = list(disabled_result.iter_solver_history())
    enabled_history = list(enabled_result.iter_solver_history())

    assert disabled_result.diagnostics["simulation_completed"] is True
    assert enabled_result.diagnostics["simulation_completed"] is True
    assert disabled_rows == enabled_rows
    assert [row["time_s"] for row in disabled_rows] == [row["time_s"] for row in enabled_rows]
    assert [
        {key: value for key, value in record.items() if key != "wall_time_s"}
        for record in disabled_history
    ] == [
        {key: value for key, value in record.items() if key != "wall_time_s"}
        for record in enabled_history
    ]
    disabled_result.cleanup_streams()
    enabled_result.cleanup_streams()

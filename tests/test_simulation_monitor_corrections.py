from __future__ import annotations

import io
from pathlib import Path

import yaml

from batch_runner.config import load_case
from batch_runner.monitor import SimulationMonitor


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_CASE = PROJECT_ROOT / "cases" / "source_supported_kinetic_case.yaml"


def _case(tmp_path: Path, name: str):
    raw = yaml.safe_load(SOURCE_CASE.read_text(encoding="utf-8"))
    raw["paths"]["output_dir"] = str(tmp_path / f"output-{name}")
    raw["solver"]["timestep"]["time"] = {
        "duration_value": 100.0,
        "duration_unit": "seconds",
    }
    raw["solver"]["timestep"]["step_size"] = {
        "dt": {"value": 10.0, "unit": "seconds"}
    }
    path = tmp_path / f"{name}.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return load_case(path)


def _progress(
    *,
    accepted_time_s: float,
    accepted: bool,
    solver_succeeded: bool | None,
    stage: str,
    current_dt_s: float,
    next_dt_s: float | None = None,
    reason: str | None = None,
) -> dict:
    return {
        "accepted_time_s": accepted_time_s,
        "requested_duration_s": 100.0,
        "current_dt_s": current_dt_s,
        "next_dt_s": next_dt_s,
        "accepted_attempts": 2,
        "rejected_attempts": 1 if not accepted else 0,
        "latest_accepted": accepted,
        "solver_succeeded": solver_succeeded,
        "latest_reason": reason,
        "solver_iterations": 4 if solver_succeeded is not None else None,
        "stage": stage,
    }


def test_fixed_solver_failure_is_terminal_error(tmp_path: Path) -> None:
    case = _case(tmp_path, "fixed-failure")
    stream = io.StringIO()
    monitor = SimulationMonitor(case, display_enabled=True, stream=stream)

    monitor.handle_progress(
        _progress(
            accepted_time_s=20.0,
            accepted=False,
            solver_succeeded=False,
            stage="kinetic_step",
            current_dt_s=10.0,
            reason="Reaktoro solver failed during kinetic step",
        )
    )

    text = stream.getvalue()
    assert "ERROR   Reaktoro solve failed during kinetic step at 20 s" in text
    assert "State restored; retrying" not in text


def test_initial_equilibrium_failure_is_terminal_error(tmp_path: Path) -> None:
    case = _case(tmp_path, "initial-equilibrium-failure")
    stream = io.StringIO()
    monitor = SimulationMonitor(case, display_enabled=True, stream=stream)

    monitor.handle_progress(
        _progress(
            accepted_time_s=0.0,
            accepted=False,
            solver_succeeded=False,
            stage="initial_equilibrium",
            current_dt_s=0.0,
            reason="Reaktoro solver failed during initial equilibrium",
        )
    )

    text = stream.getvalue()
    assert "ERROR   Reaktoro solve failed during initial equilibrium at 0 s" in text
    assert "retrying" not in text


def test_adaptive_solver_rejection_remains_warning_with_retry(tmp_path: Path) -> None:
    case = _case(tmp_path, "adaptive-retry")
    stream = io.StringIO()
    monitor = SimulationMonitor(case, display_enabled=True, stream=stream)

    monitor.handle_progress(
        _progress(
            accepted_time_s=30.0,
            accepted=False,
            solver_succeeded=False,
            stage="adaptive_kinetic_attempt",
            current_dt_s=10.0,
            next_dt_s=5.0,
            reason="Reaktoro solve returned failure",
        )
    )

    text = stream.getvalue()
    assert "WARNING Reaktoro solve failed at 30 s, attempted dt 10 s" in text
    assert "State restored; retrying from 30 s with dt 5 s" in text
    assert "ERROR" not in text


def test_adaptive_cancellation_after_success_is_not_solver_failure(tmp_path: Path) -> None:
    case = _case(tmp_path, "adaptive-cancel")
    stream = io.StringIO()
    monitor = SimulationMonitor(case, display_enabled=True, stream=stream)

    monitor.handle_progress(
        _progress(
            accepted_time_s=30.0,
            accepted=False,
            solver_succeeded=True,
            stage="adaptive_kinetic_attempt",
            current_dt_s=10.0,
            reason="cooperative cancellation requested before step commit",
        )
    )

    text = stream.getvalue()
    assert "WARNING Step not committed at 30 s; state restored" in text
    assert "cooperative cancellation requested before step commit" in text
    assert "Reaktoro solve failed" not in text
    assert "retrying" not in text


def test_unsolved_status_remains_waiting_not_failed(tmp_path: Path) -> None:
    case = _case(tmp_path, "unsolved-status")
    stream = io.StringIO()
    monitor = SimulationMonitor(case, display_enabled=True, stream=stream)

    monitor.handle_progress(
        _progress(
            accepted_time_s=0.0,
            accepted=True,
            solver_succeeded=None,
            stage="initial_state",
            current_dt_s=0.0,
        )
    )

    assert "last waiting" in stream.getvalue()


def test_simulation_log_creation_failure_is_visible_and_exposed(
    tmp_path: Path, monkeypatch
) -> None:
    case = _case(tmp_path, "log-failure")
    stream = io.StringIO()
    monitor = SimulationMonitor(case, display_enabled=True, stream=stream)
    original_write_text = Path.write_text

    def fail_simulation_log(path: Path, *args, **kwargs):
        if path.name == "simulation.log":
            raise OSError("disk full")
        return original_write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_simulation_log)
    monitor.activate_log()

    assert monitor.log_error == "disk full"
    assert "WARNING simulation.log unavailable: disk full" in stream.getvalue()

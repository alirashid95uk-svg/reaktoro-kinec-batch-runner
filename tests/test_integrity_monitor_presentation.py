from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

from batch_runner.integrity_monitor import IntegritySimulationMonitor


def _case(tmp_path: Path):
    settings = SimpleNamespace(
        refresh_interval_s=0.5,
        scalars=[],
        species=[],
        minerals=[],
    )
    return SimpleNamespace(
        duration_s=100.0,
        monitor_result_times_s=(20.0,),
        output_dir=tmp_path,
        database_path=None,
        config=SimpleNamespace(
            outputs=SimpleNamespace(monitor=settings),
            case=SimpleNamespace(name="integrity-presentation"),
            database=SimpleNamespace(name="test.dat"),
            physical=SimpleNamespace(temperature_c=25.0, pressure_bar=1.0),
        ),
    )


def _snapshot(time_s: float) -> dict:
    return {
        "status": "evaluated",
        "time_s": time_s,
        "material_balance": {
            "status": "evaluated",
            "max_relative_residual": 3.2e-10,
            "rms_relative_residual": 6.8e-11,
            "cumulative_max_relative_residual": 7.1e-10,
            "worst_component": "Ca",
        },
        "carbon": {
            "status": "open_boundary",
            "reason": "fixed fugacity CO2(g)",
        },
        "charge": {
            "status": "evaluated",
            "residual_mol": 2.4e-12,
            "relative_residual": 1.2e-12,
        },
        "open_elements": ["C", "O"],
    }


def test_progress_line_contains_compact_balance_and_charge(tmp_path: Path) -> None:
    stream = io.StringIO()
    monitor = IntegritySimulationMonitor(
        _case(tmp_path),
        display_enabled=True,
        stream=stream,
    )
    monitor.handle_numerical_integrity(_snapshot(20.0))
    monitor.handle_progress(
        {
            "accepted_time_s": 20.0,
            "requested_duration_s": 100.0,
            "current_dt_s": 10.0,
            "next_dt_s": 10.0,
            "accepted_attempts": 2,
            "rejected_attempts": 0,
            "latest_accepted": True,
            "solver_succeeded": True,
            "latest_reason": None,
            "solver_iterations": 5,
            "stage": "kinetic_step",
        }
    )

    text = stream.getvalue()
    assert (
        "20 s / 1.667 min ( 20.00%) | "
        "balance 3.2e-10 rel (Ca) | charge 2.4e-12 mol | wall "
    ) in text


def test_finish_prints_and_logs_final_cumulative_integrity(tmp_path: Path) -> None:
    stream = io.StringIO()
    monitor = IntegritySimulationMonitor(
        _case(tmp_path),
        display_enabled=True,
        stream=stream,
    )
    monitor.activate_log()
    monitor.handle_numerical_integrity(_snapshot(100.0))

    result = SimpleNamespace(
        diagnostics={
            "simulation_completed": True,
            "output_completeness": {"status": "complete"},
            "final_time_reached_s": 100.0,
            "number_of_accepted_steps": 10,
            "number_of_rejected_steps": 1,
        }
    )
    monitor.finish(result, tmp_path)

    expected = (
        "Final accepted-state numerical integrity at 1.667 min | "
        "component max=3.2e-10 | worst component=Ca | "
        "RMS=6.8e-11 | cumulative max=7.1e-10 | "
        "carbon=open boundary / not evaluated | charge residual=2.4e-12 mol"
    )
    assert expected in stream.getvalue()
    assert expected in monitor.log_path.read_text(encoding="utf-8")

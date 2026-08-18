from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from batch_runner.config import CaseConfig, load_case
from batch_runner.outputs.tables import SOLVER_HISTORY_COLUMNS
from batch_runner.simulator.solver import calls as solver_calls_module
from batch_runner.simulator.solver import execution as solver_module


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_CASE_PATH = PROJECT_ROOT / "cases" / "source_supported_kinetic_case.yaml"


def _raw_adaptive_case(
    tmp_path: Path,
    *,
    duration_s: float = 1.0,
    dt_initial_s: float = 1.0,
    dt_min_s: float = 0.25,
    dt_max_s: float = 1.0,
    max_retries: int = 3,
    max_internal_steps: int = 100,
) -> dict:
    with SOURCE_CASE_PATH.open(encoding="utf-8") as stream:
        raw = yaml.safe_load(stream)
    raw["paths"]["output_dir"] = str(tmp_path / "outputs")
    raw["solver"]["timestep"] = {
        "mode": "adaptive",
        "time": {"duration_value": duration_s, "duration_unit": "seconds"},
        "step_size": {
            "dt_initial": {"value": dt_initial_s, "unit": "seconds"},
            "dt_min": {"value": dt_min_s, "unit": "seconds"},
            "dt_max": {"value": dt_max_s, "unit": "seconds"},
            "growth_factor": 2.0,
            "shrink_factor": 0.5,
            "max_retries_per_step": max_retries,
        },
        "max_internal_steps": max_internal_steps,
        "output_schedule": {
            "mode": "explicit",
            "include_initial": True,
            "include_final": True,
            "explicit_times": [],
        },
        "checkpoint_schedule": {"enabled": False, "times": []},
    }
    return raw


def _load_adaptive_case(tmp_path: Path, raw: dict):
    path = tmp_path / "case.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return load_case(path)


class _FakeResult:
    def __init__(self, succeeded: bool):
        self._succeeded = succeeded

    def succeeded(self) -> bool:
        return self._succeeded

    def iterations(self) -> int:
        return 1


class _FakeState:
    def __init__(self, value: float = 0.0):
        self.value = value

    def assign(self, other) -> None:
        self.value = other.value


def _run_fake(
    monkeypatch,
    case,
    *,
    outcomes: list[str] | None = None,
    cancel_after_solver_return: bool = False,
):
    remaining = iter(outcomes or [])
    calls: list[tuple[float, float]] = []

    class FakeSolver:
        def solve(self, state, dt_s):
            calls.append((state.value, dt_s))
            state.value += dt_s
            outcome = next(remaining, "success")
            if outcome == "raise":
                raise RuntimeError("solver raised")
            return _FakeResult(outcome == "success")

    monkeypatch.setattr(
        solver_calls_module.rkt, "KineticsSolver", lambda _system: FakeSolver()
    )
    monkeypatch.setattr(solver_module, "build_conditions", lambda *_args: (None, None))
    monkeypatch.setattr(solver_module, "snapshot_state", deepcopy)
    monkeypatch.setattr(
        solver_module,
        "collect_row",
        lambda _case, state, record, _initial: {
            "time_s": record["time_end_s"],
            "state_value": state.value,
        },
    )
    state = _FakeState()
    rows: list[dict] = []
    history: list[dict] = []
    checkpoints: list[tuple[float, float]] = []
    _initial, progress = solver_module.execute_solver(
        case,
        object(),
        state,
        row_ready=rows.append,
        solver_record_ready=history.append,
        checkpoint_ready=lambda record, checkpoint_state: checkpoints.append(
            (record["time_end_s"], checkpoint_state.value)
        ),
        cancel_requested=(lambda: bool(calls)) if cancel_after_solver_return else None,
    )
    return state, calls, rows, history, checkpoints, progress


def test_success_advances_state_and_grows_dt_up_to_max(
    tmp_path: Path, monkeypatch
) -> None:
    case = _load_adaptive_case(
        tmp_path,
        _raw_adaptive_case(
            tmp_path,
            dt_initial_s=0.25,
            dt_min_s=0.1,
            dt_max_s=0.5,
        ),
    )

    state, calls, rows, history, _checkpoints, progress = _run_fake(
        monkeypatch, case
    )

    assert calls == [(0.0, 0.25), (0.25, 0.5), (0.75, 0.25)]
    assert state.value == 1.0
    assert [record["next_dt_s"] for record in history] == [0.5, 0.5, 0.5]
    assert all(record["accepted"] for record in history)
    assert [row["time_s"] for row in rows] == [0.0, 1.0]
    assert progress["simulation_completed"] is True
    assert progress["final_time_reached_s"] == 1.0


@pytest.mark.parametrize("failure", ["unsuccessful", "raise"])
def test_failed_solve_restores_state_shrinks_and_retries_same_time(
    tmp_path: Path, monkeypatch, failure: str
) -> None:
    case = _load_adaptive_case(
        tmp_path,
        _raw_adaptive_case(
            tmp_path,
            duration_s=0.5,
            dt_initial_s=0.5,
            dt_min_s=0.125,
            dt_max_s=0.5,
        ),
    )

    state, calls, rows, history, _checkpoints, progress = _run_fake(
        monkeypatch,
        case,
        outcomes=["raise" if failure == "raise" else "failure", "success", "success"],
    )

    assert calls == [(0.0, 0.5), (0.0, 0.25), (0.25, 0.25)]
    assert state.value == 0.5
    assert history[0]["accepted"] is False
    assert history[0]["time_start_s"] == history[0]["time_end_s"] == 0.0
    assert history[0]["next_dt_s"] == 0.25
    assert [record["time_end_s"] for record in history[1:]] == [0.25, 0.5]
    assert [row["time_s"] for row in rows] == [0.0, 0.5]
    assert progress["number_of_solver_failed_attempts"] == 1
    assert progress["final_time_reached_s"] == 0.5


def test_retry_exhaustion_preserves_last_accepted_state(
    tmp_path: Path, monkeypatch
) -> None:
    case = _load_adaptive_case(
        tmp_path,
        _raw_adaptive_case(
            tmp_path,
            duration_s=0.5,
            dt_initial_s=0.5,
            dt_min_s=0.125,
            dt_max_s=0.5,
            max_retries=1,
        ),
    )

    state, calls, rows, history, _checkpoints, progress = _run_fake(
        monkeypatch, case, outcomes=["failure", "failure"]
    )

    assert calls == [(0.0, 0.5), (0.0, 0.25)]
    assert state.value == 0.0
    assert all(not record["accepted"] for record in history)
    assert [row["time_s"] for row in rows] == [0.0]
    assert progress["termination_reason"] == "retry_limit_exceeded"
    assert progress["final_time_reached_s"] == 0.0
    assert progress["failed_attempt_dt_s"] == 0.25


def test_dt_min_failure_terminates_cleanly(tmp_path: Path, monkeypatch) -> None:
    case = _load_adaptive_case(
        tmp_path,
        _raw_adaptive_case(
            tmp_path,
            duration_s=0.5,
            dt_initial_s=0.125,
            dt_min_s=0.125,
            dt_max_s=0.5,
            max_retries=99,
        ),
    )

    state, calls, rows, history, _checkpoints, progress = _run_fake(
        monkeypatch, case, outcomes=["failure"]
    )

    assert calls == [(0.0, 0.125)]
    assert state.value == 0.0
    assert history[0]["next_dt_s"] == 0.125
    assert [row["time_s"] for row in rows] == [0.0]
    assert progress["termination_reason"] == "minimum_timestep_rejected"
    assert progress["accepted_state_restored"] is True


def test_max_internal_steps_preserves_partial_accepted_state(
    tmp_path: Path, monkeypatch
) -> None:
    case = _load_adaptive_case(
        tmp_path,
        _raw_adaptive_case(
            tmp_path,
            dt_initial_s=0.2,
            dt_min_s=0.1,
            dt_max_s=1.0,
            max_internal_steps=2,
        ),
    )

    state, calls, rows, _history, _checkpoints, progress = _run_fake(
        monkeypatch, case
    )

    assert calls == [(0.0, 0.2), (0.2, 0.4)]
    assert state.value == pytest.approx(0.6)
    assert [row["time_s"] for row in rows] == [0.0]
    assert progress["termination_reason"] == "max_internal_steps_exceeded"
    assert progress["number_of_internal_attempts"] == 2
    assert progress["final_time_reached_s"] == pytest.approx(0.6)


def test_output_checkpoint_and_final_targets_are_exact(
    tmp_path: Path, monkeypatch
) -> None:
    raw = _raw_adaptive_case(
        tmp_path, dt_initial_s=0.7, dt_min_s=0.1, dt_max_s=1.0
    )
    raw["solver"]["timestep"]["output_schedule"]["explicit_times"] = [
        {"value": 0.3, "unit": "seconds"},
        {"value": 0.8, "unit": "seconds"},
    ]
    raw["solver"]["timestep"]["checkpoint_schedule"] = {
        "enabled": True,
        "times": [{"value": 0.5, "unit": "seconds"}],
    }
    case = _load_adaptive_case(tmp_path, raw)

    _state, calls, rows, history, checkpoints, progress = _run_fake(
        monkeypatch, case
    )

    assert [dt for _value, dt in calls] == pytest.approx([0.3, 0.2, 0.3, 0.2])
    assert [record["time_end_s"] for record in history] == [0.3, 0.5, 0.8, 1.0]
    assert [row["time_s"] for row in rows] == [0.0, 0.3, 0.8, 1.0]
    assert checkpoints == [(0.5, 0.5)]
    assert progress["final_time_reached_s"] == 1.0


def test_cancellation_after_solve_restores_uncommitted_state(
    tmp_path: Path, monkeypatch
) -> None:
    case = _load_adaptive_case(tmp_path, _raw_adaptive_case(tmp_path))

    state, _calls, rows, history, _checkpoints, progress = _run_fake(
        monkeypatch, case, cancel_after_solver_return=True
    )

    assert state.value == 0.0
    assert [row["time_s"] for row in rows] == [0.0]
    assert len(history) == 1 and history[0]["accepted"] is False
    assert "cancellation" in history[0]["failure_reason"]
    assert progress["termination_reason"] == "cancelled_cleanly"
    assert progress["final_time_reached_s"] == 0.0


def test_strict_schema_rejects_removed_solver_controls(tmp_path: Path) -> None:
    raw = _raw_adaptive_case(tmp_path)
    raw["solver"]["timestep"]["acceptance"] = {"enabled": True}
    with pytest.raises(ValidationError, match="acceptance"):
        CaseConfig.model_validate(raw)

    raw = _raw_adaptive_case(tmp_path)
    raw["solver"]["workflow"]["precondition_kinetics"] = False
    with pytest.raises(ValidationError, match="precondition_kinetics"):
        CaseConfig.model_validate(raw)

    raw = _raw_adaptive_case(tmp_path)
    raw["solver"]["timestep"]["mode"] = "adaptive_long_horizon"
    with pytest.raises(ValidationError, match="adaptive_long_horizon"):
        CaseConfig.model_validate(raw)


def test_solver_history_contains_only_reaktoro_attempt_fields(
    tmp_path: Path, monkeypatch
) -> None:
    case = _load_adaptive_case(tmp_path, _raw_adaptive_case(tmp_path))
    _state, _calls, _rows, history, _checkpoints, _progress = _run_fake(
        monkeypatch, case
    )

    assert list(history[0]) == SOLVER_HISTORY_COLUMNS
    assert SOLVER_HISTORY_COLUMNS == [
        "step_index",
        "attempt_index",
        "time_start_s",
        "time_end_s",
        "dt_s",
        "stage",
        "accepted",
        "solver_succeeded",
        "iterations",
        "wall_time_s",
        "failure_reason",
        "next_dt_s",
    ]


def test_adaptive_preflight_still_uses_dt_max_and_forced_intervals(
    tmp_path: Path,
) -> None:
    raw = _raw_adaptive_case(
        tmp_path,
        duration_s=10.0,
        dt_initial_s=3.0,
        dt_min_s=0.1,
        dt_max_s=3.0,
        max_internal_steps=3,
    )
    with pytest.raises(ValueError, match="minimum_possible_accepted_steps=4"):
        _load_adaptive_case(tmp_path, raw)

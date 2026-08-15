import csv
import json
import runpy
from copy import deepcopy
from itertools import islice
from pathlib import Path

import pytest
import reaktoro as rkt
import yaml
from pydantic import ValidationError

from batch_runner import OUTPUT_SCHEMA_VERSION
from batch_runner.config import CaseConfig, load_case
from batch_runner import outputs as outputs_module
from batch_runner.outputs import write_outputs
from batch_runner.output_tables import SOLVER_HISTORY_COLUMNS
from batch_runner.simulator import simulation as simulation_module
from batch_runner.simulator import solver as solver_module
from batch_runner.simulator.state_snapshot import snapshot_state


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_CASE_PATH = PROJECT_ROOT / "cases" / "source_supported_kinetic_case.yaml"


def _raw_case(
    tmp_path: Path,
    *,
    duration_value: float,
    duration_unit: str = "seconds",
    dt_value: float,
    dt_unit: str = "seconds",
    year_definition_days: float | None = None,
    max_internal_steps: int | None = None,
) -> dict:
    with SOURCE_CASE_PATH.open(encoding="utf-8") as stream:
        raw = yaml.safe_load(stream)
    raw["paths"]["output_dir"] = str(tmp_path / "outputs")
    raw["solver"]["workflow"]["precondition_kinetics"] = False
    if max_internal_steps is not None:
        raw["solver"]["timestep"]["max_internal_steps"] = max_internal_steps
    raw["solver"]["timestep"]["time"] = {
        "duration_value": duration_value,
        "duration_unit": duration_unit,
    }
    if year_definition_days is not None:
        raw["solver"]["timestep"]["time"]["year_definition_days"] = year_definition_days
    raw["solver"]["timestep"]["step_size"]["dt"] = {
        "value": dt_value,
        "unit": dt_unit,
    }
    return raw


def _load_case(tmp_path: Path, **time_values):
    path = tmp_path / "case.yaml"
    path.write_text(yaml.safe_dump(_raw_case(tmp_path, **time_values), sort_keys=False), encoding="utf-8")
    return load_case(path)


def _load_raw_case(tmp_path: Path, raw: dict):
    path = tmp_path / "case.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return load_case(path)


@pytest.mark.parametrize(
    ("duration_s", "dt_s", "expected_steps"),
    [
        (1.0, 0.25, [(0.25, 0.25), (0.25, 0.5), (0.25, 0.75), (0.25, 1.0)]),
        (1.0, 0.3, [(0.3, 0.3), (0.3, 0.6), (0.3, 0.9), (0.1, 1.0)]),
        (0.1, 1.0, [(0.1, 0.1)]),
        (0.3, 0.1, [(0.1, 0.1), (0.1, 0.2), (0.1, 0.3)]),
    ],
)
def test_fixed_step_schedule_uses_absolute_targets(
    tmp_path: Path,
    duration_s: float,
    dt_s: float,
    expected_steps: list[tuple[float, float]],
) -> None:
    case = _load_case(tmp_path, duration_value=duration_s, dt_value=dt_s)

    steps = tuple(case.fixed_steps_s())
    assert steps == tuple(expected_steps)
    assert steps[-1][1] == case.duration_s


def test_custom_360_day_year_applies_to_duration_and_dt(tmp_path: Path) -> None:
    case = _load_case(
        tmp_path,
        duration_value=1.0,
        duration_unit="year",
        dt_value=0.25,
        dt_unit="year",
        year_definition_days=360.0,
    )

    assert case.duration_s == 31_104_000.0
    assert case.dt_s == 7_776_000.0
    assert tuple(case.fixed_steps_s())[-1] == (7_776_000.0, 31_104_000.0)

    mixed = _load_case(
        tmp_path,
        duration_value=720.0,
        duration_unit="days",
        dt_value=0.5,
        dt_unit="year",
        year_definition_days=360.0,
    )
    assert mixed.duration_s == 62_208_000.0
    assert mixed.dt_s == 15_552_000.0
    assert mixed.internal_step_count == 4


def test_year_definition_is_required_exactly_when_a_time_uses_years(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="year_definition_days is required"):
        CaseConfig.model_validate(
            _raw_case(
                tmp_path,
                duration_value=720.0,
                duration_unit="days",
                dt_value=0.5,
                dt_unit="year",
            )
        )

    with pytest.raises(ValidationError, match="year_definition_days is only valid"):
        CaseConfig.model_validate(
            _raw_case(
                tmp_path,
                duration_value=1.0,
                dt_value=0.25,
                year_definition_days=360.0,
            )
        )


def test_explicit_output_schedule_is_sorted_unique_and_splits_only_needed_steps(
    tmp_path: Path,
) -> None:
    raw = _raw_case(tmp_path, duration_value=100.0, dt_value=30.0)
    raw["solver"]["timestep"]["output_schedule"] = {
        "mode": "explicit",
        "include_initial": True,
        "include_final": True,
        "explicit_times": [
            {"value": 60.0, "unit": "seconds"},
            {"value": 1.0, "unit": "minute"},
            {"value": 10.0, "unit": "seconds"},
            {"value": 10.0, "unit": "seconds"},
        ],
    }
    case = _load_raw_case(tmp_path, raw)

    assert tuple(case.output_times_s()) == (0.0, 10.0, 60.0, 100.0)
    assert [target for _dt, target in case.fixed_steps_s()] == [10.0, 30.0, 60.0, 90.0, 100.0]
    assert case.base_internal_step_count == 4
    assert case.internal_step_count == 5
    assert case.requested_output_row_count == 4


def test_hybrid_schedule_removes_overlap_with_logarithmic_component(tmp_path: Path) -> None:
    raw = _raw_case(tmp_path, duration_value=100.0, dt_value=25.0)
    raw["solver"]["timestep"]["output_schedule"] = {
        "mode": "hybrid",
        "include_initial": True,
        "include_final": True,
        "explicit_times": [
            {"value": 10.0, "unit": "seconds"},
            {"value": 1.0, "unit": "minute"},
        ],
        "logarithmic": {
            "start": {"value": 1.0, "unit": "second"},
            "end": {"value": 100.0, "unit": "seconds"},
            "points_per_decade": 1,
        },
    }
    case = _load_raw_case(tmp_path, raw)

    assert tuple(case.output_times_s()) == (0.0, 1.0, 10.0, 60.0, 100.0)


@pytest.mark.parametrize(
    (
        "duration_value",
        "duration_unit",
        "dt_value",
        "dt_unit",
        "start",
        "end",
        "year_days",
        "expected",
    ),
    [
        (
            1.0,
            "second",
            1.0,
            "second",
            {"value": 0.001, "unit": "second"},
            {"value": 1.0, "unit": "second"},
            None,
            (0.0, 0.001, 0.01, 0.1, 1.0),
        ),
        (
            100.0,
            "years",
            25.0,
            "years",
            {"value": 1.0, "unit": "year"},
            {"value": 100.0, "unit": "years"},
            360.0,
            (0.0, 31_104_000.0, 311_040_000.0, 3_110_400_000.0),
        ),
    ],
)
def test_logarithmic_output_schedule_handles_short_and_long_durations(
    tmp_path: Path,
    duration_value: float,
    duration_unit: str,
    dt_value: float,
    dt_unit: str,
    start: dict,
    end: dict,
    year_days: float | None,
    expected: tuple[float, ...],
) -> None:
    raw = _raw_case(
        tmp_path,
        duration_value=duration_value,
        duration_unit=duration_unit,
        dt_value=dt_value,
        dt_unit=dt_unit,
        year_definition_days=year_days,
    )
    raw["solver"]["timestep"]["output_schedule"] = {
        "mode": "logarithmic",
        "include_initial": True,
        "include_final": True,
        "logarithmic": {
            "start": start,
            "end": end,
            "points_per_decade": 1,
        },
    }
    case = _load_raw_case(tmp_path, raw)

    assert tuple(case.output_times_s()) == expected


def test_final_output_inclusion_is_configurable_but_solver_still_lands_at_duration(
    tmp_path: Path,
) -> None:
    raw = _raw_case(tmp_path, duration_value=5.0, dt_value=3.0)
    raw["solver"]["timestep"]["output_schedule"] = {
        "mode": "explicit",
        "include_initial": False,
        "include_final": False,
        "explicit_times": [{"value": 2.0, "unit": "seconds"}],
    }
    without_final = _load_raw_case(tmp_path, raw)
    assert tuple(without_final.output_times_s()) == (2.0,)
    assert tuple(without_final.fixed_steps_s())[-1][1] == without_final.duration_s

    raw["solver"]["timestep"]["output_schedule"]["include_final"] = True
    with_final = _load_raw_case(tmp_path, raw)
    assert tuple(with_final.output_times_s()) == (2.0, 5.0)


def test_schedule_targets_are_bounded_and_count_toward_internal_step_limit(tmp_path: Path) -> None:
    raw = _raw_case(tmp_path, duration_value=1.0, dt_value=1.0, max_internal_steps=2)
    raw["solver"]["timestep"]["output_schedule"] = {
        "mode": "explicit",
        "include_initial": True,
        "include_final": True,
        "explicit_times": [
            {"value": 0.25, "unit": "second"},
            {"value": 0.5, "unit": "second"},
        ],
    }
    with pytest.raises(ValueError, match="requested_internal_steps=3"):
        _load_raw_case(tmp_path, raw)

    raw["solver"]["timestep"]["max_internal_steps"] = 10
    raw["solver"]["timestep"]["output_schedule"]["explicit_times"] = [
        {"value": 1.1, "unit": "second"}
    ]
    with pytest.raises(ValueError, match="exceeds configured duration"):
        _load_raw_case(tmp_path, raw)


def test_oversized_logarithmic_schedule_is_rejected_before_generation(tmp_path: Path) -> None:
    raw = _raw_case(tmp_path, duration_value=10.0, dt_value=1.0, max_internal_steps=100)
    raw["solver"]["timestep"]["output_schedule"] = {
        "mode": "logarithmic",
        "include_initial": True,
        "include_final": True,
        "logarithmic": {
            "start": {"value": 1.0, "unit": "second"},
            "end": {"value": 10.0, "unit": "seconds"},
            "points_per_decade": 1_000_000_000,
        },
    }

    with pytest.raises(ValueError, match="exceeds max_internal_steps before generation"):
        _load_raw_case(tmp_path, raw)


def test_preflight_rejects_excessive_internal_steps_with_work_estimate(tmp_path: Path) -> None:
    with pytest.raises(
        ValueError,
        match=r"requested_internal_steps=100001, max_internal_steps=100000",
    ):
        _load_case(tmp_path, duration_value=100_001.0, dt_value=1.0)


def test_extremely_large_allowed_schedule_is_lazy(tmp_path: Path) -> None:
    case = _load_case(
        tmp_path,
        duration_value=1_000_000_000.0,
        dt_value=1.0,
        max_internal_steps=1_000_000_000,
    )

    schedule = case.fixed_steps_s()
    assert iter(schedule) is schedule
    assert list(islice(schedule, 3)) == [(1.0, 1.0), (1.0, 2.0), (1.0, 3.0)]
    assert case.internal_step_count == 1_000_000_000


@pytest.mark.parametrize(
    ("duration_value", "dt_value"),
    [(float("nan"), 1.0), (float("inf"), 1.0), (1.0, float("inf"))],
)
def test_non_finite_configured_times_are_rejected(
    tmp_path: Path,
    duration_value: float,
    dt_value: float,
) -> None:
    with pytest.raises(ValidationError):
        CaseConfig.model_validate(
            _raw_case(tmp_path, duration_value=duration_value, dt_value=dt_value)
        )


def test_finite_input_that_overflows_seconds_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="resolved duration_s and dt_s must be finite"):
        _load_case(
            tmp_path,
            duration_value=1.0e308,
            duration_unit="year",
            dt_value=1.0,
            dt_unit="year",
            year_definition_days=360.0,
        )


def test_reaktoro_state_assign_restores_snapshot() -> None:
    database = rkt.PhreeqcDatabase.withName("phreeqc.dat")
    aqueous = rkt.AqueousPhase(rkt.speciate(["H", "O", "Na", "Cl"]))
    aqueous.setActivityModel(rkt.ActivityModelPhreeqc(database))
    state = rkt.ChemicalState(rkt.ChemicalSystem(database, aqueous))
    state.set("H2O", 1.0, "kg")
    state.set("Na+", 1.0, "mol")
    state.set("Cl-", 1.0, "mol")
    accepted = snapshot_state(state)

    state.set("Na+", 2.0, "mol")
    state.assign(accepted)

    assert float(state.speciesAmount("Na+")) == pytest.approx(1.0)

class _FakeResult:
    def __init__(self, succeeded: bool):
        self._succeeded = succeeded

    def succeeded(self) -> bool:
        return self._succeeded

    def iterations(self) -> int:
        return 1


class _FakeSolver:
    def __init__(self, results: list[bool], calls: list[float]):
        self.results = iter(results)
        self.calls = calls

    def solve(self, state, dt_s):
        self.calls.append(dt_s)
        state.value += 1
        return _FakeResult(next(self.results))


class _FakeState:
    def __init__(self):
        self.value = 0

    def assign(self, other) -> None:
        self.value = other.value

    def output(self, path: str) -> None:
        Path(path).write_text(f"state={self.value}\n", encoding="utf-8")


def _install_solver_spy(monkeypatch, results: list[bool]):
    calls: list[float] = []
    solver = _FakeSolver(results, calls)
    monkeypatch.setattr(solver_module.rkt, "KineticsSolver", lambda _system: solver)
    monkeypatch.setattr(solver_module, "build_conditions", lambda *_args: (None, None))
    monkeypatch.setattr(solver_module, "snapshot_state", deepcopy)

    def collect_row(_case, _state, record, _initial_state):
        return {"time_s": record["time_end_s"], "state_value": _state.value}

    monkeypatch.setattr(solver_module, "collect_row", collect_row)
    return calls


def test_solver_records_monotonic_absolute_times_and_exact_final_time(tmp_path: Path, monkeypatch) -> None:
    case = _load_case(tmp_path, duration_value=0.3, dt_value=0.1)
    calls = _install_solver_spy(monkeypatch, [True, True, True])
    rows = []
    history = []
    state = _FakeState()

    _initial_state, progress = solver_module.execute_solver(
        case,
        object(),
        state,
        row_ready=rows.append,
        solver_record_ready=history.append,
    )

    assert calls == [0.1, 0.1, 0.1]
    assert [record["time_start_s"] for record in history] == [0.0, 0.1, 0.2]
    assert [record["time_end_s"] for record in history] == [0.1, 0.2, 0.3]
    assert all(left < right for left, right in zip([0.0, 0.1, 0.2], [0.1, 0.2, 0.3]))
    assert rows[-1]["time_s"] == history[-1]["time_end_s"] == case.duration_s
    assert progress["simulation_completed"] is True


def test_fixed_solver_cancels_at_safe_boundary_after_return(
    tmp_path: Path,
    monkeypatch,
) -> None:
    case = _load_case(tmp_path, duration_value=1.0, dt_value=0.5)
    calls = _install_solver_spy(monkeypatch, [True, True])
    rows = []
    history = []
    state = _FakeState()

    _initial_state, progress = solver_module.execute_solver(
        case,
        object(),
        state,
        row_ready=rows.append,
        solver_record_ready=history.append,
        cancel_requested=lambda: bool(calls),
    )

    assert calls == [0.5]
    assert state.value == 1
    assert [row["time_s"] for row in rows] == [0.0]
    assert len(history) == 1 and history[0]["accepted"] is True
    assert progress["termination_reason"] == "cancelled_cleanly"
    assert progress["cancellation_boundary"] == "after_fixed_solver_attempt"
    assert progress["final_time_reached_s"] == 0.5


def test_fixed_solver_failure_remains_primary_when_cancel_arrives(
    tmp_path: Path, monkeypatch
) -> None:
    case = _load_case(tmp_path, duration_value=1.0, dt_value=0.5)
    calls = _install_solver_spy(monkeypatch, [False])

    _initial_state, progress = solver_module.execute_solver(
        case,
        object(),
        _FakeState(),
        solver_record_ready=lambda _record: None,
        cancel_requested=lambda: bool(calls),
    )

    assert progress["termination_reason"] == "solver_failure"
    assert progress["failed_stage"] == "kinetic_step"
    assert progress["cancellation_requested"] is True
    assert progress["cancellation_boundary"] == "after_fixed_solver_attempt"


def test_solver_lands_on_sparse_output_times_without_emitting_every_internal_step(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw = _raw_case(tmp_path, duration_value=1.0, dt_value=0.3)
    raw["solver"]["timestep"]["output_schedule"] = {
        "mode": "explicit",
        "include_initial": True,
        "include_final": True,
        "explicit_times": [
            {"value": 0.2, "unit": "second"},
            {"value": 0.55, "unit": "second"},
            {"value": 0.8, "unit": "second"},
        ],
    }
    case = _load_raw_case(tmp_path, raw)
    calls = _install_solver_spy(monkeypatch, [True] * case.internal_step_count)
    rows = []
    history = []

    _initial_state, progress = solver_module.execute_solver(
        case,
        object(),
        _FakeState(),
        row_ready=rows.append,
        solver_record_ready=history.append,
    )

    assert [record["time_end_s"] for record in history] == [
        0.2,
        0.3,
        0.55,
        0.6,
        0.8,
        0.9,
        1.0,
    ]
    assert [row["time_s"] for row in rows] == [0.0, 0.2, 0.55, 0.8, 1.0]
    assert len(calls) == case.internal_step_count == 7
    assert len(rows) == case.requested_output_row_count == 5
    assert progress["final_time_reached_s"] == case.duration_s


def test_checkpoint_target_splits_solver_step_without_creating_timeseries_row(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw = _raw_case(tmp_path, duration_value=1.0, dt_value=0.5)
    raw["solver"]["timestep"]["output_schedule"] = {
        "mode": "explicit",
        "include_initial": True,
        "include_final": True,
        "explicit_times": [{"value": 0.2, "unit": "second"}],
    }
    raw["solver"]["timestep"]["checkpoint_schedule"] = {
        "enabled": True,
        "times": [{"value": 0.4, "unit": "second"}],
    }
    case = _load_raw_case(tmp_path, raw)
    _install_solver_spy(monkeypatch, [True] * case.internal_step_count)
    rows = []
    history = []
    checkpoints = []

    _initial_state, progress = solver_module.execute_solver(
        case,
        object(),
        _FakeState(),
        row_ready=rows.append,
        solver_record_ready=history.append,
        checkpoint_ready=lambda record, _state: checkpoints.append(record["time_end_s"]),
    )

    assert [record["time_end_s"] for record in history] == [0.2, 0.4, 0.5, 1.0]
    assert [row["time_s"] for row in rows] == [0.0, 0.2, 1.0]
    assert checkpoints == [0.4]
    assert progress["checkpoint_count"] == 1


def test_every_internal_step_includes_checkpoint_split_steps(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw = _raw_case(tmp_path, duration_value=1.0, dt_value=0.5)
    raw["solver"]["timestep"]["checkpoint_schedule"] = {
        "enabled": True,
        "times": [{"value": 0.25, "unit": "second"}],
    }
    case = _load_raw_case(tmp_path, raw)
    _install_solver_spy(monkeypatch, [True] * case.internal_step_count)
    rows = []

    solver_module.execute_solver(
        case,
        object(),
        _FakeState(),
        row_ready=rows.append,
    )

    assert [row["time_s"] for row in rows] == [0.0, 0.25, 0.5, 1.0]
    assert case.requested_output_row_count == 4
    assert case.output_schedule_summary()["representation"] == (
        "every actual accepted solver step, including schedule-split steps"
    )


def test_checkpoint_files_are_streamed_and_declared_in_manifest(tmp_path: Path, monkeypatch) -> None:
    raw = _raw_case(tmp_path, duration_value=1.0, dt_value=0.5)
    raw["solver"]["timestep"]["checkpoint_schedule"] = {
        "enabled": True,
        "times": [{"value": 0.5, "unit": "second"}],
    }
    raw["outputs"]["timeseries"]["enabled"] = False
    raw["outputs"]["solver_history"]["enabled"] = False
    raw["outputs"]["plots"] = {key: False for key in raw["outputs"]["plots"]}
    raw["outputs"]["debug"] = {key: False for key in raw["outputs"]["debug"]}
    raw["outputs"]["summaries"] = {key: False for key in raw["outputs"]["summaries"]}
    case = _load_raw_case(tmp_path, raw)

    class FakeSystem:
        def elements(self):
            return []

        species = phases = reactions = surfaces = elements

    def fake_execute(
        _case,
        _system,
        state,
        *,
        row_ready,
        solver_record_ready,
        boundary_row_ready,
        checkpoint_ready,
    ):
        del row_ready, solver_record_ready, boundary_row_ready
        checkpoint_ready({"time_end_s": 0.5, "dt_s": 0.5}, state)
        return deepcopy(state), {
            "simulation_completed": True,
            "failed_stage": None,
            "error_message": None,
            "termination_reason": "completed",
            "final_time_reached_s": 1.0,
            "number_of_accepted_steps": 2,
            "number_of_rejected_steps": 0,
            "number_of_failed_steps": 0,
            "smallest_dt_s": 0.5,
            "largest_dt_s": 0.5,
            "average_dt_s": 0.5,
            "kinetic_precondition_applied": False,
            "failed_attempt_target_time_s": None,
            "failed_attempt_dt_s": None,
            "accepted_state_restored": None,
            "checkpoint_count": 1,
        }

    monkeypatch.setattr(simulation_module, "load_kinetic_parameters", lambda _case: object())
    monkeypatch.setattr(simulation_module, "load_database", lambda _case: object())
    monkeypatch.setattr(simulation_module, "build_kinetic_mapping", lambda *_args: [])
    monkeypatch.setattr(simulation_module, "require_valid_kinetic_mapping", lambda _mapping: None)
    monkeypatch.setattr(simulation_module, "build_chemical_system", lambda *_args: FakeSystem())
    monkeypatch.setattr(simulation_module, "build_chemical_state", lambda *_args: _FakeState())
    monkeypatch.setattr(simulation_module, "execute_solver", fake_execute)

    result = simulation_module.run_simulation(
        case,
        mapping_ready=lambda _mapping: case.output_dir.mkdir(parents=True),
    )
    write_outputs(case, result)

    index = case.output_dir / "checkpoints" / "index.jsonl"
    assert json.loads(index.read_text(encoding="utf-8"))["time_s"] == 0.5
    assert (case.output_dir / "checkpoints" / "checkpoint_000001_state.txt").is_file()
    manifest = json.loads((case.output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["time_semantics"]["checkpoint_schedule"]["resolved_times_s"] == [0.5]
    assert "checkpoints/index.jsonl" in manifest["output_files"]
    auditor = runpy.run_path(
        str(
            PROJECT_ROOT
            / ".agents"
            / "skills"
            / "objective1-output-auditor"
            / "scripts"
            / "audit_output_package.py"
        )
    )
    audit = auditor["audit"]
    assert audit(case.output_dir)["ok"] is True


def test_failed_solve_does_not_publish_attempted_time(tmp_path: Path, monkeypatch) -> None:
    case = _load_case(tmp_path, duration_value=1.0, dt_value=0.3)
    calls = _install_solver_spy(monkeypatch, [True, False])
    rows = []
    history = []
    state = _FakeState()

    _initial_state, progress = solver_module.execute_solver(
        case,
        object(),
        state,
        row_ready=rows.append,
        solver_record_ready=history.append,
    )

    assert calls == [0.3, 0.3]
    assert [row["time_s"] for row in rows] == [0.0, 0.3]
    assert history[-1]["accepted"] is False
    assert history[-1]["time_start_s"] == history[-1]["time_end_s"] == 0.3
    assert progress["final_time_reached_s"] == 0.3
    assert progress["failed_attempt_target_time_s"] == 0.6
    assert progress["accepted_state_restored"] is True
    assert state.value == 1


def test_streamed_partial_run_writes_machine_readable_failure_diagnostics(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw = _raw_case(tmp_path, duration_value=1.0, dt_value=0.3)
    raw["outputs"]["timeseries"] = {
        "enabled": True,
        "include_species_amounts": False,
        "include_species_molalities": False,
        "include_mineral_amounts": False,
        "include_mineral_deltas": False,
        "include_saturation_indices": False,
        "include_solver_columns": False,
    }
    raw["outputs"]["plots"] = {
        "enabled": False,
        "pH": False,
        "mineral_change": False,
        "saturation_index": False,
        "solver_dt": False,
        "solver_iterations": False,
    }
    raw["outputs"]["debug"] = {
        "enabled": False,
        "mineral_connection": False,
        "resolved_config": False,
        "final_state": False,
    }
    case = _load_raw_case(tmp_path, raw)

    class FakeSystem:
        def elements(self):
            return []

        def species(self):
            return []

        def phases(self):
            return []

        def reactions(self):
            return []

        def surfaces(self):
            return []

    def fake_execute(
        _case,
        _system,
        state,
        *,
        row_ready,
        solver_record_ready,
        boundary_row_ready,
        checkpoint_ready,
    ):
        del checkpoint_ready
        initial_row = {
            "time_s": 0.0,
            "time_days": 0.0,
            "stage": "initial_state",
            "pH": 7.0,
            "ionic_strength_molal": 0.0,
            "alkalinity_eq_per_l": 0.0,
        }
        boundary_row_ready("initial", initial_row)
        row_ready(
            initial_row
        )
        solver_record_ready(
            {
                "step_index": 0,
                "time_start_s": 0.0,
                "time_end_s": 0.3,
                "dt_s": 0.3,
                "stage": "kinetic_step",
                "accepted": True,
                "solver_succeeded": True,
                "iterations": 1,
                "wall_time_s": 0.01,
                "failure_reason": "",
            }
        )
        row_ready(
            {
                "time_s": 0.3,
                "time_days": 0.3 / 86400.0,
                "stage": "kinetic_step",
                "pH": 6.9,
                "ionic_strength_molal": 0.01,
                "alkalinity_eq_per_l": 0.0,
            }
        )
        solver_record_ready(
            {
                "step_index": 1,
                "time_start_s": 0.3,
                "time_end_s": 0.3,
                "dt_s": 0.3,
                "stage": "kinetic_step",
                "accepted": False,
                "solver_succeeded": False,
                "iterations": 2,
                "wall_time_s": 0.02,
                "failure_reason": "Reaktoro solver failed during kinetic step ending at 0.6 s",
            }
        )
        return deepcopy(state), {
            "simulation_completed": False,
            "failed_stage": "kinetic_step",
            "error_message": "Reaktoro solver failed during kinetic step ending at 0.6 s",
            "termination_reason": "solver_failure",
            "final_time_reached_s": 0.3,
            "number_of_accepted_steps": 1,
            "number_of_rejected_steps": 1,
            "number_of_failed_steps": 1,
            "smallest_dt_s": 0.3,
            "largest_dt_s": 0.3,
            "average_dt_s": 0.3,
            "kinetic_precondition_applied": False,
            "failed_attempt_target_time_s": 0.6,
            "failed_attempt_dt_s": 0.3,
            "accepted_state_restored": True,
        }

    monkeypatch.setattr(simulation_module, "load_kinetic_parameters", lambda _case: object())
    monkeypatch.setattr(simulation_module, "load_database", lambda _case: object())
    monkeypatch.setattr(simulation_module, "build_kinetic_mapping", lambda *_args: [])
    monkeypatch.setattr(simulation_module, "require_valid_kinetic_mapping", lambda _mapping: None)
    monkeypatch.setattr(simulation_module, "build_chemical_system", lambda *_args: FakeSystem())
    monkeypatch.setattr(simulation_module, "build_chemical_state", lambda *_args: _FakeState())
    monkeypatch.setattr(simulation_module, "execute_solver", fake_execute)

    result = simulation_module.run_simulation(
        case,
        mapping_ready=lambda _mapping: case.output_dir.mkdir(parents=True),
    )

    assert result.rows is None
    assert [row["time_s"] for row in result.iter_rows()] == [0.0, 0.3]
    assert result.row_stream_path.is_file()
    write_outputs(case, result)

    diagnostics = json.loads((case.output_dir / "diagnostics.json").read_text(encoding="utf-8"))
    assert diagnostics["simulation_completed"] is False
    assert diagnostics["partial_run"] is True
    assert diagnostics["final_time_reached_s"] == 0.3
    assert diagnostics["failed_attempt_target_time_s"] == 0.6
    assert diagnostics["accepted_state_restored"] is True
    assert diagnostics["requested_internal_steps"] == 4
    with (case.output_dir / "timeseries.csv").open(newline="", encoding="utf-8") as stream:
        assert [float(row["time_s"]) for row in csv.DictReader(stream)] == [0.0, 0.3]
    assert not (case.output_dir / "mineral_summary.csv").exists()
    assert not result.row_stream_path.exists()


class _LifecycleSystem:
    def elements(self):
        return []

    species = phases = reactions = surfaces = elements


def _install_lifecycle_stubs(monkeypatch) -> None:
    monkeypatch.setattr(simulation_module, "load_database", lambda _case: object())
    monkeypatch.setattr(simulation_module, "load_kinetic_parameters", lambda _case: object())
    monkeypatch.setattr(simulation_module, "build_kinetic_mapping", lambda *_args: [])
    monkeypatch.setattr(simulation_module, "require_valid_kinetic_mapping", lambda _mapping: None)
    monkeypatch.setattr(
        simulation_module,
        "build_chemical_system",
        lambda *_args: _LifecycleSystem(),
    )
    monkeypatch.setattr(simulation_module, "build_chemical_state", lambda *_args: _FakeState())


@pytest.mark.parametrize(
    "failed_stage",
    [
        "database_loading",
        "kinetics_loading",
        "mapping",
        "system_construction",
        "state_construction",
    ],
)
def test_setup_failures_produce_complete_machine_readable_diagnostics(
    tmp_path: Path,
    monkeypatch,
    failed_stage: str,
) -> None:
    case = _load_case(tmp_path, duration_value=1.0, dt_value=1.0)
    _install_lifecycle_stubs(monkeypatch)

    def fail(*_args):
        raise LookupError(f"forced {failed_stage} failure")

    targets = {
        "database_loading": (simulation_module, "load_database", fail),
        "kinetics_loading": (simulation_module, "load_kinetic_parameters", fail),
        "mapping": (simulation_module, "build_kinetic_mapping", fail),
        "system_construction": (simulation_module, "build_chemical_system", fail),
        "state_construction": (simulation_module, "build_chemical_state", fail),
    }
    monkeypatch.setattr(*targets[failed_stage])

    result = simulation_module.run_simulation(case)
    write_outputs(case, result)
    diagnostics = json.loads((case.output_dir / "diagnostics.json").read_text(encoding="utf-8"))

    assert diagnostics["failed_stage"] == failed_stage
    assert diagnostics["exception_type"] == "LookupError"
    assert diagnostics["error_message"] == f"forced {failed_stage} failure"
    assert diagnostics["final_time_reached_s"] == 0.0
    assert diagnostics["output_completeness"]["status"] == "partial"
    assert "diagnostics.json" in diagnostics["output_completeness"]["files_written"]


def test_unexpected_solver_exception_preserves_last_accepted_time(
    tmp_path: Path,
    monkeypatch,
) -> None:
    case = _load_case(tmp_path, duration_value=1.0, dt_value=0.5)
    _install_lifecycle_stubs(monkeypatch)

    def fail_after_acceptance(
        _case,
        _system,
        _state,
        *,
        row_ready,
        solver_record_ready,
        boundary_row_ready,
        checkpoint_ready,
    ):
        del row_ready, boundary_row_ready, checkpoint_ready
        solver_record_ready(
            {
                "step_index": 0,
                "attempt_index": 1,
                "time_start_s": 0.0,
                "time_end_s": 0.5,
                "dt_s": 0.5,
                "stage": "kinetic_step",
                "accepted": True,
                "solver_succeeded": True,
            }
        )
        raise ArithmeticError("forced solver lifecycle failure")

    monkeypatch.setattr(simulation_module, "execute_solver", fail_after_acceptance)
    result = simulation_module.run_simulation(case)

    assert result.diagnostics["failed_stage"] == "solver_execution"
    assert result.diagnostics["exception_type"] == "ArithmeticError"
    assert result.diagnostics["final_time_reached_s"] == 0.5
    assert result.diagnostics["number_of_accepted_steps"] == 1


def test_output_failure_preserves_simulation_status_and_file_completeness(
    tmp_path: Path,
    monkeypatch,
) -> None:
    case = _load_case(tmp_path, duration_value=1.0, dt_value=1.0)
    _install_lifecycle_stubs(monkeypatch)

    def fake_execute(*_args, **_kwargs):
        return _FakeState(), {
            "simulation_completed": True,
            "failed_stage": None,
            "exception_type": None,
            "error_message": None,
            "termination_reason": "completed",
            "final_time_reached_s": 1.0,
            "number_of_accepted_steps": 1,
            "number_of_rejected_steps": 0,
            "number_of_failed_steps": 0,
            "smallest_dt_s": 1.0,
            "largest_dt_s": 1.0,
            "average_dt_s": 1.0,
            "kinetic_precondition_applied": False,
            "failed_attempt_target_time_s": None,
            "failed_attempt_dt_s": None,
            "accepted_state_restored": None,
            "checkpoint_count": 0,
            "number_of_internal_attempts": 1,
            "number_of_solver_failed_attempts": 0,
            "retries_at_final_accepted_time": 0,
            "rejection_reason_counts": {},
        }

    monkeypatch.setattr(simulation_module, "execute_solver", fake_execute)
    result = simulation_module.run_simulation(case)

    def fail_csv(*_args, **_kwargs):
        raise OSError("forced CSV failure")

    monkeypatch.setattr(outputs_module, "write_csv", fail_csv)
    write_outputs(case, result)
    diagnostics = json.loads((case.output_dir / "diagnostics.json").read_text(encoding="utf-8"))

    assert diagnostics["simulation_completed"] is True
    assert diagnostics["failed_stage"] is None
    assert diagnostics["output_failure"] == {
        "failed_stage": "output_writing",
        "exception_type": "OSError",
        "error_message": "forced CSV failure",
    }
    assert diagnostics["final_time_reached_s"] == 1.0
    assert diagnostics["output_completeness"]["status"] == "partial"
    assert "forced CSV failure" in result.exception_traceback
    assert not (case.output_dir / ".timeseries.jsonl").exists()
    assert not (case.output_dir / ".solver_history.jsonl").exists()
    assert (case.output_dir / "debug" / "partial_timeseries.jsonl").is_file()
    assert (case.output_dir / "debug" / "partial_solver_history.jsonl").is_file()
    assert not (case.output_dir / "surrogate_dataset.csv").exists()


def test_output_failure_does_not_overwrite_primary_failure(tmp_path: Path, monkeypatch) -> None:
    case = _load_case(tmp_path, duration_value=1.0, dt_value=1.0)
    _install_lifecycle_stubs(monkeypatch)

    def fail_mapping(*_args):
        raise LookupError("primary mapping failure")

    monkeypatch.setattr(simulation_module, "build_kinetic_mapping", fail_mapping)
    result = simulation_module.run_simulation(case)

    def fail_csv(*_args, **_kwargs):
        raise OSError("secondary output failure")

    monkeypatch.setattr(outputs_module, "write_csv", fail_csv)
    write_outputs(case, result)
    diagnostics = json.loads((case.output_dir / "diagnostics.json").read_text(encoding="utf-8"))

    assert diagnostics["failed_stage"] == "mapping"
    assert diagnostics["error_message"] == "primary mapping failure"
    assert diagnostics["output_failure"] == {
        "failed_stage": "output_writing",
        "exception_type": "OSError",
        "error_message": "secondary output failure",
    }
    assert "primary mapping failure" in result.exception_traceback
    assert "secondary output failure" in result.exception_traceback


def test_output_auditor_rejects_previous_schema_version(tmp_path: Path) -> None:
    output_dir = tmp_path / "old_schema"
    output_dir.mkdir()
    manifest = {
        "output_schema_version": "objective1_audit_v3",
        "run_identity": {
            "case_name": "old",
            "output_schema_version": "objective1_audit_v3",
            "simulation_completed": True,
        },
        "traceability": {},
        "output_configuration": {
            "manifest": {"enabled": True},
            "diagnostics": {"enabled": False},
            "timeseries": {"enabled": False},
            "solver_history": {"enabled": False},
            "summaries": {},
            "plots": {"enabled": False},
            "debug": {"enabled": False},
        },
        "output_files": ["manifest.json"],
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    auditor = runpy.run_path(
        str(
            PROJECT_ROOT
            / ".agents"
            / "skills"
            / "objective1-output-auditor"
            / "scripts"
            / "audit_output_package.py"
        )
    )
    audit = auditor["audit"]

    observed = audit(output_dir)

    assert OUTPUT_SCHEMA_VERSION == "objective1_audit_v4"
    assert auditor["SOLVER_HISTORY_COLUMNS"] == SOLVER_HISTORY_COLUMNS
    assert observed["ok"] is False
    assert any("output_schema_version" in error for error in observed["errors"])

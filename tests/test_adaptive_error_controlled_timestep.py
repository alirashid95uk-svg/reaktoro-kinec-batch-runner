from copy import deepcopy
import json
from math import inf, isfinite, nan
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError

from batch_runner.config import CaseConfig, load_case
from batch_runner.outputs.tables import (
    ERROR_CONTROL_SOLVER_HISTORY_COLUMNS,
    SOLVER_HISTORY_COLUMNS,
    solver_history_columns,
)
from batch_runner.simulator.solver import calls as solver_calls_module
from batch_runner.simulator.solver import error_controlled as error_module
from batch_runner.simulator.solver import execution as execution_module


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_CASE_PATH = PROJECT_ROOT / "cases" / "jayasekara_2020_reproduction_monitor.yaml"
ERROR_CASE_PATH = (
    PROJECT_ROOT / "cases" / "jayasekara_2020_reproduction_error_controlled.yaml"
)


def _raw_case(
    tmp_path: Path,
    *,
    duration_s: float = 1.0,
    dt_initial_s: float = 1.0,
    dt_min_s: float = 0.01,
    dt_max_s: float = 1.0,
    absolute_tolerance_mol: float = 1.0,
    relative_tolerance: float = 0.0,
) -> dict:
    with SOURCE_CASE_PATH.open(encoding="utf-8") as stream:
        raw = yaml.safe_load(stream)
    raw["case"]["name"] = "jayasekara_quartz_controller_test"
    raw["paths"]["output_dir"] = str(tmp_path / "outputs")
    raw["minerals"] = [raw["minerals"][0]]
    raw["postprocessing"]["requested_minerals"] = ["Quartz"]
    raw["outputs"]["monitor"]["enabled"] = False
    raw["outputs"]["monitor"]["minerals"] = []
    raw["outputs"]["monitor"]["result_times"] = []
    raw["solver"]["timestep"] = {
        "mode": "adaptive_error_controlled",
        "time": {"duration_value": duration_s, "duration_unit": "seconds"},
        "step_size": {
            "dt_initial": {"value": dt_initial_s, "unit": "seconds"},
            "dt_min": {"value": dt_min_s, "unit": "seconds"},
            "dt_max": {"value": dt_max_s, "unit": "seconds"},
            "safety_factor": 0.8,
            "growth_factor": 2.0,
            "shrink_factor": 0.25,
            "solver_failure_shrink_factor": 0.5,
            "max_retries_per_step": 8,
        },
        "error_control": {
            "temporal_order": 1.0,
            "relative_tolerance": relative_tolerance,
            "negative_amount_tolerance": {"value": 1.0e-12, "unit": "mol"},
            "controlled_minerals": [
                {
                    "name": "Quartz",
                    "absolute_tolerance": {
                        "value": absolute_tolerance_mol,
                        "unit": "mol",
                    },
                    "reference_floor": {"value": 0.0, "unit": "mol"},
                }
            ],
        },
        "events": {"hard_mineral_exhaustion": None, "soft": None},
        "max_internal_steps": 100,
        "output_schedule": {
            "mode": "explicit",
            "include_initial": True,
            "include_final": True,
            "explicit_times": [],
        },
        "checkpoint_schedule": {"enabled": False, "times": []},
    }
    return raw


def _load(tmp_path: Path, raw: dict):
    path = tmp_path / "case.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return load_case(path)


class _Result:
    def __init__(self, succeeded: bool = True):
        self._succeeded = succeeded

    def succeeded(self) -> bool:
        return self._succeeded

    def iterations(self) -> int:
        return 1


class _State:
    def __init__(self, value: float = 1.0):
        self.value = value

    def assign(self, other) -> None:
        self.value = other.value

    def speciesAmount(self, name: str) -> float:
        assert name == "Quartz"
        return self.value


def _run_fake(
    monkeypatch,
    case,
    *,
    outcomes: list[bool | str] | None = None,
    update=None,
    cancel_after_calls: int | None = None,
):
    remaining = iter(outcomes or [])
    calls: list[tuple[float, float]] = []

    class Solver:
        def solve(self, state, dt_s):
            calls.append((state.value, dt_s))
            state.value = (
                update(state.value, dt_s)
                if update is not None
                else state.value + dt_s * state.value
            )
            outcome = next(remaining, True)
            if outcome == "raise":
                raise RuntimeError("solver raised")
            return _Result(bool(outcome))

    monkeypatch.setattr(solver_calls_module.rkt, "KineticsSolver", lambda _arg: Solver())
    monkeypatch.setattr(execution_module, "build_conditions", lambda *_args: (None, None))
    monkeypatch.setattr(execution_module, "snapshot_state", deepcopy)
    monkeypatch.setattr(
        execution_module,
        "collect_row",
        lambda _case, state, record, _initial: {
            "time_s": record["time_end_s"],
            "state_value": state.value,
        },
    )
    monkeypatch.setattr(
        error_module,
        "observe_state",
        lambda _run, state, time_s: {
            "time_s": time_s,
            "pH": state.value,
            "amounts": {"Quartz": state.value},
            "saturation_indices": {"Quartz": -1.0},
            "rates": {},
        },
    )
    state = _State()
    rows: list[dict] = []
    history: list[dict] = []
    _initial, progress = execution_module.execute_solver(
        case,
        object(),
        state,
        row_ready=rows.append,
        solver_record_ready=history.append,
        cancel_requested=(
            (lambda: len(calls) >= cancel_after_calls)
            if cancel_after_calls is not None
            else None
        ),
    )
    return state, calls, rows, history, progress

def test_schema_keeps_error_control_explicit_and_molar(tmp_path: Path) -> None:
    raw = _raw_case(tmp_path)
    assert CaseConfig.model_validate(raw).solver.timestep.mode == "adaptive_error_controlled"

    wrong_unit = deepcopy(raw)
    wrong_unit["solver"]["timestep"]["error_control"]["controlled_minerals"][0][
        "absolute_tolerance"
    ]["unit"] = "kg"
    with pytest.raises(ValidationError, match="mol"):
        CaseConfig.model_validate(wrong_unit)

    missing_mineral = deepcopy(raw)
    missing_mineral["solver"]["timestep"]["error_control"]["controlled_minerals"][0][
        "name"
    ] = "Illite"
    with pytest.raises(ValidationError, match="exactly match kinetic minerals"):
        CaseConfig.model_validate(missing_mineral)

    zero_hard_tolerance = deepcopy(raw)
    zero_hard_tolerance["solver"]["timestep"]["events"][
        "hard_mineral_exhaustion"
    ] = {
        "amount_tolerance": {"value": 0.0, "unit": "mol"},
        "time_tolerance": {"value": 0.001, "unit": "seconds"},
        "restart_dt": {"value": 0.25, "unit": "seconds"},
        "max_localizations": 2,
    }
    with pytest.raises(ValidationError, match="amount_tolerance must be positive"):
        CaseConfig.model_validate(zero_hard_tolerance)

    initial_equilibrium = deepcopy(raw)
    initial_equilibrium["solver"]["workflow"]["mode"] = (
        "fixed_fugacity_initial_equilibrium_then_closed_kinetics"
    )
    with pytest.raises(ValidationError, match="does not support an initial-equilibrium"):
        CaseConfig.model_validate(initial_equilibrium)


def test_existing_jayasekara_yaml_still_dispatches_legacy_adaptive(
    tmp_path: Path, monkeypatch
) -> None:
    with SOURCE_CASE_PATH.open(encoding="utf-8") as stream:
        raw = yaml.safe_load(stream)
    raw["paths"]["output_dir"] = str(tmp_path / "legacy_outputs")
    case = _load(tmp_path, raw)
    state = _State()
    called: list[str] = []

    monkeypatch.setattr(execution_module, "build_conditions", lambda *_args: (None, None))
    monkeypatch.setattr(
        execution_module,
        "kinetics_solver",
        lambda *_args: (called.append("solver_created") or object()),
    )
    monkeypatch.setattr(
        execution_module,
        "snapshot_state",
        lambda value: (called.append("state_snapshotted") or deepcopy(value)),
    )
    monkeypatch.setattr(
        execution_module,
        "collect_row",
        lambda _case, _state, record, _initial: {"time_s": record["time_end_s"]},
    )
    monkeypatch.setattr(
        execution_module,
        "run_adaptive_timesteps",
        lambda run: (called.append(run.timestep.mode) or run.state, {"path": "legacy"}),
    )

    _, progress = execution_module.execute_solver(case, object(), state)

    assert called == ["solver_created", "state_snapshotted", "adaptive"]
    assert progress == {"path": "legacy"}


def test_error_mode_extends_history_without_changing_legacy_columns(tmp_path: Path) -> None:
    case = _load(tmp_path, _raw_case(tmp_path))
    assert solver_history_columns(case) == ERROR_CONTROL_SOLVER_HISTORY_COLUMNS
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


def test_richardson_formula_and_zero_to_positive_scaling(tmp_path: Path) -> None:
    config = CaseConfig.model_validate(_raw_case(tmp_path)).solver.timestep.error_control
    estimate = error_module.richardson_error(
        {"Quartz": 0.0}, {"Quartz": 0.5}, config
    )
    assert estimate.raw_error_mol == pytest.approx(0.5)
    assert estimate.tolerance_mol == 1.0
    assert estimate.value == pytest.approx(0.5)
    with pytest.raises(ValueError, match="non-finite Richardson error"):
        error_module.richardson_error({"Quartz": 0.0}, {"Quartz": nan}, config)

    floor_raw = _raw_case(
        tmp_path, absolute_tolerance_mol=0.0, relative_tolerance=0.25
    )
    floor_raw["solver"]["timestep"]["error_control"]["controlled_minerals"][0][
        "reference_floor"
    ]["value"] = 2.0
    floor_config = CaseConfig.model_validate(floor_raw).solver.timestep.error_control
    floor_estimate = error_module.richardson_error(
        {"Quartz": 0.0}, {"Quartz": 0.5}, floor_config
    )
    assert floor_estimate.tolerance_mol == 0.5
    assert floor_estimate.value == 1.0


@pytest.mark.parametrize(
    ("error", "accepted", "expected"),
    [(0.0, True, 2.0), (1.0, True, 0.8), (inf, False, 0.25), (nan, False, 0.25)],
)
def test_i_controller_edges(error: float, accepted: bool, expected: float) -> None:
    assert error_module.controller_step(
        1.0,
        error,
        temporal_order=1.0,
        safety_factor=0.8,
        shrink_factor=0.25,
        growth_factor=2.0,
        dt_min_s=0.01,
        dt_max_s=10.0,
        accepted=accepted,
    ) == pytest.approx(expected)


def test_i_controller_bounds_and_rejected_steps_never_grow() -> None:
    for attempted in (0.01, 0.02, 1.0, 10.0):
        for error in (0.0, 1.0 - 1.0e-12, 1.0, 1.0 + 1.0e-12, 1.0e12):
            accepted = error <= 1.0
            next_dt = error_module.controller_step(
                attempted,
                error,
                temporal_order=1.0,
                safety_factor=0.8,
                shrink_factor=0.25,
                growth_factor=2.0,
                dt_min_s=0.01,
                dt_max_s=10.0,
                accepted=accepted,
            )
            assert 0.01 <= next_dt <= 10.0
            if not accepted:
                assert next_dt <= attempted


def test_full_and_half_branches_start_independently_and_accept_half_state(
    tmp_path: Path, monkeypatch
) -> None:
    case = _load(tmp_path, _raw_case(tmp_path))
    state, calls, rows, history, progress = _run_fake(monkeypatch, case)

    assert calls[:3] == [(1.0, 1.0), (1.0, 0.5), (1.5, 0.5)]
    assert state.value == pytest.approx(2.25)
    assert [row["time_s"] for row in rows] == [0.0, 1.0]
    assert history[0]["stage"] == "adaptive_error_controlled_trial"
    assert history[0]["richardson_error"] == pytest.approx(0.25)
    assert history[0]["accepted"] is True
    assert history[0]["reaktoro_solve_calls"] == 3
    assert progress["number_of_reaktoro_solve_calls"] == 3


def test_temporal_rejection_rolls_back_and_is_not_solver_failure(
    tmp_path: Path, monkeypatch
) -> None:
    case = _load(
        tmp_path,
        _raw_case(tmp_path, absolute_tolerance_mol=0.1, dt_min_s=0.01),
    )
    state, calls, _rows, history, progress = _run_fake(monkeypatch, case)
    trials = [record for record in history if record["dt_s"] > 0.0]

    assert calls[:3] == [(1.0, 1.0), (1.0, 0.5), (1.5, 0.5)]
    assert calls[3][0] == 1.0
    assert trials[0]["accepted"] is False
    assert trials[0]["rejection_reason"] == "temporal_error_rejection"
    assert trials[0]["solver_failure"] is False
    assert trials[0]["time_start_s"] == trials[0]["time_end_s"] == 0.0
    assert progress["number_of_temporal_error_rejections"] >= 1
    assert progress["simulation_completed"] is True
    assert state.value > 1.0


@pytest.mark.parametrize(
    ("outcomes", "first_trial_calls", "branch_statuses"),
    [
        ([False], 1, (False, None, None)),
        (["raise"], 1, (None, None, None)),
        ([True, False], 2, (True, False, None)),
        ([True, "raise"], 2, (True, None, None)),
        ([True, True, False], 3, (True, True, False)),
        ([True, True, "raise"], 3, (True, True, None)),
    ],
)
def test_solver_failure_is_separate_and_retries_from_accepted_state(
    tmp_path: Path,
    monkeypatch,
    outcomes: list[bool | str],
    first_trial_calls: int,
    branch_statuses: tuple[bool | None, bool | None, bool | None],
) -> None:
    case = _load(tmp_path, _raw_case(tmp_path))
    _state, calls, _rows, history, progress = _run_fake(
        monkeypatch, case, outcomes=outcomes
    )
    trials = [record for record in history if record["dt_s"] > 0.0]

    assert trials[0]["reaktoro_solve_calls"] == first_trial_calls
    assert calls[first_trial_calls][0] == 1.0
    assert trials[0]["rejection_reason"] == "solver_failure"
    assert trials[0]["solver_failure"] is True
    assert trials[0]["temporal_error_rejection"] is False
    assert trials[0]["richardson_error"] is None
    assert trials[0]["solver_succeeded"] is False
    assert (
        trials[0]["full_step_succeeded"],
        trials[0]["first_half_step_succeeded"],
        trials[0]["second_half_step_succeeded"],
    ) == branch_statuses
    assert progress["number_of_solver_failed_attempts"] == 1


@pytest.mark.parametrize("outcomes", [[], [False]])
def test_cancellation_after_trial_rolls_back_even_with_solver_failure(
    tmp_path: Path, monkeypatch, outcomes: list[bool]
) -> None:
    case = _load(tmp_path, _raw_case(tmp_path))
    state, _calls, _rows, history, progress = _run_fake(
        monkeypatch,
        case,
        outcomes=outcomes,
        cancel_after_calls=1,
    )
    trial = next(record for record in history if record["dt_s"] > 0.0)
    assert trial["accepted"] is False
    assert trial["rejection_reason"] == "cancellation"
    assert trial["solver_failure"] is bool(outcomes)
    assert trial["solver_succeeded"] is (not outcomes)
    assert state.value == 1.0
    assert progress["termination_reason"] == "cancelled_cleanly"


def test_output_checkpoint_and_final_targets_land_exactly(
    tmp_path: Path, monkeypatch
) -> None:
    raw = _raw_case(
        tmp_path,
        dt_initial_s=0.7,
        dt_min_s=0.4,
        dt_max_s=1.0,
        absolute_tolerance_mol=10.0,
    )
    raw["solver"]["timestep"]["output_schedule"]["explicit_times"] = [
        {"value": 0.3, "unit": "seconds"},
        {"value": 0.8, "unit": "seconds"},
    ]
    raw["solver"]["timestep"]["checkpoint_schedule"] = {
        "enabled": True,
        "times": [{"value": 0.5, "unit": "seconds"}],
    }
    case = _load(tmp_path, raw)
    _state, _calls, rows, history, progress = _run_fake(monkeypatch, case)
    accepted = [
        record for record in history if record["dt_s"] > 0.0 and record["accepted"]
    ]

    assert [record["time_end_s"] for record in accepted] == [0.3, 0.5, 0.8, 1.0]
    assert [row["time_s"] for row in rows] == [0.0, 0.3, 0.8, 1.0]
    assert all(record["reaktoro_solve_calls"] == 3 for record in accepted)
    assert progress["checkpoint_count"] == 1
    assert progress["final_time_reached_s"] == 1.0


def test_hard_exhaustion_localizes_and_resets_controller(
    tmp_path: Path, monkeypatch
) -> None:
    raw = _raw_case(
        tmp_path,
        duration_s=2.0,
        dt_initial_s=2.0,
        dt_min_s=0.01,
        dt_max_s=2.0,
        absolute_tolerance_mol=10.0,
    )
    raw["solver"]["timestep"]["events"]["hard_mineral_exhaustion"] = {
        "amount_tolerance": {"value": 0.1, "unit": "mol"},
        "time_tolerance": {"value": 0.001, "unit": "seconds"},
        "restart_dt": {"value": 0.25, "unit": "seconds"},
        "max_localizations": 4,
    }
    case = _load(tmp_path, raw)
    _state, _calls, _rows, history, progress = _run_fake(
        monkeypatch, case, update=lambda value, dt: value - dt
    )
    trials = [record for record in history if record["dt_s"] > 0.0]

    assert trials[0]["rejection_reason"] == "hard_event_localization"
    assert trials[0]["next_dt_s"] == pytest.approx(0.9)
    event_record = next(
        record
        for record in trials
        if str(record["event_cap_type"]).startswith("hard_mineral_exhaustion")
    )
    assert event_record["accepted"] is True
    assert event_record["controller_history_reset"] is True
    assert event_record["next_dt_s"] == 0.25
    assert progress["number_of_event_localizations"] == 1


def test_hard_exhaustion_endpoint_and_localization_limit(
    tmp_path: Path, monkeypatch
) -> None:
    endpoint_raw = _raw_case(
        tmp_path,
        duration_s=1.0,
        dt_initial_s=1.0,
        dt_max_s=1.0,
        absolute_tolerance_mol=10.0,
    )
    endpoint_raw["solver"]["timestep"]["events"]["hard_mineral_exhaustion"] = {
        "amount_tolerance": {"value": 0.1, "unit": "mol"},
        "time_tolerance": {"value": 0.001, "unit": "seconds"},
        "restart_dt": {"value": 0.25, "unit": "seconds"},
        "max_localizations": 2,
    }
    endpoint = _load(tmp_path, endpoint_raw)
    _state, _calls, _rows, history, progress = _run_fake(
        monkeypatch,
        endpoint,
        update=lambda value, dt: max(0.1, round(value - 0.9 * dt, 12)),
    )
    accepted = next(record for record in history if record["dt_s"] > 0.0)
    assert accepted["accepted"] is True
    assert accepted["controller_history_reset"] is True
    assert progress["number_of_event_localizations"] == 0

    limit_raw = _raw_case(
        tmp_path,
        duration_s=2.0,
        dt_initial_s=2.0,
        dt_max_s=2.0,
        absolute_tolerance_mol=10.0,
    )
    limit_raw["solver"]["timestep"]["events"]["hard_mineral_exhaustion"] = {
        "amount_tolerance": {"value": 0.1, "unit": "mol"},
        "time_tolerance": {"value": 0.001, "unit": "seconds"},
        "restart_dt": {"value": 0.25, "unit": "seconds"},
        "max_localizations": 1,
    }
    limit = _load(tmp_path, limit_raw)
    state, _calls, _rows, _history, progress = _run_fake(
        monkeypatch, limit, update=lambda value, dt: value - dt**0.5
    )
    assert state.value == 1.0
    assert progress["simulation_completed"] is False
    assert progress["termination_reason"] == "hard_event_localization_limit"


def test_inadmissible_state_rejects_independently_of_lte(
    tmp_path: Path, monkeypatch
) -> None:
    observation = {
        "pH": 7.0,
        "amounts": {"Quartz": -5.0e-13},
        "saturation_indices": {"Quartz": 0.0},
        "rates": {},
    }
    assert error_module._admissibility_error(observation, 1.0e-12) is None
    observation["amounts"]["Quartz"] = -2.0e-12
    assert error_module._admissibility_error(observation, 1.0e-12) is not None

    raw = _raw_case(
        tmp_path,
        dt_initial_s=0.1,
        dt_min_s=0.1,
        dt_max_s=0.1,
        absolute_tolerance_mol=10.0,
    )
    case = _load(tmp_path, raw)
    state, _calls, _rows, history, progress = _run_fake(
        monkeypatch, case, update=lambda _value, _dt: -1.0
    )
    trial = next(record for record in history if record["dt_s"] > 0.0)
    assert trial["richardson_error"] == 0.0
    assert trial["rejection_reason"] == "state_admissibility_rejection"
    assert trial["temporal_error_rejection"] is False
    assert state.value == 1.0
    assert progress["final_time_reached_s"] == 0.0


def test_soft_event_caps_only_the_subsequent_proposal(
    tmp_path: Path, monkeypatch
) -> None:
    raw = _raw_case(
        tmp_path,
        duration_s=1.0,
        dt_initial_s=0.25,
        dt_min_s=0.01,
        dt_max_s=1.0,
        absolute_tolerance_mol=10.0,
    )
    raw["solver"]["timestep"]["events"]["soft"] = {
        "timestep_cap_factor": 0.5,
        "saturation_index_crossing": False,
        "max_pH_change": 0.1,
        "secondary_mineral_appearance": None,
        "max_reaction_rate_relative_change": None,
        "reaction_rate_floor": None,
    }
    case = _load(tmp_path, raw)
    _state, _calls, _rows, history, progress = _run_fake(monkeypatch, case)
    accepted = [
        record for record in history if record["dt_s"] > 0.0 and record["accepted"]
    ]

    assert accepted[0]["effective_dt_s"] == 0.25
    assert accepted[1]["effective_dt_s"] == 0.125
    assert accepted[1]["event_cap_type"] == "soft_rapid_pH_change"
    assert all(record["temporal_error_rejection"] is False for record in accepted)
    assert progress["simulation_completed"] is True


def test_geochemical_event_detection_and_prediction_guards(tmp_path: Path) -> None:
    raw = _raw_case(tmp_path)
    with SOURCE_CASE_PATH.open(encoding="utf-8") as stream:
        source = yaml.safe_load(stream)
    raw["minerals"].append(
        next(mineral for mineral in source["minerals"] if mineral["name"] == "Gibbsite")
    )
    raw["solver"]["timestep"]["events"] = {
        "hard_mineral_exhaustion": {
            "amount_tolerance": {"value": 0.1, "unit": "mol"},
            "time_tolerance": {"value": 0.01, "unit": "seconds"},
            "restart_dt": {"value": 0.25, "unit": "seconds"},
            "max_localizations": 4,
        },
        "soft": {
            "timestep_cap_factor": 0.5,
            "saturation_index_crossing": True,
            "max_pH_change": 0.1,
            "secondary_mineral_appearance": {"value": 0.1, "unit": "mol"},
            "max_reaction_rate_relative_change": 0.2,
            "reaction_rate_floor": {"value": 0.01, "unit": "mol/s"},
        },
    }
    case = _load(tmp_path, raw)
    run = SimpleNamespace(timestep=case.config.solver.timestep, case=case)
    previous = {
        "time_s": 0.0,
        "pH": 7.0,
        "amounts": {"Quartz": 0.0, "Gibbsite": 0.0},
        "saturation_indices": {"Quartz": -1.0, "Gibbsite": -1.0},
        "rates": {"Quartz": 0.1},
    }
    current = {
        "time_s": 1.0,
        "pH": 7.2,
        "amounts": {"Quartz": 0.2, "Gibbsite": 0.2},
        "saturation_indices": {"Quartz": 1.0, "Gibbsite": -1.0},
        "rates": {"Quartz": 0.2},
    }
    assert set(error_module._soft_event_types(run, previous, current)) == {
        "soft_saturation_index_crossing:Quartz",
        "soft_rapid_pH_change",
        "soft_secondary_mineral_appearance:Gibbsite",
        "soft_rapid_reaction_rate_change:Quartz",
    }

    increasing = deepcopy(current)
    increasing["amounts"]["Quartz"] = 0.3
    assert error_module._predicted_event_cap(run, current, increasing, 1.0)[0] is None

    declining_previous = deepcopy(current)
    declining_previous["amounts"]["Quartz"] = 1.0
    declining_current = deepcopy(current)
    declining_current["time_s"] = 2.0
    declining_current["amounts"]["Quartz"] = 0.5
    cap, event_type, target = error_module._predicted_event_cap(
        run, declining_previous, declining_current, 1.0
    )
    assert cap == pytest.approx(0.8)
    assert event_type == "hard_mineral_exhaustion:Quartz"
    assert target == pytest.approx(2.8)


def test_property_loops_preserve_error_and_tolerance_monotonicity(
    tmp_path: Path,
) -> None:
    raw = _raw_case(tmp_path, absolute_tolerance_mol=0.1, relative_tolerance=0.2)
    config = CaseConfig.model_validate(raw).solver.timestep.error_control
    prior_error = -1.0
    for disagreement in (0.0, 1.0e-12, 1.0e-8, 1.0e-4, 1.0):
        value = error_module.richardson_error(
            {"Quartz": 1.0}, {"Quartz": 1.0 + disagreement}, config
        ).value
        assert value >= prior_error
        prior_error = value

    loose_raw = deepcopy(raw)
    loose_raw["solver"]["timestep"]["error_control"]["controlled_minerals"][0][
        "absolute_tolerance"
    ]["value"] = 1.0
    loose = CaseConfig.model_validate(loose_raw).solver.timestep.error_control
    assert error_module.richardson_error(
        {"Quartz": 1.0}, {"Quartz": 2.0}, loose
    ).value < error_module.richardson_error(
        {"Quartz": 1.0}, {"Quartz": 2.0}, config
    ).value


def test_real_reaktoro_native_solve_richardson_contract(tmp_path: Path) -> None:
    with ERROR_CASE_PATH.open(encoding="utf-8") as stream:
        raw = yaml.safe_load(stream)
    raw["case"]["name"] = "jayasekara_error_controlled_runtime_contract"
    raw["paths"]["output_dir"] = str(tmp_path / "real_outputs")
    raw["minerals"] = [
        mineral for mineral in raw["minerals"] if mineral["name"] == "Quartz"
    ]
    raw["postprocessing"]["requested_minerals"] = ["Quartz"]
    raw["solver"]["timestep"]["error_control"]["controlled_minerals"] = [
        mineral
        for mineral in raw["solver"]["timestep"]["error_control"][
            "controlled_minerals"
        ]
        if mineral["name"] == "Quartz"
    ]
    raw["solver"]["timestep"]["time"] = {
        "duration_value": 2700.0,
        "duration_unit": "seconds",
    }
    raw["solver"]["timestep"]["output_schedule"]["explicit_times"] = []
    raw["outputs"]["monitor"]["enabled"] = False
    raw["outputs"]["monitor"]["minerals"] = []
    raw["outputs"]["monitor"]["result_times"] = []
    path = tmp_path / "real_case.yaml"
    path.write_text(
        yaml.safe_dump(raw, sort_keys=False),
        encoding="utf-8",
    )
    code = """
import json
import os
import sys
from batch_runner.config import load_case
from batch_runner.simulator import run_simulation

result = run_simulation(load_case(sys.argv[1]))
history = list(result.iter_solver_history())
rows = list(result.iter_rows())
trials = [row for row in history if row["stage"] == "adaptive_error_controlled_trial"]
print(json.dumps({
    "completed": result.diagnostics["simulation_completed"],
    "final_time_s": result.diagnostics["final_time_reached_s"],
    "solve_calls": result.diagnostics["number_of_reaktoro_solve_calls"],
    "trial_count": len(trials),
    "recorded_calls": sum(row["reaktoro_solve_calls"] for row in history),
    "full": trials[-1]["full_step_succeeded"],
    "half1": trials[-1]["first_half_step_succeeded"],
    "half2": trials[-1]["second_half_step_succeeded"],
    "accepted": trials[-1]["accepted"],
    "initial_quartz_rate": rows[0]["reaction_rate_mol_s::Quartz"],
    "initial_quartz_amount": rows[0]["mineral_amount_mol::Quartz"],
}), flush=True)
os._exit(0)
"""
    completed = subprocess.run(
        [sys.executable, "-c", code, str(path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert completed.returncode == 0, completed.stderr
    observed = json.loads(completed.stdout)
    initial_quartz_rate = observed.pop("initial_quartz_rate")
    initial_quartz_amount = observed.pop("initial_quartz_amount")
    trial_count = observed.pop("trial_count")
    recorded_calls = observed.pop("recorded_calls")
    solve_calls = observed.pop("solve_calls")
    assert observed == {
        "completed": True,
        "final_time_s": 2700.0,
        "full": True,
        "half1": True,
        "half2": True,
        "accepted": True,
    }
    assert trial_count >= 1
    assert solve_calls == recorded_calls
    assert initial_quartz_amount == pytest.approx(4.26)
    assert isfinite(initial_quartz_rate)

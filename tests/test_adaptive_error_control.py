from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from batch_runner.config import (
    AdaptiveErrorControlConfig,
    AdaptiveEventControlConfig,
    AdaptiveStepSizeConfig,
    AdaptiveTimestepConfig,
    DurationConfig,
    OutputScheduleConfig,
    TimeValue,
)
from batch_runner.simulator.solver import adaptive as adaptive_module
from batch_runner.simulator.solver.adaptive_control import (
    EventSnapshot,
    controller_dt,
    error_rejection_dt,
    event_overshoot_correction,
    predict_event_limit,
    richardson_estimate,
)
from batch_runner.simulator.solver.runtime import SolverRun


class _Phase:
    def __init__(self, name: str):
        self._name = name

    def name(self) -> str:
        return self._name

    def species(self):
        return [self._name]


class _System:
    def phases(self):
        return [_Phase("Calcite")]


class _State:
    def __init__(self, species: float, mineral: float):
        self.species = species
        self.mineral = mineral
        self._system = _System()

    def system(self):
        return self._system

    def speciesAmount(self, key):
        if key == "Ca+2":
            return self.species
        if key == 0:
            return self.mineral
        raise KeyError(key)


class _ValueState:
    def __init__(self, value: float = 0.0):
        self.value = value

    def assign(self, other) -> None:
        self.value = other.value


class _Result:
    def succeeded(self) -> bool:
        return True

    def iterations(self) -> int:
        return 1


class _ScaleSolver:
    def __init__(self, scale: float):
        self.scale = scale

    def solve(self, state, dt_s):
        state.value += self.scale * dt_s
        return _Result()


def _case_for_error_control(relative: float = 0.0):
    error = AdaptiveErrorControlConfig(
        enabled=True,
        temporal_order=1.0,
        relative_tolerance=relative,
        species_absolute_tolerance_mol=0.01,
        mineral_absolute_tolerance_mol=0.01,
        controlled_species=["Ca+2"],
        controlled_minerals=["Calcite"],
    )
    return SimpleNamespace(
        config=SimpleNamespace(
            solver=SimpleNamespace(timestep=SimpleNamespace(error_control=error)),
            postprocessing=SimpleNamespace(requested_species=[]),
            minerals=[SimpleNamespace(name="Calcite", role="kinetic")],
        )
    )


def _adaptive_timestep() -> AdaptiveTimestepConfig:
    return AdaptiveTimestepConfig(
        mode="adaptive",
        time=DurationConfig(duration_value=1.0, duration_unit="seconds"),
        step_size=AdaptiveStepSizeConfig(
            dt_initial=TimeValue(value=0.5, unit="seconds"),
            dt_min=TimeValue(value=0.1, unit="seconds"),
            dt_max=TimeValue(value=1.0, unit="seconds"),
            growth_factor=2.0,
            shrink_factor=0.5,
            max_retries_per_step=3,
        ),
        output_schedule=OutputScheduleConfig(
            mode="explicit", include_initial=False, include_final=False
        ),
        error_control=AdaptiveErrorControlConfig(
            enabled=True,
            temporal_order=1.0,
        ),
    )


class _RunCase:
    def __init__(self):
        timestep = _adaptive_timestep()
        self.config = SimpleNamespace(solver=SimpleNamespace(timestep=timestep))
        self.duration_s = 1.0
        self.dt_initial_s = 0.5
        self.dt_min_s = 0.1
        self.dt_max_s = 1.0
        self.checkpoint_times_s = ()

    def output_times_s(self):
        return iter(())


def test_enabled_error_control_requires_demonstrated_temporal_order() -> None:
    with pytest.raises(ValidationError, match="temporal_order"):
        AdaptiveErrorControlConfig(enabled=True)


def test_event_control_cannot_replace_temporal_error_control() -> None:
    with pytest.raises(ValidationError, match="event_control requires error_control"):
        AdaptiveTimestepConfig(
            mode="adaptive",
            time=DurationConfig(duration_value=1.0, duration_unit="seconds"),
            step_size=AdaptiveStepSizeConfig(
                dt_initial=TimeValue(value=0.1, unit="seconds"),
                dt_min=TimeValue(value=0.01, unit="seconds"),
                dt_max=TimeValue(value=1.0, unit="seconds"),
                growth_factor=2.0,
                shrink_factor=0.5,
                max_retries_per_step=3,
            ),
            output_schedule=OutputScheduleConfig(
                mode="explicit", explicit_times=[]
            ),
            event_control=AdaptiveEventControlConfig(enabled=True),
        )


def test_richardson_estimate_uses_full_vs_two_half_states() -> None:
    case = _case_for_error_control()
    estimate = richardson_estimate(
        case,
        _State(species=1.0, mineral=2.0),
        _State(species=1.005, mineral=1.98),
    )

    # p=1 -> denominator 2**p - 1 = 1. Mineral error is 0.02/0.01 = 2.
    assert estimate.norm == pytest.approx(2.0)
    assert estimate.worst_variable == "mineral::Calcite"
    assert estimate.variable_count == 2


def test_controller_uses_startup_i_then_pi_and_bounds_change_factor() -> None:
    cfg = AdaptiveErrorControlConfig(enabled=True, temporal_order=1.0)

    grown, startup_kind = controller_dt(1.0, 0.01, None, cfg)
    assert startup_kind == "startup_i"
    assert 1.0 < grown <= cfg.max_growth_factor

    reduced, pi_kind = controller_dt(1.0, 2.0, 0.8, cfg)
    assert pi_kind == "pi"
    assert 0.0 < reduced < 1.0

    retry = error_rejection_dt(1.0, 1000.0, cfg)
    assert cfg.min_reduction_factor <= retry < 1.0


def test_event_prediction_uses_future_zero_crossing() -> None:
    cfg = AdaptiveEventControlConfig(enabled=True)
    previous = EventSnapshot(
        time_s=0.0,
        values={"amount::Calcite": 2.0, "si::Calcite": -2.0},
    )
    current = EventSnapshot(
        time_s=1.0,
        values={"amount::Calcite": 1.0, "si::Calcite": -1.0},
    )

    limit = predict_event_limit(previous, current, cfg)
    assert limit is not None
    assert limit.dt_s == pytest.approx(1.0)
    assert "zero_crossing" in limit.reason


def test_event_overshoot_interpolates_retry_interval() -> None:
    cfg = AdaptiveEventControlConfig(enabled=True)
    start = EventSnapshot(time_s=1.0, values={"si::Calcite": -1.0})
    trial = EventSnapshot(time_s=3.0, values={"si::Calcite": 3.0})

    correction = event_overshoot_correction(start, trial, 2.0, cfg)
    assert correction is not None
    assert correction.dt_s == pytest.approx(0.5)
    assert correction.reason == "corrected_zero_crossing:si::Calcite"


def test_error_controlled_loop_commits_two_half_step_state(monkeypatch) -> None:
    case = _RunCase()
    state = _ValueState()
    run = SolverRun(
        case=case,
        system=object(),
        state=state,
        emit_row=lambda _row: None,
        emit_record=lambda _record: None,
        emit_boundary=lambda _which, _row: None,
        emit_checkpoint=lambda _record, _state: None,
        is_cancelled=lambda: False,
        collect_row=lambda _case, current, record, _initial: {
            "time_s": record["time_end_s"],
            "value": current.value,
        },
        snapshot_state=deepcopy,
    )
    run.kinetic_solver = _ScaleSolver(scale=2.0)
    run.richardson_half_solver = _ScaleSolver(scale=1.0)
    run.initial_state = deepcopy(state)

    monkeypatch.setattr(
        adaptive_module,
        "richardson_estimate",
        lambda *_args: SimpleNamespace(
            norm=0.25,
            worst_variable="fake",
            worst_ratio=0.25,
        ),
    )
    monkeypatch.setattr(
        adaptive_module,
        "event_snapshot",
        lambda _case, _state, time_s: EventSnapshot(time_s=time_s, values={}),
    )

    _initial, progress = adaptive_module.run_adaptive_timesteps(run)

    assert state.value == pytest.approx(1.0)
    assert progress["simulation_completed"] is True
    assert progress["number_of_kinetic_solve_calls"] == 6
    assert progress["number_of_accepted_steps"] == 2

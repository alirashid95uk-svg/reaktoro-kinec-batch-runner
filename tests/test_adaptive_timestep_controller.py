import json
import math
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
import reaktoro as rkt
import yaml
from pydantic import ValidationError

from batch_runner.config import CaseConfig, load_case
from batch_runner.simulator import acceptance as acceptance_module
from batch_runner.simulator import solver as solver_module
from batch_runner.simulator.acceptance import evaluate_trial
from batch_runner.simulator.state_snapshot import snapshot_state


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
    mode: str = "adaptive",
) -> dict:
    with SOURCE_CASE_PATH.open(encoding="utf-8") as stream:
        raw = yaml.safe_load(stream)
    raw["paths"]["output_dir"] = str(tmp_path / "outputs")
    raw["solver"]["workflow"]["precondition_kinetics"] = False
    raw["solver"]["timestep"] = {
        "mode": mode,
        "time": {"duration_value": duration_s, "duration_unit": "seconds"},
        "step_size": {
            "dt_initial": {"value": dt_initial_s, "unit": "seconds"},
            "dt_min": {"value": dt_min_s, "unit": "seconds"},
            "dt_max": {"value": dt_max_s, "unit": "seconds"},
            "growth_factor": 2.0,
            "shrink_factor": 0.5,
            "max_retries_per_step": max_retries,
        },
        "acceptance": {
            "enabled": True,
            "fail_on_non_finite": True,
            "fail_on_negative_amounts": True,
            "max_delta_pH": None,
            "max_delta_saturation_index": None,
            "max_selected_species_fraction_change": None,
            "max_mineral_fraction_change": None,
            "element_conservation": {
                "enabled": False,
                "relative_tolerance": None,
                "absolute_tolerance_mol": None,
            },
            "max_relative_rate_change": None,
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
    raw["solver"]["restart"] = {"enabled": False, "from_checkpoint": None}
    return raw


def _load_adaptive_case(tmp_path: Path, raw: dict):
    path = tmp_path / "case.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return load_case(path)


class _FakeResult:
    def __init__(self, succeeded: bool = True):
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
    accept_results: list[bool] | None = None,
    solve=None,
    initial_value: float = 0.0,
):
    calls: list[float] = []
    accepted = iter(accept_results) if accept_results is not None else None

    class FakeSolver:
        def solve(self, state, dt_s):
            calls.append(dt_s)
            if solve is None:
                state.value += dt_s
            else:
                solve(state, dt_s)
            return _FakeResult()

    monkeypatch.setattr(solver_module.rkt, "KineticsSolver", lambda _system: FakeSolver())
    monkeypatch.setattr(solver_module, "build_conditions", lambda *_args: (None, None))
    monkeypatch.setattr(solver_module, "snapshot_state", deepcopy)
    monkeypatch.setattr(
        solver_module,
        "collect_row",
        lambda _case, state, record, _initial, _params: {
            "time_s": record["time_end_s"],
            "state_value": state.value,
        },
    )

    def fake_acceptance(*_args):
        accepted_now = next(accepted) if accepted is not None else True
        return {
            **solver_module._empty_acceptance("accepted" if accepted_now else "forced_rejection"),
            "accepted": accepted_now,
        }

    monkeypatch.setattr(solver_module, "evaluate_trial", fake_acceptance)
    state = _FakeState(initial_value)
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
    )
    return state, calls, rows, history, checkpoints, progress


def test_rejected_trial_restores_state_and_retries_from_accepted_time(
    tmp_path: Path, monkeypatch
) -> None:
    case = _load_adaptive_case(tmp_path, _raw_adaptive_case(tmp_path))
    state, calls, rows, history, _checkpoints, progress = _run_fake(
        monkeypatch,
        case,
        accept_results=[False, True, True],
    )

    assert calls == [1.0, 0.5, 0.5]
    assert history[0]["accepted"] is False
    assert history[0]["time_start_s"] == history[0]["time_end_s"] == 0.0
    assert [record["time_end_s"] for record in history[1:]] == [0.5, 1.0]
    assert [row["time_s"] for row in rows] == [0.0, 1.0]
    assert state.value == 1.0
    assert progress["number_of_rejected_steps"] == 1
    assert progress["final_time_reached_s"] == case.duration_s
    assert progress["simulation_completed"] is True


def test_adaptive_steps_land_exactly_on_output_and_checkpoint_targets(
    tmp_path: Path, monkeypatch
) -> None:
    raw = _raw_adaptive_case(tmp_path, dt_initial_s=0.7, dt_min_s=0.1)
    raw["solver"]["timestep"]["output_schedule"]["explicit_times"] = [
        {"value": 0.3, "unit": "seconds"},
        {"value": 0.8, "unit": "seconds"},
    ]
    raw["solver"]["timestep"]["checkpoint_schedule"] = {
        "enabled": True,
        "times": [{"value": 0.5, "unit": "seconds"}],
    }
    case = _load_adaptive_case(tmp_path, raw)
    _state, calls, rows, history, checkpoints, progress = _run_fake(monkeypatch, case)

    assert calls == pytest.approx([0.3, 0.2, 0.3, 0.2])
    assert [record["time_end_s"] for record in history] == [0.3, 0.5, 0.8, 1.0]
    assert [row["time_s"] for row in rows] == [0.0, 0.3, 0.8, 1.0]
    assert checkpoints == [(0.5, 0.5)]
    assert progress["final_time_reached_s"] == case.duration_s


@pytest.mark.parametrize(
    ("dt_initial", "dt_min", "max_retries", "expected_attempts", "termination"),
    [
        (1.0, 0.25, 1, [1.0, 0.5], "retry_limit_exceeded"),
        (0.25, 0.25, 99, [0.25], "minimum_timestep_rejected"),
    ],
)
def test_adaptive_rejection_limits_terminate_without_advancing_time(
    tmp_path: Path,
    monkeypatch,
    dt_initial: float,
    dt_min: float,
    max_retries: int,
    expected_attempts: list[float],
    termination: str,
) -> None:
    raw = _raw_adaptive_case(
        tmp_path,
        dt_initial_s=dt_initial,
        dt_min_s=dt_min,
        max_retries=max_retries,
    )
    case = _load_adaptive_case(tmp_path, raw)
    state, calls, rows, history, _checkpoints, progress = _run_fake(
        monkeypatch,
        case,
        accept_results=[False] * len(expected_attempts),
    )

    assert calls == expected_attempts
    assert state.value == 0.0
    assert [row["time_s"] for row in rows] == [0.0]
    assert all(record["time_end_s"] == 0.0 for record in history)
    assert progress["termination_reason"] == termination
    assert progress["accepted_state_restored"] is True


def test_max_internal_steps_counts_attempts_and_preserves_partial_state(
    tmp_path: Path, monkeypatch
) -> None:
    raw = _raw_adaptive_case(
        tmp_path,
        dt_initial_s=0.2,
        dt_min_s=0.1,
        dt_max_s=0.2,
        max_internal_steps=2,
    )
    case = _load_adaptive_case(tmp_path, raw)
    state, calls, rows, _history, _checkpoints, progress = _run_fake(monkeypatch, case)

    assert calls == [0.2, 0.2]
    assert state.value == pytest.approx(0.4)
    assert [row["time_s"] for row in rows] == [0.0]
    assert progress["termination_reason"] == "max_internal_steps_exceeded"
    assert progress["number_of_internal_attempts"] == 2
    assert progress["final_time_reached_s"] == 0.4


def test_controlled_timestep_refinement_reduces_forward_euler_error(
    tmp_path: Path, monkeypatch
) -> None:
    errors = []
    for steps in (1, 2, 4):
        dt = 1.0 / steps
        raw = _raw_adaptive_case(
            tmp_path,
            dt_initial_s=dt,
            dt_min_s=dt,
            dt_max_s=dt,
            max_internal_steps=steps,
        )
        raw["paths"]["output_dir"] = str(tmp_path / f"outputs_{steps}")
        case = _load_adaptive_case(tmp_path, raw)
        state, _calls, _rows, _history, _checkpoints, progress = _run_fake(
            monkeypatch,
            case,
            solve=lambda trial, step: setattr(trial, "value", trial.value * (1.0 - step)),
            initial_value=1.0,
        )
        assert progress["simulation_completed"] is True
        errors.append(abs(state.value - math.exp(-1.0)))

    assert errors[0] > errors[1] > errors[2]


def test_adaptive_long_horizon_requires_sparse_output_final_and_checkpoints(
    tmp_path: Path,
) -> None:
    raw = _raw_adaptive_case(tmp_path, mode="adaptive_long_horizon")
    with pytest.raises(ValidationError, match="checkpoint_schedule.enabled"):
        CaseConfig.model_validate(raw)

    raw["solver"]["timestep"]["checkpoint_schedule"] = {
        "enabled": True,
        "times": [{"value": 0.5, "unit": "seconds"}],
    }
    case = _load_adaptive_case(tmp_path, raw)
    assert case.config.solver.timestep.mode == "adaptive_long_horizon"


def test_unverified_rate_criterion_and_restart_are_rejected(tmp_path: Path) -> None:
    raw = _raw_adaptive_case(tmp_path)
    raw["solver"]["timestep"]["acceptance"]["max_relative_rate_change"] = 0.1
    with pytest.raises(ValidationError, match="rate-based adaptive acceptance is not verified"):
        CaseConfig.model_validate(raw)

    raw = _raw_adaptive_case(tmp_path)
    raw["solver"]["restart"] = {"enabled": True, "from_checkpoint": "state.txt"}
    with pytest.raises(ValidationError, match="automatic restart is not implemented or validated"):
        CaseConfig.model_validate(raw)


class _AcceptanceState:
    def __init__(
        self,
        *,
        species: list[float],
        named: dict[str, float],
        elements: list[float],
        pH: float,
        si: dict[str, float],
    ):
        self.species = species
        self.named = named
        self.elements = elements
        self.pH = pH
        self.si = si

    def speciesAmounts(self):
        return self.species

    def speciesAmount(self, name):
        return self.named[name]

    def elementAmounts(self):
        return self.elements

    def temperature(self):
        return 298.15

    def pressure(self):
        return 1.0e5

    def charge(self):
        return 0.0


class _AqueousProps:
    def __init__(self, state):
        self.state = state

    def pH(self):
        return self.state.pH

    def saturationIndex(self, name):
        return self.state.si[name]


class _Element:
    def name(self):
        return "C"


def test_acceptance_checks_reject_solver_success_on_configured_state_changes(
    tmp_path: Path, monkeypatch
) -> None:
    raw = _raw_adaptive_case(tmp_path)
    checks = raw["solver"]["timestep"]["acceptance"]
    checks.update(
        {
            "max_delta_pH": 0.1,
            "max_delta_saturation_index": 0.1,
            "max_selected_species_fraction_change": 0.1,
            "max_mineral_fraction_change": 0.1,
            "element_conservation": {
                "enabled": True,
                "relative_tolerance": 1.0e-6,
                "absolute_tolerance_mol": 1.0e-9,
            },
        }
    )
    case = _load_adaptive_case(tmp_path, raw)
    accepted = _AcceptanceState(
        species=[1.0, 2.0],
        named={"H+": 1.0, "HCO3-": 1.0, "CO3-2": 1.0, "Calcite": 1.0},
        elements=[1.0],
        pH=7.0,
        si={"Calcite": -1.0},
    )
    trial = _AcceptanceState(
        species=[-1.0e-12, 2.0],
        named={"H+": 2.0, "HCO3-": 1.0, "CO3-2": 1.0, "Calcite": 0.5},
        elements=[1.2],
        pH=8.0,
        si={"Calcite": 0.0},
    )
    monkeypatch.setattr(acceptance_module.rkt, "AqueousProps", _AqueousProps)

    observed = evaluate_trial(case, type("System", (), {"elements": lambda _self: [_Element()]})(), accepted, trial)

    assert observed["accepted"] is False
    assert set(observed["acceptance_reason"].split(";")) == {
        "negative_species_amount",
        "max_delta_pH",
        "max_delta_saturation_index",
        "max_selected_species_fraction_change",
        "max_mineral_fraction_change",
        "element_conservation",
    }


def test_non_finite_trial_state_is_rejected(tmp_path: Path, monkeypatch) -> None:
    case = _load_adaptive_case(tmp_path, _raw_adaptive_case(tmp_path))
    accepted = _AcceptanceState(
        species=[1.0], named={}, elements=[], pH=7.0, si={"Calcite": 0.0}
    )
    trial = _AcceptanceState(
        species=[float("nan")], named={}, elements=[], pH=7.0, si={"Calcite": 0.0}
    )
    monkeypatch.setattr(acceptance_module.rkt, "AqueousProps", _AqueousProps)

    observed = evaluate_trial(case, object(), accepted, trial)

    assert observed["accepted"] is False
    assert observed["acceptance_reason"] == "non_finite_state_value"


def test_reaktoro_copy_assign_reconstruction_and_substep_consistency() -> None:
    code = """
import json
import os
import reaktoro as rkt

def rate(_props: rkt.ChemicalProps) -> rkt.ReactionRate:
    return rkt.ReactionRate(1.0e-8)

database = rkt.PhreeqcDatabase.withName("phreeqc.dat")
aqueous = rkt.AqueousPhase(rkt.speciate(["H", "O", "C", "Ca"]))
aqueous.setActivityModel(rkt.ActivityModelPhreeqc(database))
reaction = rkt.MineralReaction("Calcite")
reaction.setRateModel(rkt.ReactionRateModel(rate))
system = rkt.ChemicalSystem(database, aqueous, rkt.MineralPhases(["Calcite"]), reaction)
state = rkt.ChemicalState(system)
state.temperature(25.0, "celsius")
state.pressure(1.0, "bar")
state.set("H2O", 1.0, "kg")
state.set("Calcite", 1.0, "mol")
snapshot = rkt.ChemicalState(state)
one = rkt.ChemicalState(state)
two = rkt.ChemicalState(state)
one_result = rkt.KineticsSolver(system).solve(one, 1.0)
two_solver = rkt.KineticsSolver(system)
two_results = [two_solver.solve(two, 0.5).succeeded(), two_solver.solve(two, 0.5).succeeded()]
state.set("Calcite", 0.5, "mol")
state.assign(snapshot)
reconstructed = rkt.ChemicalState(system)
reconstructed.temperature(float(snapshot.temperature()), "kelvin")
reconstructed.pressure(float(snapshot.pressure()), "pascal")
reconstructed.setSpeciesAmounts(snapshot.speciesAmounts())
print(json.dumps({
    "version": rkt.__version__,
    "solves_succeeded": one_result.succeeded() and all(two_results),
    "copy_restored": float(state.speciesAmount("Calcite")) == float(snapshot.speciesAmount("Calcite")),
    "reconstruction_max_species_difference": max(abs(float(a) - float(b)) for a, b in zip(snapshot.speciesAmounts(), reconstructed.speciesAmounts())),
    "reconstruction_max_element_difference": max(abs(float(a) - float(b)) for a, b in zip(snapshot.elementAmounts(), reconstructed.elementAmounts())),
    "one_substep_calcite_difference_mol": abs(float(one.speciesAmount("Calcite")) - float(two.speciesAmount("Calcite"))),
}), flush=True)
os._exit(0)
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    observed = json.loads(completed.stdout)
    assert observed["version"] == "2.13.0"
    assert observed["solves_succeeded"] is True
    assert observed["copy_restored"] is True
    assert observed["reconstruction_max_species_difference"] == 0.0
    assert observed["reconstruction_max_element_difference"] == 0.0
    assert observed["one_substep_calcite_difference_mol"] < 1.0e-12

from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from batch_runner.config import CaseConfig, load_case
from batch_runner.simulator.chemistry import conditions as state_builder_module
from batch_runner.simulator.solver import calls as solver_calls_module
from batch_runner.simulator.solver import equilibrium as equilibrium_module
from batch_runner.simulator.solver import execution as solver_module


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_CASE_PATH = (
    PROJECT_ROOT / "tests" / "fixtures" / "cases" / "synthetic_kinec_case.yaml"
)


def _read_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def _write_case(tmp_path: Path, raw: dict) -> Path:
    raw["paths"]["output_dir"] = str(tmp_path / "outputs")
    path = tmp_path / "case.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


def _fixed_fugacity_case(workflow: str) -> dict:
    """Return a synthetic config variant for solver-routing tests only."""

    raw = _read_yaml(SYNTHETIC_CASE_PATH)
    raw["co2"] = {
        "mode": "fixed_fugacity",
        "gas_species": "CO2(g)",
        "fugacity_bar": 1.0,
    }
    raw["solver"]["workflow"] = {"mode": workflow}
    return raw


def test_workflow_mode_can_change_without_mutating_other_config_values() -> None:
    staged = _fixed_fugacity_case(
        "fixed_fugacity_initial_equilibrium_then_closed_kinetics"
    )
    constrained_variant = deepcopy(staged)
    constrained_variant["solver"]["workflow"] = {
        "mode": "fixed_fugacity_during_kinetic_steps",
    }

    CaseConfig.model_validate(staged)
    CaseConfig.model_validate(constrained_variant)
    constrained_variant["solver"]["workflow"] = staged["solver"]["workflow"]
    assert constrained_variant == staged


def test_constrained_kinetic_workflow_requires_fixed_fugacity_co2() -> None:
    raw = _fixed_fugacity_case("fixed_fugacity_during_kinetic_steps")
    raw["co2"] = {"mode": "disabled"}

    with pytest.raises(ValidationError, match="fixed_fugacity"):
        CaseConfig.model_validate(raw)


def test_constrained_kinetic_conditions_include_configured_co2_fugacity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    case = load_case(
        _write_case(
            tmp_path,
            _fixed_fugacity_case("fixed_fugacity_during_kinetic_steps"),
        )
    )
    calls = []

    class FakeSpecs:
        def fugacity(self, species):
            calls.append(("specs.fugacity", species))

        def pE(self):
            calls.append(("specs.pE",))

    class FakeSpecsType:
        @staticmethod
        def TP(system):
            calls.append(("EquilibriumSpecs.TP", system))
            return FakeSpecs()

    class FakeConditions:
        def __init__(self, specs):
            calls.append(("EquilibriumConditions", specs))

        def temperature(self, value, unit):
            calls.append(("temperature", value, unit))

        def pressure(self, value, unit):
            calls.append(("pressure", value, unit))

        def fugacity(self, species, value, unit):
            calls.append(("conditions.fugacity", species, value, unit))

        def pE(self, value):
            calls.append(("conditions.pE", value))

        def setInitialComponentAmountsFromState(self, state):
            calls.append(("setInitialComponentAmountsFromState", state))

    monkeypatch.setattr(state_builder_module.rkt, "EquilibriumSpecs", FakeSpecsType)
    monkeypatch.setattr(state_builder_module.rkt, "EquilibriumConditions", FakeConditions)
    system = object()
    state = object()

    specs, conditions = state_builder_module.build_conditions(case, system, state, "kinetic_steps")

    assert specs is not None
    assert conditions is not None
    assert ("specs.fugacity", "CO2(g)") in calls
    assert ("conditions.fugacity", "CO2(g)", 1.0, "bar") in calls
    assert not any(call[0] == "conditions.pE" for call in calls)


class _FakeResult:
    def succeeded(self) -> bool:
        return True

    def iterations(self) -> int:
        return 1


class _FakeSolver:
    def __init__(self, constructor_arg, events):
        self.constructor_arg = constructor_arg
        self.events = events

    def solve(self, *args):
        self.events.append(("solve", args))
        return _FakeResult()


def _run_with_solver_spy(monkeypatch, case):
    events = []
    constructors = []
    system = object()
    state = object()
    initial_specs = object()
    initial_conditions = object()
    kinetic_specs = object()
    kinetic_conditions = object()

    def fake_equilibrium_solver(arg):
        constructors.append(("EquilibriumSolver", arg))
        return _FakeSolver(arg, events)

    def fake_kinetics_solver(arg):
        constructors.append(("KineticsSolver", arg))
        return _FakeSolver(arg, events)

    def fake_build_conditions(_case, _system, _state, stage):
        if stage == "initial_equilibrium":
            return initial_specs, initial_conditions
        if case.config.solver.workflow.mode == "fixed_fugacity_during_kinetic_steps":
            return kinetic_specs, kinetic_conditions
        return None, None

    monkeypatch.setattr(solver_calls_module.rkt, "EquilibriumSolver", fake_equilibrium_solver)
    monkeypatch.setattr(solver_calls_module.rkt, "KineticsSolver", fake_kinetics_solver)
    monkeypatch.setattr(solver_module, "build_conditions", fake_build_conditions)
    monkeypatch.setattr(equilibrium_module, "build_conditions", fake_build_conditions)
    monkeypatch.setattr(solver_module, "snapshot_state", lambda value: value)
    monkeypatch.setattr(solver_module, "collect_row", lambda *_args: {"time_s": 0.0})

    solver_module.execute_solver(case, system, state)
    return {
        "constructors": constructors,
        "events": events,
        "system": system,
        "state": state,
        "initial_specs": initial_specs,
        "initial_conditions": initial_conditions,
        "kinetic_specs": kinetic_specs,
        "kinetic_conditions": kinetic_conditions,
    }


def test_constrained_workflow_uses_kinetics_solver_specs_and_passes_conditions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    case = load_case(
        _write_case(
            tmp_path,
            _fixed_fugacity_case("fixed_fugacity_during_kinetic_steps"),
        )
    )
    observed = _run_with_solver_spy(monkeypatch, case)

    assert observed["constructors"] == [("KineticsSolver", observed["kinetic_specs"])]
    assert (
        "solve",
        (observed["state"], case.dt_s, observed["kinetic_conditions"]),
    ) in observed["events"]


def test_staged_workflow_preserves_initial_equilibrium_then_closed_kinetics(
    tmp_path: Path,
    monkeypatch,
) -> None:
    case = load_case(
        _write_case(
            tmp_path,
            _fixed_fugacity_case(
                "fixed_fugacity_initial_equilibrium_then_closed_kinetics"
            ),
        )
    )
    observed = _run_with_solver_spy(monkeypatch, case)

    assert observed["constructors"] == [
        ("EquilibriumSolver", observed["initial_specs"]),
        ("KineticsSolver", observed["system"]),
    ]
    assert ("solve", (observed["state"], observed["initial_conditions"])) in observed["events"]
    assert ("solve", (observed["state"], case.dt_s)) in observed["events"]
    assert all(
        event != (
            "solve",
            (observed["state"], case.dt_s, observed["kinetic_conditions"]),
        )
        for event in observed["events"]
    )

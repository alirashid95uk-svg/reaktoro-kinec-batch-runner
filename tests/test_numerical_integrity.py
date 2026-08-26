from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from batch_runner.config import load_case
from batch_runner.integrity_monitor import IntegritySimulationMonitor
from batch_runner.simulator import run_simulation
from batch_runner.simulator.integrity import NumericalIntegrityObserver


class _Element:
    def __init__(self, symbol: str) -> None:
        self._symbol = symbol

    def symbol(self) -> str:
        return self._symbol


class _Composition:
    def __init__(self, symbols: list[str]) -> None:
        self._symbols = symbols

    def symbols(self) -> list[str]:
        return self._symbols


class _Species:
    def __init__(self, symbols: list[str], charge: float = 0.0) -> None:
        self._elements = _Composition(symbols)
        self._charge = charge

    def elements(self) -> _Composition:
        return self._elements

    def charge(self) -> float:
        return self._charge


class _Database:
    def __init__(self, species: dict[str, _Species]) -> None:
        self._species = species

    def species(self, name: str) -> _Species:
        return self._species[name]


class _System:
    def __init__(
        self,
        elements: list[str],
        species: list[_Species],
        database_species: dict[str, _Species] | None = None,
    ) -> None:
        self._elements = [_Element(symbol) for symbol in elements]
        self._species = species
        self._database = _Database(database_species or {})

    def elements(self):
        return self._elements

    def species(self):
        return self._species

    def database(self) -> _Database:
        return self._database


class _State:
    def __init__(
        self,
        system: _System,
        components: list[float],
        species_amounts: list[float],
        charge: float,
    ) -> None:
        self._system = system
        self._components = components
        self._species_amounts = species_amounts
        self._charge = charge

    def system(self) -> _System:
        return self._system

    def componentAmounts(self):
        return self._components

    def speciesAmounts(self):
        return self._species_amounts

    def charge(self) -> float:
        return self._charge


def _case(*, fixed_fugacity: bool = False, redox: bool = False):
    return SimpleNamespace(
        config=SimpleNamespace(
            solver=SimpleNamespace(
                workflow=SimpleNamespace(
                    mode=(
                        "fixed_fugacity_during_kinetic_steps"
                        if fixed_fugacity
                        else "closed_kinetics"
                    )
                )
            ),
            co2=SimpleNamespace(
                mode="fixed_fugacity" if fixed_fugacity else "disabled",
                gas_species="CO2(g)",
            ),
            redox=SimpleNamespace(
                enabled=redox,
                apply_during="kinetic_steps",
            ),
        )
    )


def test_closed_components_carbon_and_charge_are_diagnostic() -> None:
    system = _System(
        ["H", "C", "O", "Na"],
        [_Species(["Na"], +1), _Species(["C", "O"], -1)],
    )
    initial = _State(system, [100.0, 1.0, 50.0, 2.0, 0.0], [2.0, 2.0], 0.0)
    current = _State(
        system,
        [100.0 + 1.0e-10, 1.0 + 2.0e-12, 50.0, 2.0, 1.0e-13],
        [2.0, 2.0],
        1.0e-13,
    )
    observer = NumericalIntegrityObserver(_case())

    reference = observer.observe(initial, time_s=0.0, initialize=True)
    snapshot = observer.observe(current, time_s=10.0)

    assert reference["material_balance"]["max_relative_residual"] == 0.0
    assert snapshot["status"] == "evaluated"
    assert snapshot["material_balance"]["worst_component"] == "C"
    assert snapshot["material_balance"]["max_relative_residual"] == pytest.approx(2.0e-12)
    assert snapshot["material_balance"]["cumulative_max_relative_residual"] == pytest.approx(2.0e-12)
    assert snapshot["carbon"]["status"] == "evaluated"
    assert snapshot["carbon"]["relative_residual"] == pytest.approx(2.0e-12)
    assert snapshot["charge"]["status"] == "evaluated"
    assert snapshot["charge"]["residual_mol"] == pytest.approx(1.0e-13)



def test_zero_reference_component_uses_absolute_not_invented_relative_scale() -> None:
    system = _System(["H", "C"], [_Species(["H"], +1)])
    initial = _State(system, [1.0, 0.0, 0.0], [1.0], 0.0)
    current = _State(system, [1.0, 2.0e-12, 0.0], [1.0], 0.0)
    observer = NumericalIntegrityObserver(_case())

    observer.observe(initial, time_s=0.0, initialize=True)
    snapshot = observer.observe(current, time_s=1.0)

    assert snapshot["material_balance"]["zero_reference_component_count"] == 1
    assert snapshot["material_balance"]["zero_reference_max_absolute_residual_mol"] == pytest.approx(2.0e-12)
    assert snapshot["carbon"]["relative_residual"] is None
    assert snapshot["carbon"]["residual_mol"] == pytest.approx(2.0e-12)


def test_charge_residual_is_absolute_charge_not_only_drift_from_reference() -> None:
    system = _System(["H"], [_Species(["H"], +1)])
    initial = _State(system, [1.0, 1.0e-6], [1.0], 1.0e-6)
    current = _State(system, [1.0, 1.1e-6], [1.0], 1.1e-6)
    observer = NumericalIntegrityObserver(_case())

    observer.observe(initial, time_s=0.0, initialize=True)
    snapshot = observer.observe(current, time_s=1.0)

    assert snapshot["charge"]["residual_mol"] == pytest.approx(1.1e-6)
    assert snapshot["charge"]["drift_from_reference_mol"] == pytest.approx(1.0e-7)

def test_fixed_fugacity_excludes_only_titrant_elements_and_marks_carbon_open() -> None:
    co2 = _Species(["C", "O"], 0.0)
    system = _System(
        ["H", "C", "O", "Na"],
        [_Species(["Na"], +1)],
        {"CO2(g)": co2},
    )
    initial = _State(system, [100.0, 1.0, 50.0, 2.0, 0.0], [2.0], 0.0)
    current = _State(system, [100.0, 5.0, 58.0, 2.0 + 4.0e-12, 0.0], [2.0], 0.0)
    observer = NumericalIntegrityObserver(_case(fixed_fugacity=True))

    observer.observe(initial, time_s=0.0, initialize=True)
    snapshot = observer.observe(current, time_s=10.0)

    assert snapshot["open_elements"] == ["C", "O"]
    assert snapshot["carbon"]["status"] == "open_boundary"
    assert snapshot["material_balance"]["worst_component"] == "Na"
    assert snapshot["material_balance"]["max_relative_residual"] == pytest.approx(2.0e-12)


def test_redox_constraint_marks_charge_open_without_hiding_element_balance() -> None:
    system = _System(["H", "O"], [_Species(["H"], +1)])
    initial = _State(system, [10.0, 5.0, 0.0], [1.0], 0.0)
    current = _State(system, [10.0, 5.0, 0.1], [1.0], 0.1)
    observer = NumericalIntegrityObserver(_case(redox=True))

    observer.observe(initial, time_s=0.0, initialize=True)
    snapshot = observer.observe(current, time_s=1.0)

    assert snapshot["material_balance"]["max_relative_residual"] == 0.0
    assert snapshot["charge"]["status"] == "open_boundary"


def test_diagnostic_failure_is_contained_not_raised() -> None:
    system = _System(["H", "C", "O"], [], {})
    state = _State(system, [10.0, 1.0, 5.0, 0.0], [], 0.0)
    observer = NumericalIntegrityObserver(_case(fixed_fugacity=True))

    snapshot = observer.observe(state, time_s=0.0, initialize=True)

    assert snapshot["status"] == "unavailable"
    assert observer.unavailable_reason is not None
    assert "CO2(g)" in observer.unavailable_reason


def _monitor_case(tmp_path: Path):
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
            case=SimpleNamespace(name="integrity-monitor"),
            database=SimpleNamespace(name="test.dat"),
            physical=SimpleNamespace(temperature_c=25.0, pressure_bar=1.0),
        ),
    )


def _integrity_snapshot(time_s: float) -> dict:
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


def test_monitor_displays_and_logs_numerical_integrity(tmp_path: Path) -> None:
    case = _monitor_case(tmp_path)
    stream = io.StringIO()
    monitor = IntegritySimulationMonitor(case, display_enabled=True, stream=stream)
    monitor.activate_log()
    monitor.handle_numerical_integrity(_integrity_snapshot(20.0))
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
    monitor.handle_accepted_row({"time_s": 20.0})

    text = stream.getvalue()
    assert "Numerical integrity @ 20 s" in text
    assert "component max 3.2e-10 rel (Ca)" in text
    assert "cumulative max 7.1e-10" in text
    assert "carbon open boundary" in text
    assert "charge 2.4e-12 mol" in text

    log = monitor.log_path.read_text(encoding="utf-8")
    assert "Numerical integrity at 20 s" in log
    assert "component max=3.2e-10" in log


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_CASE = (
    PROJECT_ROOT / "tests" / "fixtures" / "cases" / "synthetic_kinec_case.yaml"
)


def _synthetic_case(tmp_path: Path, name: str):
    raw = yaml.safe_load(SOURCE_CASE.read_text(encoding="utf-8"))
    raw["paths"]["output_dir"] = str(tmp_path / name)
    raw["kinetics"] = {"enabled": True, "model": "palandri_kharaka"}
    raw["solver"]["timestep"]["time"] = {
        "duration_value": 20.0,
        "duration_unit": "seconds",
    }
    raw["solver"]["timestep"]["step_size"] = {
        "dt": {"value": 10.0, "unit": "seconds"}
    }
    path = tmp_path / f"{name}.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return load_case(path)


def test_observer_callback_is_numerically_invariant_on_synthetic_case(tmp_path: Path) -> None:
    baseline_case = _synthetic_case(tmp_path, "integrity-off")
    observed_case = _synthetic_case(tmp_path, "integrity-on")

    baseline = run_simulation(baseline_case)
    observer = NumericalIntegrityObserver(observed_case)
    snapshots: list[dict] = []

    def accepted_state_ready(state, record: dict) -> None:
        snapshots.append(
            observer.observe(
                state,
                time_s=float(record["time_end_s"]),
                initialize=record["stage"] == "initial_state",
            )
        )

    observed = run_simulation(
        observed_case,
        accepted_state_ready=accepted_state_ready,
    )

    baseline_rows = list(baseline.iter_rows())
    observed_rows = list(observed.iter_rows())
    baseline_history = list(baseline.iter_solver_history())
    observed_history = list(observed.iter_solver_history())

    assert baseline.diagnostics["simulation_completed"] is True
    assert observed.diagnostics["simulation_completed"] is True
    assert baseline_rows == observed_rows
    assert [
        {key: value for key, value in record.items() if key != "wall_time_s"}
        for record in baseline_history
    ] == [
        {key: value for key, value in record.items() if key != "wall_time_s"}
        for record in observed_history
    ]
    assert observer.summary()["status"] == "evaluated"
    assert len(snapshots) == observed.diagnostics["number_of_accepted_steps"] + 1

    baseline.cleanup_streams()
    observed.cleanup_streams()

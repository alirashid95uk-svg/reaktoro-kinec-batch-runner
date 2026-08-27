import json
from math import isfinite
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest
import reaktoro as rkt
import yaml

from batch_runner.config import load_case
from batch_runner.simulator import prepare_simulation
from batch_runner.simulator.chemistry import build_chemical_system, load_database
from batch_runner.simulator.chemistry.conditions import build_conditions
from batch_runner.simulator.chemistry.observations import collect_reaction_rate_fields
from batch_runner.simulator.kinetics import load_kinetic_parameters
from batch_runner.simulator.simulation import (
    _copy_state_values_to_system,
    _isolated_initial_reaction_rate_fields,
)
from batch_runner.simulator.solver.calls import kinetics_solver


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPECIES_CASE = PROJECT_ROOT / "cases" / "jayasekara_2020_reproduction_monitor.yaml"
ELEMENT_CASE = PROJECT_ROOT / "cases" / "jayasekara_2020_reproduction_Abrine.yaml"
RESULT_PREFIX = "RATE_ISOLATION_RESULT:"
SOLVE_SCRIPT = r"""
import json
from pathlib import Path
import sys

import numpy as np
import reaktoro as rkt

from batch_runner.config import load_case
from batch_runner.simulator import prepare_simulation
from batch_runner.simulator.chemistry.conditions import build_conditions
from batch_runner.simulator.simulation import _isolated_initial_reaction_rate_fields
from batch_runner.simulator.solver.calls import kinetics_solver

case = load_case(sys.argv[1], output_dir_override=Path(sys.argv[2]))
prepared = prepare_simulation(case)
assert prepared.ready, prepared.error
specs, conditions = build_conditions(
    case, prepared.system, prepared.state, "kinetic_steps"
)
solver = kinetics_solver(prepared.system, specs)
if sys.argv[4] == "isolated":
    _isolated_initial_reaction_rate_fields(case, prepared.state)
try:
    result = (
        solver.solve(prepared.state, float(sys.argv[3]), conditions)
        if conditions is not None
        else solver.solve(prepared.state, float(sys.argv[3]))
    )
    succeeded = bool(result.succeeded())
    observed = {
        "kind": "result",
        "succeeded": succeeded,
        "iterations": int(result.iterations()),
        "amounts": (
            np.asarray(prepared.state.speciesAmounts(), dtype=float).tolist()
            if succeeded
            else None
        ),
        "pH": float(rkt.AqueousProps(prepared.state).pH()) if succeeded else None,
    }
except Exception as error:
    observed = {
        "kind": "exception",
        "succeeded": False,
        "error": f"{type(error).__name__}: {error}",
    }
print("RATE_ISOLATION_RESULT:" + json.dumps(observed), flush=True)
"""


def _solve_once(
    case_path: Path, output_dir: Path, dt_s: float, *, isolated_rates: bool
) -> dict:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            SOLVE_SCRIPT,
            str(case_path),
            str(output_dir),
            str(dt_s),
            "isolated" if isolated_rates else "none",
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert completed.returncode == 0, completed.stderr
    payload = next(
        (
            line[len(RESULT_PREFIX) :]
            for line in completed.stdout.splitlines()
            if line.startswith(RESULT_PREFIX)
        ),
        None,
    )
    assert payload is not None, completed.stdout + completed.stderr
    return json.loads(payload)


def _species_contract_case(tmp_path: Path) -> Path:
    raw = yaml.safe_load(SPECIES_CASE.read_text(encoding="utf-8"))
    raw["case"]["name"] = "species_initial_rate_contract"
    raw["paths"]["output_dir"] = str(tmp_path / "configured-output")
    raw["minerals"] = [
        mineral for mineral in raw["minerals"] if mineral["name"] == "Quartz"
    ]
    raw["postprocessing"]["requested_minerals"] = ["Quartz"]
    raw["monitor"]["enabled"] = False
    raw["monitor"]["minerals"] = []
    raw["monitor"]["result_times"] = []
    path = tmp_path / "species-contract.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


def test_real_species_rates_are_observational_for_first_solve(tmp_path: Path) -> None:
    assert rkt.__version__ == "2.13.0"
    case_path = _species_contract_case(tmp_path)

    without_rates = _solve_once(
        case_path, tmp_path / "without-rates", 1.0, isolated_rates=False
    )
    with_isolated_rates = _solve_once(
        case_path, tmp_path / "isolated-rates", 1.0, isolated_rates=True
    )

    assert (with_isolated_rates["kind"], with_isolated_rates["succeeded"]) == (
        without_rates["kind"],
        without_rates["succeeded"],
    ), (without_rates, with_isolated_rates)
    assert "too concentrated" not in with_isolated_rates.get("error", "").lower()
    if without_rates["succeeded"]:
        np.testing.assert_allclose(
            with_isolated_rates["amounts"],
            without_rates["amounts"],
            rtol=1e-12,
            atol=1e-14,
        )
        assert with_isolated_rates["pH"] == pytest.approx(
            without_rates["pH"], rel=1e-12, abs=1e-12
        )


def test_real_element_reset_and_isolated_rates_accept_2700_s_repeatedly(
    tmp_path: Path,
) -> None:
    assert rkt.__version__ == "2.13.0"

    observations = [
        _solve_once(ELEMENT_CASE, tmp_path / f"run-{index}", 2700.0, isolated_rates=True)
        for index in range(3)
    ]

    assert all(
        item["kind"] == "result" and item["succeeded"] for item in observations
    ), observations
    assert all(isfinite(item["pH"]) for item in observations)


def test_disposable_state_copy_and_reaction_rates_match(tmp_path: Path) -> None:
    assert rkt.__version__ == "2.13.0"
    case = load_case(SPECIES_CASE, output_dir_override=tmp_path / "outputs")
    prepared = prepare_simulation(case)
    reference = prepare_simulation(case)
    assert prepared.ready, prepared.error
    assert reference.ready, reference.error

    database = load_database(case)
    params = load_kinetic_parameters(case)
    diagnostic_system = build_chemical_system(case, database, params)
    live_names = tuple(species.name() for species in prepared.system.species())
    diagnostic_state = _copy_state_values_to_system(
        diagnostic_system,
        live_names,
        float(prepared.state.temperature()),
        float(prepared.state.pressure()),
        [float(amount) for amount in prepared.state.speciesAmounts()],
    )

    diagnostic_names = tuple(species.name() for species in diagnostic_system.species())
    assert diagnostic_names == live_names
    assert float(diagnostic_state.temperature()) == float(prepared.state.temperature())
    assert float(diagnostic_state.pressure()) == float(prepared.state.pressure())
    np.testing.assert_array_equal(
        diagnostic_state.speciesAmounts(), prepared.state.speciesAmounts()
    )

    specs, _conditions = build_conditions(
        case, prepared.system, prepared.state, "kinetic_steps"
    )
    live_solver = kinetics_solver(prepared.system, specs)
    reference_specs, _reference_conditions = build_conditions(
        case, reference.system, reference.state, "kinetic_steps"
    )
    reference_solver = kinetics_solver(reference.system, reference_specs)
    direct = collect_reaction_rate_fields(case, reference.state)
    isolated = _isolated_initial_reaction_rate_fields(case, prepared.state)
    assert live_solver is not None
    assert reference_solver is not None

    assert isolated.keys() == direct.keys()
    for name, expected in direct.items():
        if isinstance(expected, float):
            assert isolated[name] == pytest.approx(expected, rel=1e-12, abs=1e-18)
        else:
            assert isolated[name] == expected

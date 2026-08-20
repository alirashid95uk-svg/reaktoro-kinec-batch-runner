from copy import deepcopy
import math
from pathlib import Path

import pytest
import reaktoro as rkt
import yaml
from pydantic import ValidationError

from batch_runner.config import CaseConfig, load_case
from batch_runner.simulator.chemistry import (
    build_chemical_state,
    build_chemical_system,
    load_database,
)
from batch_runner.simulator.chemistry.state import _equilibrate_element_brine
from batch_runner.simulator.kinetics import load_kinetic_parameters


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_CASE = PROJECT_ROOT / "cases" / "jayasekara_2020_reproduction_monitor.yaml"
ELEMENT_AMOUNTS = {
    "H": {"value": 111.05065020155693, "unit": "mol"},
    "O": {"value": 57.854747959971405, "unit": "mol"},
    "C": {"value": 1.1647114295964651, "unit": "mol"},
    "Na": {"value": 0.8062660077228982, "unit": "mol"},
    "Cl": {"value": 0.8062660077228987, "unit": "mol"},
}


def _source_case(tmp_path: Path) -> dict:
    raw = yaml.safe_load(SOURCE_CASE.read_text(encoding="utf-8"))
    raw["case"]["name"] = "element_total_brine_test"
    raw["paths"]["output_dir"] = str(tmp_path / "outputs")
    return raw


def _element_case(tmp_path: Path) -> dict:
    raw = _source_case(tmp_path)
    raw["brine"] = {
        "aqueous_elements": raw["brine"]["aqueous_elements"],
        "element_amounts": deepcopy(ELEMENT_AMOUNTS),
    }
    return raw


def _write_case(tmp_path: Path, raw: dict) -> Path:
    path = tmp_path / "case.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


def test_brine_initialization_schema_is_exclusive_and_backward_compatible(tmp_path: Path) -> None:
    species = CaseConfig.model_validate(_source_case(tmp_path)).brine
    assert species.species_amounts is not None
    assert species.element_amounts is None

    element_raw = _element_case(tmp_path)
    element = CaseConfig.model_validate(element_raw).brine
    assert element.species_amounts is None
    assert set(element.element_amounts) == set(ELEMENT_AMOUNTS)

    both = deepcopy(element_raw)
    both["brine"]["species_amounts"] = {
        "H2O": {"value": 1.0, "unit": "kg"}
    }
    with pytest.raises(ValidationError, match="exactly one of species_amounts or element_amounts"):
        CaseConfig.model_validate(both)

    neither = deepcopy(element_raw)
    del neither["brine"]["element_amounts"]
    with pytest.raises(ValidationError, match="exactly one of species_amounts or element_amounts"):
        CaseConfig.model_validate(neither)


@pytest.mark.parametrize("mapping", ["species_amounts", "element_amounts"])
def test_brine_initialization_mapping_must_be_non_empty(tmp_path: Path, mapping: str) -> None:
    raw = _element_case(tmp_path)
    raw["brine"].pop("element_amounts")
    raw["brine"][mapping] = {}
    with pytest.raises(ValidationError, match="at least 1 item"):
        CaseConfig.model_validate(raw)


def test_element_amount_keys_must_be_aqueous_elements(tmp_path: Path) -> None:
    raw = _element_case(tmp_path)
    raw["brine"]["element_amounts"]["N"] = {"value": 1.0, "unit": "mol"}
    with pytest.raises(ValidationError, match="must be listed in brine.aqueous_elements: N"):
        CaseConfig.model_validate(raw)


def test_real_reaktoro_213_element_total_brine_state(tmp_path: Path) -> None:
    assert rkt.__version__ == "2.13.0"

    case = load_case(_write_case(tmp_path, _element_case(tmp_path)))
    database = load_database(case)
    system = build_chemical_system(case, database, load_kinetic_parameters(case))

    brine_state = _equilibrate_element_brine(case, system)
    aqueous = rkt.AqueousProps(brine_state)
    assert math.isfinite(float(aqueous.pH()))
    assert math.isfinite(float(aqueous.ionicStrength()))

    totals = brine_state.elementAmounts()
    for element, amount in ELEMENT_AMOUNTS.items():
        assert float(totals[system.elements().index(element)]) == pytest.approx(
            amount["value"], rel=1e-10, abs=1e-12
        )

    net_charge = sum(
        float(species.charge()) * float(amount)
        for species, amount in zip(system.species(), brine_state.speciesAmounts())
    )
    assert net_charge == pytest.approx(0.0, abs=1e-10)
    assert float(brine_state.speciesAmount("Gibbsite")) <= 1e-15

    state = build_chemical_state(case, system)
    for mineral in case.config.minerals:
        assert float(state.speciesAmount(mineral.name)) == pytest.approx(
            mineral.initial_amount.value, abs=1e-15
        )

    species_case = load_case(
        _write_case(tmp_path, _source_case(tmp_path)),
        output_dir_override=tmp_path / "species-outputs",
    )
    species_state = build_chemical_state(species_case, system)
    assert float(species_state.speciesAmount("Na+")) == pytest.approx(
        species_case.config.brine.species_amounts["Na+"].value
    )
    assert float(species_state.speciesAmount("Cl-")) == pytest.approx(
        species_case.config.brine.species_amounts["Cl-"].value
    )

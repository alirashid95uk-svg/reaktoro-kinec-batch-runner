from __future__ import annotations

"""Generate batch-runner cases for the Pokrovsky et al. (2005) Calcite benchmark.

Scientific/source boundary
--------------------------
Published experiment: Pokrovsky et al. (2005), Chemical Geology 217, 239-255,
DOI 10.1016/j.chemgeo.2004.12.012.

The source-traced benchmark values are stored in
``data/validation/pokrovsky_2005_calcite_intrinsic_targets.csv``.

This preprocessor follows the existing Validation-Pokrovsky implementation for
three deterministic implementation choices that are not experimental values:

* 1 L prepared 0.1 mol/L NaCl basis, with water mass iterated from the Reaktoro
  aqueous volume;
* 10 mol initial CO2 gas reservoir, checked to remain present;
* 1 mol Calcite and 1 m2 total Calcite surface area as a rate-normalisation
  probe.  At time zero, the numerical reaction rate in mol/s therefore equals
  the area-normalised flux in mol/m2/s.

The pre-equilibration is intentionally fluid-only: Calcite is added only after
CO2/brine equilibrium has been calculated.  This avoids equilibrating the
kinetic mineral before the time-zero rate is observed.

The batch runner currently supports PHREEQC activity modelling, not the
MINTEQA2/Davies thermodynamic setup used by Pokrovsky et al.  Generated cases
therefore reproduce the experimental bulk T/NaCl/pCO2 envelope inside the
runner's current thermodynamic contract; they are not an exact reconstruction
of the paper's thermodynamic or rotating-disc transport calculation.
"""

import csv
import math
from pathlib import Path

import reaktoro as rkt
import yaml

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ROOT / "data" / "validation" / "pokrovsky_2005_calcite_intrinsic_targets.csv"
DATABASE = ROOT / "data" / "thermo" / "Kinec_v3_4.dat"
KINETICS = "data/kinetics/PalandriKharaka_pokrovsky_2005_weiss_calcite.yaml"
CASE_DIR = ROOT / "cases" / "pokrovsky_2005"

T_C = 25.0
T_K = 298.15
NACL_M = 0.1
CO2_RESERVOIR_MOL = 10.0
CALCITE_AMOUNT_MOL = 1.0
CALCITE_AREA_M2 = 1.0
ATM_TO_PA = 101325.0


def load_targets() -> list[dict[str, float | str]]:
    with TARGETS.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    if [float(row["pCO2_atm"]) for row in rows] != [2.0, 10.0, 50.0]:
        raise RuntimeError("expected Pokrovsky target rows at 2, 10, and 50 atm")
    return rows


def build_fluid_system():
    db = rkt.PhreeqcDatabase.fromFile(str(DATABASE))
    aqueous = rkt.AqueousPhase(rkt.speciate("H O C Ca Na Cl"))
    aqueous.setActivityModel(rkt.ActivityModelPhreeqc(db))
    gas = rkt.GaseousPhase(["CO2(g)"])
    gas.setActivityModel(rkt.ActivityModelPengRobinsonPhreeqc())
    return rkt.ChemicalSystem(db, aqueous, gas)


def aqueous_species(system):
    """Return aqueous species from the constructed system, which owns the phase."""
    for phase in system.phases():
        if phase.name() == "AqueousPhase":
            return list(phase.species())
    raise RuntimeError("constructed fluid system has no AqueousPhase")


def prepared_water_mass_kg(system) -> float:
    """Match the 1 L / 0.1 mol NaCl preparation used in Validation-Pokrovsky."""
    water_density_kg_m3 = float(rkt.waterLiquidDensityWagnerPruss(T_K, ATM_TO_PA))
    water_mass_kg = water_density_kg_m3 * 1.0e-3
    prepared_volume_l = math.nan
    for _ in range(4):
        state = rkt.ChemicalState(system)
        state.temperature(T_C, "celsius")
        state.pressure(1.0, "atm")
        state.set("H2O", water_mass_kg, "kg")
        state.set("Na+", NACL_M, "mol")
        state.set("Cl-", NACL_M, "mol")
        state.set("CO2(g)", 1.0e-12, "mol")
        result = rkt.EquilibriumSolver(system).solve(state)
        if not result.succeeded():
            raise RuntimeError("failed to prepare 0.1 mol/L NaCl fluid basis")
        prepared_volume_l = float(rkt.ChemicalProps(state).phaseProps("AqueousPhase").volume()) * 1000.0
        water_mass_kg /= prepared_volume_l
    if not math.isclose(prepared_volume_l, 1.0, rel_tol=0.0, abs_tol=1.0e-9):
        raise RuntimeError(f"prepared aqueous volume is not 1 L: {prepared_volume_l}")
    return water_mass_kg


def equilibrated_fluid(system, water_mass_kg: float, pressure_bar: float):
    state = rkt.ChemicalState(system)
    state.temperature(T_C, "celsius")
    state.pressure(pressure_bar, "bar")
    state.set("H2O", water_mass_kg, "kg")
    state.set("Na+", NACL_M, "mol")
    state.set("Cl-", NACL_M, "mol")
    state.set("CO2(g)", CO2_RESERVOIR_MOL, "mol")
    result = rkt.EquilibriumSolver(system).solve(state)
    if not result.succeeded():
        raise RuntimeError(f"fluid equilibrium failed at {pressure_bar} bar")
    gas_amount = float(state.speciesAmount("CO2(g)"))
    if gas_amount <= 0.0:
        raise RuntimeError(f"CO2 gas phase exhausted at {pressure_bar} bar")
    aqueous_amounts = {
        species.name(): {"value": float(state.speciesAmount(species.name())), "unit": "mol"}
        for species in aqueous_species(system)
    }
    if not aqueous_amounts:
        raise RuntimeError("no aqueous species were extracted from the equilibrated fluid")
    return state, aqueous_amounts, gas_amount


def case_document(target: dict[str, float | str], aqueous_amounts, gas_amount: float) -> dict:
    p_atm = float(target["pCO2_atm"])
    pressure_bar = float(target["pressure_bar"])
    label = f"{int(p_atm)}atm"
    return {
        "case": {"name": f"pokrovsky_2005_calcite_{label}"},
        "paths": {"output_dir": f"outputs/pokrovsky_2005/{label}"},
        "database": {"source": "local", "path": "data/thermo/Kinec_v3_4.dat"},
        "activity_models": {"aqueous": "phreeqc", "gas": "peng_robinson_phreeqc"},
        "physical": {"temperature_c": T_C, "pressure_bar": pressure_bar},
        "brine": {
            "aqueous_elements": ["H", "O", "C", "Ca", "Na", "Cl"],
            "species_amounts": aqueous_amounts,
        },
        "co2": {
            "mode": "finite",
            "gas_species": "CO2(g)",
            "initial_amount": {"value": gas_amount, "unit": "mol"},
        },
        "redox": {"enabled": False},
        "kinetics": {
            "enabled": True,
            "model": "palandri_kharaka",
            "path": KINETICS,
        },
        "minerals": [
            {
                "name": "Calcite",
                "role": "kinetic",
                "initial_amount": {"value": CALCITE_AMOUNT_MOL, "unit": "mol"},
                "surface_area": {"value": CALCITE_AREA_M2, "unit": "m2"},
                "surface_area_basis": "1 m2 computational rate-normalisation probe",
                "surface_area_provenance": "Validation-Pokrovsky validation/calcite_validation.py SURFACE_AREA_M2",
                "selection_reason": "Pokrovsky et al. (2005) rotating-disc Calcite benchmark",
            }
        ],
        "solver": {
            "workflow": {"mode": "closed_kinetics"},
            "timestep": {
                "mode": "fixed",
                "time": {"duration_value": 1.0, "duration_unit": "seconds"},
                "step_size": {"dt": {"value": 1.0, "unit": "seconds"}},
                "output_schedule": {
                    "mode": "every_internal_step",
                    "include_initial": True,
                    "include_final": True,
                    "explicit_times": [],
                },
            },
        },
        "postprocessing": {
            "requested_species": ["H+", "CO2", "HCO3-", "CO3-2", "Ca+2"],
            "requested_minerals": ["Calcite"],
            "aqueous_molalities": True,
            "saturation_indices": True,
            "reaction_rates": True,
            "element_budget": {"enabled": False, "elements": [], "species": {}, "minerals": {}, "gas_species": {}},
            "carbon_inventory": {"enabled": False, "carbon_species": {}, "carbon_minerals": {}, "carbon_gas_species": {}},
            "mineral_volume_change": {"enabled": False, "molar_volumes_cm3_per_mol": {}, "sources": {}},
            "regime_classification": {"enabled": False},
            "surface_area_audit": {"enabled": True},
            "workflow_comparison": {"enabled": False},
            "secondary_mineral_assemblage": {"enabled": False},
            "surrogate_dataset": {"enabled": False},
            "porosity_permeability": {"enabled": False},
        },
        "validation": {"enabled": False, "targets": []},
        "outputs": {
            "monitor": {
                "enabled": True,
                "refresh_interval_s": 0.5,
                "scalars": ["pH"],
                "species": ["Ca+2", "CO2"],
                "minerals": ["Calcite"],
                "result_times": [],
            },
            "manifest": {"enabled": True, "include_input_snapshot": True},
            "diagnostics": {"enabled": True},
            "timeseries": {
                "enabled": True,
                "include_species_amounts": True,
                "include_species_molalities": True,
                "include_mineral_amounts": True,
                "include_mineral_deltas": True,
                "include_saturation_indices": True,
                "include_solver_columns": True,
            },
            "summaries": {
                "mineral_summary": True,
                "aqueous_summary": True,
                "reaction_rates": True,
                "reaction_rate_validation": True,
                "carbon_inventory": False,
                "element_budget": False,
                "mineral_volume_change": False,
                "regime_classification": False,
                "surface_area_audit": True,
                "workflow_comparison": False,
                "secondary_mineral_assemblage": False,
                "surrogate_dataset": False,
                "validation_ledger": False,
                "porosity_permeability": False,
            },
            "solver_history": {"enabled": True},
            "plots": {
                "enabled": True,
                "pH": True,
                "mineral_change": True,
                "saturation_index": True,
                "solver_dt": False,
                "solver_iterations": False,
            },
            "debug": {"enabled": True, "mineral_connection": True, "resolved_config": True, "final_state": True},
        },
    }


def main() -> None:
    system = build_fluid_system()
    water_mass = prepared_water_mass_kg(system)
    CASE_DIR.mkdir(parents=True, exist_ok=True)

    print(f"prepared_water_mass_kg={water_mass:.17g}")
    for target in load_targets():
        pressure_bar = float(target["pressure_bar"])
        state, aqueous_amounts, gas_amount = equilibrated_fluid(
            system, water_mass, pressure_bar
        )
        document = case_document(target, aqueous_amounts, gas_amount)
        label = f"{int(float(target['pCO2_atm']))}atm"
        path = CASE_DIR / f"pokrovsky_2005_{label}.yaml"
        path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
        props = rkt.ChemicalProps(state)
        print(
            f"{label}: pressure_bar={pressure_bar:.10g}, "
            f"gas_CO2_mol={gas_amount:.17g}, "
            f"bulk_pH={-math.log10(float(props.speciesActivity('H+'))):.12g}, "
            f"aCO2={float(props.speciesActivity('CO2')):.12g}, "
            f"case={path.relative_to(ROOT)}"
        )


if __name__ == "__main__":
    main()

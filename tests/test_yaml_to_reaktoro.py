from pathlib import Path

import pytest

from yaml_to_reaktoro import generate_reaktoro_code


def _case():
    return {
        "case": {"name": "test"},
        "paths": {"output_dir": "outputs/test"},
        "database": {"source": "local", "path": "data/thermo/Kinec_v3_4.dat"},
        "activity_models": {"aqueous": "phreeqc"},
        "physical": {"temperature_c": 40.0, "pressure_bar": 100.0},
        "brine": {
            "aqueous_elements": ["H", "O", "Na", "Cl", "C", "Ca"],
            "species_amounts": {
                "H2O": {"value": 1.0, "unit": "kg"},
                "Na+": {"value": 1.0, "unit": "mol"},
                "Cl-": {"value": 1.0, "unit": "mol"},
            },
        },
        "co2": {"mode": "fixed_fugacity", "gas_species": "CO2(g)", "fugacity_bar": 57.77},
        "redox": {"enabled": False},
        "kinetics": {"enabled": True},
        "minerals": [
            {
                "name": "Calcite",
                "role": "kinetic",
                "initial_amount": {"value": 0.8, "unit": "mol"},
                "surface_area": {"value": 0.08, "unit": "m2/g"},
            }
        ],
        "solver": {
            "workflow": {
                "mode": "fixed_fugacity_initial_equilibrium_then_closed_kinetics",
            },
            "timestep": {
                "mode": "fixed",
                "time": {"duration_value": 60.0, "duration_unit": "seconds"},
                "step_size": {"dt": {"value": 10.0, "unit": "seconds"}},
            },
        },
        "postprocessing": {
            "requested_species": ["H+", "Ca+2"],
            "requested_minerals": ["Calcite"],
            "aqueous_molalities": True,
            "saturation_indices": True,
            "reaction_rates": False,
            "element_budget": {"enabled": False, "elements": [], "species": {}, "minerals": {}, "gas_species": {}},
            "carbon_inventory": {"enabled": False, "carbon_species": {}, "carbon_minerals": {}, "carbon_gas_species": {}},
            "mineral_volume_change": {"enabled": False, "molar_volumes_cm3_per_mol": {}, "sources": {}},
            "regime_classification": {"enabled": False},
            "surface_area_audit": {"enabled": False},
            "workflow_comparison": {"enabled": False},
            "secondary_mineral_assemblage": {"enabled": False},
            "surrogate_dataset": {"enabled": False},
            "porosity_permeability": {"enabled": False},
        },
        "validation": {"enabled": False, "targets": []},
        "outputs": {},
    }


def _generate(case):
    code = generate_reaktoro_code(case, Path("case.yaml"))
    compile(code, "<generated>", "exec")
    return code


def test_native_staged_fixed_fugacity_equivalent():
    code = _generate(_case())
    assert "rkt.ActivityModelPhreeqc(database)" in code
    assert "rkt.ReactionRateModelPalandriKharaka(kinetic_params)" in code
    assert "conditions.fugacity('CO2(g)', 57.77, 'bar')" in code
    assert "equilibrium_solver.solve(state, initial_conditions)" in code
    assert "rkt.KineticsSolver(system)" in code
    assert "state.assign(accepted_state)" in code
    assert "max_internal_steps" in code


def test_finite_co2_custom_kinec_equivalent():
    case = _case()
    case["activity_models"]["gas"] = "peng_robinson_phreeqc"
    case["co2"] = {
        "mode": "finite",
        "gas_species": "CO2(g)",
        "initial_amount": {"value": 1.0, "unit": "mol"},
    }
    case["kinetics"] = {"enabled": True, "model": "kinec"}
    case["solver"]["workflow"] = {"mode": "closed_kinetics"}
    code = _generate(case)
    assert "rkt.GaseousPhase(['CO2(g)'])" in code
    assert "rkt.ActivityModelPengRobinsonPhreeqc()" in code
    assert "state.set('CO2(g)', 1.0, 'mol')" in code
    assert "def make_kinec_rate_model" in code
    assert "activity must be positive when raised to a nonzero power" in code
    assert "kinetic_solver.precondition" not in code


def test_adaptive_solver_failure_rollback_and_retry_are_emitted():
    case = _case()
    case["solver"]["workflow"] = {
        "mode": "fixed_fugacity_during_kinetic_steps",
    }
    case["solver"]["timestep"] = {
        "mode": "adaptive",
        "time": {"duration_value": 1, "duration_unit": "day"},
        "step_size": {
            "dt_initial": {"value": 1, "unit": "second"},
            "dt_min": {"value": 0.1, "unit": "second"},
            "dt_max": {"value": 1, "unit": "hour"},
            "growth_factor": 1.25,
            "shrink_factor": 0.5,
            "max_retries_per_step": 8,
        },
        "output_schedule": {
            "mode": "explicit",
            "include_initial": True,
            "include_final": True,
            "explicit_times": [],
        },
        "checkpoint_schedule": {"enabled": False, "times": []},
    }
    code = _generate(case)
    assert "def trial_accepted" not in code
    assert "accepted_state = rkt.ChemicalState(state)" in code
    assert "result is None or not result.succeeded()" in code
    assert "controller_dt_s = max(dt_min_s, dt_s * shrink_factor)" in code
    assert "state.assign(accepted_state)" in code
    assert "controller_dt_s" in code
    assert "conditions.fugacity('CO2(g)', 57.77, 'bar')" in code


def test_unknown_physics_fails_loudly():
    case = _case()
    case["new_physics"] = {"enabled": True}
    with pytest.raises(ValueError, match="unsupported top-level YAML section"):
        generate_reaktoro_code(case, Path("case.yaml"))

    case = _case()
    case["solver"]["timestep"]["mystery_control"] = 123
    with pytest.raises(ValueError, match="unsupported YAML field.*solver.timestep"):
        generate_reaktoro_code(case, Path("case.yaml"))

    case = _case()
    case["solver"]["workflow"]["precondition_kinetics"] = False
    with pytest.raises(ValueError, match="precondition_kinetics"):
        generate_reaktoro_code(case, Path("case.yaml"))

    case = _case()
    case["solver"]["timestep"]["mode"] = "adaptive_long_horizon"
    with pytest.raises(ValueError, match="adaptive_long_horizon"):
        generate_reaktoro_code(case, Path("case.yaml"))

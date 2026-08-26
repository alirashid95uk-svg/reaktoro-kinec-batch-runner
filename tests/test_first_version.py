import csv
import hashlib
import json
import re
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import reaktoro as rkt
import yaml
from pydantic import ValidationError

from batch_runner.config import CaseConfig, load_case
from batch_runner.outputs import write_kinetic_mapping, write_outputs
from batch_runner.outputs.tables import mineral_summary_rows, timeseries_columns
from batch_runner.simulator import (
    SimulationResult,
    preflight_case,
    run_simulation,
)
from batch_runner.simulator.chemistry import (
    build_chemical_state,
    build_chemical_system,
    collect_row,
    load_database,
)
from batch_runner.simulator.kinetics import (
    build_kinetic_mapping,
    load_kinetic_parameters,
    require_valid_kinetic_mapping,
)
from batch_runner.simulator.kinetics.kinec import KinecParams, ReactionRateModelKinec


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = PROJECT_ROOT / "data" / "thermo" / "Kinec_v3_4.dat"
KINETICS_PATH = PROJECT_ROOT / "data" / "kinetics" / "kinec_rates_minimal.yaml"
PALANDRI_PATH = PROJECT_ROOT / "data" / "kinetics" / "PalandriKharaka_local.yaml"
TEMPLATE_PATH = PROJECT_ROOT / "cases" / "schema_template.yaml"
SYNTHETIC_CASE_PATH = (
    PROJECT_ROOT / "tests" / "fixtures" / "cases" / "synthetic_kinec_case.yaml"
)


def _read_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def _source_case_with_output(output_dir: Path) -> dict:
    raw = _read_yaml(SYNTHETIC_CASE_PATH)
    raw["paths"]["output_dir"] = str(output_dir)
    return raw


def _equilibrium_case(output_dir: Path, database_path: Path = DATABASE_PATH) -> dict:
    raw = _source_case_with_output(output_dir)
    raw["database"] = {"source": "local", "path": str(database_path)}
    raw["co2"] = {"mode": "disabled"}
    raw["kinetics"] = {"enabled": False}
    raw["minerals"][0]["role"] = "equilibrium"
    del raw["minerals"][0]["surface_area"]
    raw["solver"]["workflow"] = {"mode": "equilibrium_only"}
    return raw


def _write_case(tmp_path: Path, raw: dict) -> Path:
    config_path = tmp_path / "case.yaml"
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return config_path


def test_schema_template_parses_but_is_not_runnable() -> None:
    raw = _read_yaml(TEMPLATE_PATH)
    assert isinstance(raw, dict)
    with pytest.raises(ValidationError):
        CaseConfig.model_validate(raw)


def test_project_relative_path_resolves_from_project_root(tmp_path: Path) -> None:
    raw = _equilibrium_case(tmp_path / "outputs", Path("data/thermo/Kinec_v3_4.dat"))
    resolved = load_case(_write_case(tmp_path, raw))
    assert resolved.database_path == DATABASE_PATH.resolve()
    assert resolved.full_steps == 0


def test_preflight_blocks_missing_kinec_records_before_solver(tmp_path: Path) -> None:
    raw = _source_case_with_output(tmp_path / "preflight-results")
    raw["minerals"][0]["name"] = "Afwillite"
    raw["postprocessing"]["requested_minerals"] = ["Afwillite"]
    case = load_case(_write_case(tmp_path, raw))

    report = preflight_case(case)

    assert report["ready"] is False
    assert report["failed_stage"] == "mapping"
    assert [
        row["mineral_name"]
        for row in report["kinetic_mapping"]
        if row["status"] == "failed"
    ] == ["Afwillite"]
    assert not case.output_dir.exists()


def test_kinetic_model_defaults_and_resolved_provenance(tmp_path: Path) -> None:
    raw = _source_case_with_output(tmp_path / "palandri")
    raw["kinetics"] = {"enabled": True}
    resolved = load_case(_write_case(tmp_path, raw))
    assert resolved.config.kinetics.model == "palandri_kharaka"
    assert resolved.kinetics_path == PALANDRI_PATH.resolve()
    assert resolved.as_dict()["kinetics"] == {
        "enabled": True,
        "model": "palandri_kharaka",
        "path": str(PALANDRI_PATH.resolve()),
        "sha256": hashlib.sha256(PALANDRI_PATH.read_bytes()).hexdigest(),
    }

    kinec_raw = _source_case_with_output(tmp_path / "kinec")
    kinec_default = CaseConfig.model_validate(kinec_raw)
    assert kinec_default.kinetics.model == "kinec"
    assert kinec_default.kinetics.path == "data/kinetics/kinec_rates_minimal.yaml"
    kinec_raw["kinetics"]["path"] = str(KINETICS_PATH)
    kinec = load_case(_write_case(tmp_path, kinec_raw))
    assert kinec.config.kinetics.model == "kinec"
    assert kinec.kinetics_path == KINETICS_PATH.resolve()

    disabled = _equilibrium_case(tmp_path / "disabled")
    disabled["kinetics"]["model"] = "kinec"
    with pytest.raises(ValidationError, match="forbids model and path"):
        CaseConfig.model_validate(disabled)


def test_removed_mineral_alias_fields_are_unknown(tmp_path: Path) -> None:
    raw = _source_case_with_output(tmp_path / "outputs")
    raw["minerals"][0]["thermo" + "_name"] = "Calcite"
    raw["minerals"][0]["kinetic" + "_name"] = "Calcite"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CaseConfig.model_validate(raw)


def test_missing_local_path_reports_exact_resolved_path(tmp_path: Path) -> None:
    relative_missing = Path("data/thermo/missing-test-database.dat")
    raw = _equilibrium_case(tmp_path / "outputs", relative_missing)
    with pytest.raises(FileNotFoundError, match=re.escape(str(PROJECT_ROOT / relative_missing))):
        load_case(_write_case(tmp_path, raw))


def test_legacy_kinetics_timestep_fields_are_rejected(tmp_path: Path) -> None:
    raw = _source_case_with_output(tmp_path / "outputs")
    raw["kinetics"]["duration_s"] = 1.0
    raw["kinetics"]["dt_s"] = 1.0
    with pytest.raises(ValidationError, match="duration_s|dt_s"):
        CaseConfig.model_validate(raw)


def test_future_timestep_modes_are_rejected(tmp_path: Path) -> None:
    raw = _source_case_with_output(tmp_path / "outputs")
    assert CaseConfig.model_validate(raw).solver.timestep.mode == "fixed"
    raw["solver"]["timestep"] = {
        "mode": "adaptive",
        "time": {"duration_value": 1.0, "duration_unit": "day"},
        "step_size": {"dt_initial": {"value": 1.0, "unit": "second"}},
    }
    with pytest.raises(ValidationError, match="fixed|dt"):
        CaseConfig.model_validate(raw)


def test_removed_future_runtime_fields_are_unknown(tmp_path: Path) -> None:
    cases = [
        (("solver", "backend"), {"type": "standard"}),
        (("postprocessing", "unreviewed_future_diagnostic"), False),
        (("outputs", "checkpoints"), {"enabled": False}),
        (("outputs", "plots", "species_molality"), False),
    ]
    for path, value in cases:
        raw = _source_case_with_output(tmp_path / "outputs")
        target = raw
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        with pytest.raises(ValidationError, match=path[-1]):
            CaseConfig.model_validate(raw)


def test_redox_staging_and_workflow_co2_compatibility(tmp_path: Path) -> None:
    raw = _source_case_with_output(tmp_path / "outputs")
    raw["redox"] = {"enabled": True, "pe": 4.0}
    with pytest.raises(ValidationError, match="apply_during"):
        CaseConfig.model_validate(raw)

    raw = _source_case_with_output(tmp_path / "outputs")
    raw["solver"]["workflow"]["mode"] = "fixed_fugacity_initial_equilibrium_then_closed_kinetics"
    with pytest.raises(ValidationError, match="fixed_fugacity"):
        CaseConfig.model_validate(raw)


def test_unsupported_third_mineral_role_is_rejected(tmp_path: Path) -> None:
    raw = _equilibrium_case(tmp_path / "outputs")
    raw["minerals"][0]["role"] = "diagnostic"
    with pytest.raises(ValidationError, match="equilibrium.*kinetic"):
        CaseConfig.model_validate(raw)


def test_equilibrium_mineral_mapping_does_not_require_kinec_record(tmp_path: Path) -> None:
    case = load_case(_write_case(tmp_path, _equilibrium_case(tmp_path / "outputs")))
    mapping = build_kinetic_mapping(case, load_database(case), None)
    assert mapping[0]["status"] == "active"
    assert mapping[0]["kinetic_parameter_record_found"] is False
    assert mapping[0]["reason"] == "equilibrium mineral; no kinetic record required"


def test_cleaned_kinec_yaml_loads_through_supplied_adapter() -> None:
    params = KinecParams.local(KINETICS_PATH)
    assert {"Calcite", "Quartz", "Illite"}.issubset(params.data)


def test_local_database_loads_and_kinec_adapter_attaches() -> None:
    database = rkt.PhreeqcDatabase.fromFile(str(DATABASE_PATH))
    aqueous = rkt.AqueousPhase(rkt.speciate(["H", "O", "C", "Ca"]))
    aqueous.setActivityModel(rkt.ActivityModelPhreeqc(database))
    params = KinecParams.local(KINETICS_PATH)
    reaction = rkt.MineralReaction("Calcite")
    reaction.setRateModel(ReactionRateModelKinec(params, "Calcite"))
    system = rkt.ChemicalSystem(
        database,
        aqueous,
        rkt.MineralPhases(["Calcite"]),
        reaction,
        rkt.MineralSurface("Calcite", 6.0, "cm2/cm3"),
    )
    assert len(system.reactions()) == 1
    assert len(system.surfaces()) == 1


def test_native_palandri_uses_mineral_and_other_names_and_kinec_is_explicit(
    tmp_path: Path,
) -> None:
    for index, name in enumerate(("Calcite", "K-Feldspar")):
        raw = _source_case_with_output(tmp_path / f"palandri-{index}")
        raw["kinetics"] = {"enabled": True}
        raw["brine"]["aqueous_elements"] = ["H", "O", "Na", "Cl", "C", "Ca", "K", "Al", "Si"]
        raw["minerals"][0]["name"] = name
        raw["postprocessing"]["requested_minerals"] = [name]
        case = load_case(_write_case(tmp_path, raw))
        params = load_kinetic_parameters(case)
        mapping = build_kinetic_mapping(case, load_database(case), params)
        require_valid_kinetic_mapping(mapping)
        system = build_chemical_system(case, load_database(case), params)
        assert system.reactions()[0].name() == name

    raw = _source_case_with_output(tmp_path / "missing-palandri")
    raw["kinetics"] = {"enabled": True}
    raw["minerals"][0]["name"] = "Chalcedony"
    raw["postprocessing"]["requested_minerals"] = ["Chalcedony"]
    case = load_case(_write_case(tmp_path, raw))
    mapping = build_kinetic_mapping(case, load_database(case), load_kinetic_parameters(case))
    with pytest.raises(ValueError, match="missing palandri_kharaka parameter record"):
        require_valid_kinetic_mapping(mapping)

    raw["paths"]["output_dir"] = str(tmp_path / "kinec-chalcedony")
    raw["kinetics"] = {"enabled": True, "model": "kinec"}
    case = load_case(_write_case(tmp_path, raw))
    mapping = build_kinetic_mapping(case, load_database(case), load_kinetic_parameters(case))
    require_valid_kinetic_mapping(mapping)


def test_runtime_reaction_rate_diagnostics_use_chemical_props(tmp_path: Path) -> None:
    raw = _source_case_with_output(tmp_path / "runtime-rates")
    raw["kinetics"] = {"enabled": True}
    raw["postprocessing"]["reaction_rates"] = True
    case = load_case(_write_case(tmp_path, raw))
    database = load_database(case)
    params = load_kinetic_parameters(case)
    system = build_chemical_system(case, database, params)
    state = build_chemical_state(case, system)
    result = rkt.KineticsSolver(system).precondition(state)
    assert result.succeeded()
    initial_state = rkt.ChemicalState(state)
    record = {
        "time_end_s": 0.0,
        "stage": "initial_state",
        "solver_succeeded": None,
        "iterations": None,
        "dt_s": 0.0,
    }
    row = collect_row(case, state, record, initial_state)
    props = rkt.ChemicalProps(state)
    rate = float(props.reactionRate("Calcite"))
    area = float(props.surfaceArea("Calcite"))
    assert rate == pytest.approx(float(props.reactionRate(0)))
    assert row["saturation_index::Calcite"] < 0.0
    assert rate > 0.0
    assert row["reaction_rate_mol_s::Calcite"] == pytest.approx(rate)
    assert row["reaction_rate_surface_area_m2::Calcite"] == pytest.approx(area)
    assert row["reaction_rate_mol_m2_s::Calcite"] == pytest.approx(rate / area)

    state.set("Calcite", 0.0, "mol")
    zero_row = collect_row(case, state, record, rkt.ChemicalState(state))
    assert zero_row["reaction_rate_surface_area_m2::Calcite"] == 0.0
    assert zero_row["reaction_rate_mol_m2_s::Calcite"] is None
    assert zero_row["reaction_rate_status::Calcite"] == "zero_live_surface_area"


def test_general_reaction_rate_contract_positive_mol_per_second_dissolves() -> None:
    code = """
import json
import os
import sys
import reaktoro as rkt

rate_mol_s = 1.0e-8
dt_s = 1.0

def constant_rate(props: rkt.ChemicalProps) -> rkt.ReactionRate:
    return rkt.ReactionRate(rate_mol_s)

database = rkt.PhreeqcDatabase.withName("phreeqc.dat")
aqueous = rkt.AqueousPhase(rkt.speciate(["H", "O", "C", "Ca"]))
aqueous.setActivityModel(rkt.ActivityModelPhreeqc(database))
reaction = rkt.MineralReaction("Calcite")
reaction.setRateModel(rkt.ReactionRateModel(constant_rate))
system = rkt.ChemicalSystem(database, aqueous, rkt.MineralPhases(["Calcite"]), reaction)
coefficient = float(system.reactions()[0].equation().coefficient("Calcite"))

state = rkt.ChemicalState(system)
state.temperature(25.0, "celsius")
state.pressure(1.0, "bar")
state.set("H2O", 1.0, "kg")
state.set("Calcite", 1.0, "mol")
initial_calcite_mol = float(state.speciesAmount("Calcite"))
result = rkt.KineticsSolver(system).solve(state, dt_s)
dissolved_mol = initial_calcite_mol - float(state.speciesAmount("Calcite"))

print(json.dumps({
    "version": rkt.__version__,
    "coefficient": coefficient,
    "succeeded": result.succeeded(),
    "dissolved_mol": dissolved_mol,
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
    assert observed["coefficient"] == -1.0
    assert observed["succeeded"] is True
    assert observed["dissolved_mol"] == pytest.approx(1.0e-8, rel=1.0e-6)


def test_synthetic_case_loads_with_fixed_timestep(tmp_path: Path) -> None:
    resolved = load_case(_write_case(tmp_path, _source_case_with_output(tmp_path / "outputs")))
    assert resolved.full_steps == 2
    assert resolved.final_step_s == 0.0
    assert resolved.duration_s == 2.0


def test_missing_surface_area_and_kinetic_record_fail(tmp_path: Path) -> None:
    raw = _source_case_with_output(tmp_path / "surface-output")
    del raw["minerals"][0]["surface_area"]
    with pytest.raises(ValidationError, match="surface_area"):
        CaseConfig.model_validate(raw)

    raw = _source_case_with_output(tmp_path / "record-output")
    raw["minerals"][0]["name"] = "Afwillite"
    raw["postprocessing"]["requested_minerals"] = ["Afwillite"]
    result = run_simulation(load_case(_write_case(tmp_path, raw)))
    assert result.diagnostics["simulation_completed"] is False
    assert result.diagnostics["failed_stage"] == "mapping"
    assert "missing kinec parameter record" in result.diagnostics["error_message"]
    assert result.diagnostics["kinetic_model"] == "kinec"
    assert result.diagnostics["kinetic_parameter_path"] == str(KINETICS_PATH.resolve())
    assert result.diagnostics["kinetic_parameter_sha256"] == hashlib.sha256(
        KINETICS_PATH.read_bytes()
    ).hexdigest()
    assert result.diagnostics["database_sha256"] == hashlib.sha256(
        DATABASE_PATH.read_bytes()
    ).hexdigest()
    assert result.database_sha256 == result.diagnostics["database_sha256"]
    assert result.kinetic_parameter_sha256 == result.diagnostics["kinetic_parameter_sha256"]

    raw = _source_case_with_output(tmp_path / "thermodynamic-output")
    raw["minerals"][0]["name"] = "Not-A-Thermodynamic-Mineral"
    raw["postprocessing"]["requested_minerals"] = ["Not-A-Thermodynamic-Mineral"]
    result = run_simulation(load_case(_write_case(tmp_path, raw)))
    assert result.diagnostics["failed_stage"] == "mapping"
    assert "missing thermodynamic mineral" in result.diagnostics["error_message"]


def test_mapping_report_location_and_output_toggle(tmp_path: Path) -> None:
    raw = _source_case_with_output(tmp_path / "mapping-output")
    case = load_case(_write_case(tmp_path, raw))
    mapping = build_kinetic_mapping(case, load_database(case), load_kinetic_parameters(case))
    output_dir = write_kinetic_mapping(case, mapping)
    with (output_dir / "debug" / "mineral_connection.csv").open(newline="", encoding="utf-8") as stream:
        assert list(csv.DictReader(stream))[0]["status"] == "active"

    raw = _source_case_with_output(tmp_path / "mapping-disabled")
    raw["outputs"]["debug"]["mineral_connection"] = False
    case = load_case(_write_case(tmp_path, raw))
    write_kinetic_mapping(case, mapping)
    assert not (case.output_dir / "debug" / "mineral_connection.csv").exists()


def test_synthetic_fixed_fugacity_variant_validates_and_uses_staged_workflow() -> None:
    raw = _read_yaml(SYNTHETIC_CASE_PATH)
    raw["co2"] = {
        "mode": "fixed_fugacity",
        "gas_species": "CO2(g)",
        "fugacity_bar": 1.0,
    }
    raw["solver"]["workflow"] = {
        "mode": "fixed_fugacity_initial_equilibrium_then_closed_kinetics"
    }
    config = CaseConfig.model_validate(raw)
    assert [mineral.name for mineral in config.minerals] == ["Calcite"]
    assert config.solver.workflow.mode == "fixed_fugacity_initial_equilibrium_then_closed_kinetics"
    assert config.solver.timestep.mode == "fixed"


def test_deterministic_timeseries_column_order(tmp_path: Path) -> None:
    case = load_case(_write_case(tmp_path, _source_case_with_output(tmp_path / "outputs")))
    assert timeseries_columns(case) == [
        "time_s",
        "time_days",
        "stage",
        "pH",
        "ionic_strength_molal",
        "alkalinity_eq_per_l",
        "species_amount_mol::H+",
        "species_amount_mol::HCO3-",
        "species_amount_mol::CO3-2",
        "species_molality_mol_kgw::H+",
        "species_molality_mol_kgw::HCO3-",
        "species_molality_mol_kgw::CO3-2",
        "mineral_amount_mol::Calcite",
        "mineral_delta_mol::Calcite",
        "saturation_index::Calcite",
        "solver_succeeded",
        "solver_iterations",
        "dt_s",
    ]


def test_zero_initial_mineral_summary_avoids_divide_by_zero(tmp_path: Path) -> None:
    case = load_case(_write_case(tmp_path, _source_case_with_output(tmp_path / "outputs")))
    rows = [
        {"mineral_amount_mol::Calcite": 0.0, "saturation_index::Calcite": -1.0},
        {"mineral_amount_mol::Calcite": 1.0, "saturation_index::Calcite": 0.5},
    ]
    result = SimpleNamespace(initial_row=rows[0], final_row=rows[-1])
    summary = mineral_summary_rows(case, result)[0]
    assert summary["delta_percent"] is None
    assert summary["net_change"] == "precipitation_from_zero"


def test_base_output_package_and_disabled_plot_behavior(tmp_path: Path) -> None:
    raw = _source_case_with_output(tmp_path / "output-package")
    raw["outputs"]["plots"]["enabled"] = False
    raw["outputs"]["plots"].update(
        {
            "pH": False,
            "mineral_change": False,
            "saturation_index": False,
        }
    )
    case = load_case(_write_case(tmp_path, raw))
    mapping = build_kinetic_mapping(case, load_database(case), load_kinetic_parameters(case))
    write_kinetic_mapping(case, mapping)

    row = {
        "time_s": 0.0,
        "time_days": 0.0,
        "stage": "initial_state",
        "pH": 7.0,
        "ionic_strength_molal": 1.0,
        "alkalinity_eq_per_l": 0.0,
        "species_amount_mol::H+": 0.0,
        "species_amount_mol::HCO3-": 0.0,
        "species_amount_mol::CO3-2": 0.0,
        "species_molality_mol_kgw::H+": 0.0,
        "species_molality_mol_kgw::HCO3-": 0.0,
        "species_molality_mol_kgw::CO3-2": 0.0,
        "mineral_amount_mol::Calcite": 1.0,
        "mineral_delta_mol::Calcite": 0.0,
        "saturation_index::Calcite": 0.0,
        "solver_succeeded": None,
        "solver_iterations": None,
        "dt_s": 0.0,
    }
    history = {
        "step_index": 0,
        "time_start_s": 0.0,
        "time_end_s": 0.0,
        "dt_s": 0.0,
        "stage": "initial_state",
        "accepted": True,
        "solver_succeeded": None,
        "iterations": None,
        "wall_time_s": 0.0,
        "failure_reason": "",
    }

    class FakeState:
        def output(self, path: str) -> None:
            Path(path).write_text("test state", encoding="utf-8")

    diagnostics = {
        "run_started_at": "2026-06-14T00:00:00+00:00",
        "run_finished_at": "2026-06-14T00:00:01+00:00",
        "simulation_completed": True,
    }
    result = SimulationResult(
        rows=[deepcopy(row)],
        kinetic_mapping=mapping,
        solver_history=[history],
        diagnostics=diagnostics,
        initial_state=FakeState(),
        final_state=FakeState(),
    )
    write_outputs(case, result)
    expected = {
        "manifest.json",
        "diagnostics.json",
        "timeseries.csv",
        "mineral_summary.csv",
        "aqueous_summary.csv",
        "solver_history.csv",
        "debug/mineral_connection.csv",
        "debug/resolved_config.yaml",
        "debug/final_state.txt",
    }
    actual = {
        str(path.relative_to(case.output_dir)).replace("\\", "/")
        for path in case.output_dir.rglob("*")
        if path.is_file()
    }
    assert expected == actual
    assert not (case.output_dir / "plots").exists()
    manifest = json.loads((case.output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["time_semantics"] == {
        "canonical_unit": "second",
        "duration_s": case.duration_s,
        "timestep_mode": "fixed",
        "configured_fixed_dt_s": case.dt_s,
        "configured_adaptive_dt_initial_s": None,
        "configured_adaptive_dt_min_s": None,
        "configured_adaptive_dt_max_s": None,
        "base_internal_steps": case.base_internal_step_count,
            "resolved_internal_steps": case.internal_step_count,
            "minimum_possible_accepted_steps": case.minimum_accepted_steps,
        "solver_target_rule": (
            "absolute fixed-grid targets split at requested output and checkpoint timestamps"
        ),
        "output_state_rule": "accepted states only; no interpolation",
        "output_schedule": case.output_schedule_summary(),
        "checkpoint_schedule": case.checkpoint_schedule_summary(),
        "restart": {"enabled": False, "from_checkpoint": None},
    }


def test_optional_scientific_audit_outputs_are_config_controlled(tmp_path: Path) -> None:
    raw = _source_case_with_output(tmp_path / "audit-output")
    raw["postprocessing"]["reaction_rates"] = True
    raw["postprocessing"]["element_budget"] = {
        "enabled": True,
        "elements": ["C"],
        "species": {"HCO3-": {"C": 1.0}, "CO3-2": {"C": 1.0}},
        "minerals": {"Calcite": {"C": 1.0}},
        "gas_species": {},
    }
    raw["postprocessing"]["carbon_inventory"] = {
        "enabled": True,
        "carbon_species": {"HCO3-": 1.0, "CO3-2": 1.0},
        "carbon_minerals": {"Calcite": 1.0},
        "carbon_gas_species": {},
    }
    raw["postprocessing"]["regime_classification"] = {"enabled": True}
    raw["postprocessing"]["surface_area_audit"] = {"enabled": True}
    raw["postprocessing"]["workflow_comparison"] = {"enabled": True}
    raw["outputs"]["plots"]["enabled"] = False
    raw["outputs"]["plots"].update({"pH": False, "mineral_change": False, "saturation_index": False})
    raw["outputs"]["summaries"].update(
        {
            "reaction_rates": True,
            "reaction_rate_validation": True,
            "carbon_inventory": True,
            "element_budget": True,
            "regime_classification": True,
            "surface_area_audit": True,
            "workflow_comparison": True,
        }
    )
    case = load_case(_write_case(tmp_path, raw))
    mapping = build_kinetic_mapping(case, load_database(case), load_kinetic_parameters(case))
    write_kinetic_mapping(case, mapping)

    row = {
        "time_s": 0.0,
        "time_days": 0.0,
        "stage": "initial_state",
        "pH": 7.0,
        "ionic_strength_molal": 1.0,
        "alkalinity_eq_per_l": 0.0,
        "species_amount_mol::H+": 0.0,
        "species_amount_mol::HCO3-": 2.0,
        "species_amount_mol::CO3-2": 3.0,
        "species_molality_mol_kgw::H+": 0.0,
        "species_molality_mol_kgw::HCO3-": 2.0,
        "species_molality_mol_kgw::CO3-2": 3.0,
        "mineral_amount_mol::Calcite": 4.0,
        "mineral_delta_mol::Calcite": 0.0,
        "saturation_index::Calcite": -1.0,
        "reaction_rate_mol_s::Calcite": 0.25,
        "reaction_rate_mol_m2_s::Calcite": 0.5,
        "reaction_rate_saturation_ratio::Calcite": 0.1,
        "reaction_rate_surface_area_m2::Calcite": 0.5,
        "reaction_rate_status::Calcite": "evaluated",
        "solver_succeeded": None,
        "solver_iterations": None,
        "dt_s": 0.0,
    }
    history = {
        "step_index": 0,
        "time_start_s": 0.0,
        "time_end_s": 0.0,
        "dt_s": 0.0,
        "stage": "initial_state",
        "accepted": True,
        "solver_succeeded": None,
        "iterations": None,
        "wall_time_s": 0.0,
        "failure_reason": "",
    }

    class FakeState:
        def output(self, path: str) -> None:
            Path(path).write_text("test state", encoding="utf-8")

    diagnostics = {
        "output_schema_version": "objective1_audit_v4",
        "run_started_at": "2026-06-14T00:00:00+00:00",
        "run_finished_at": "2026-06-14T00:00:01+00:00",
        "simulation_completed": True,
    }
    result = SimulationResult(
        rows=[row],
        kinetic_mapping=mapping,
        solver_history=[history],
        diagnostics=diagnostics,
        initial_state=FakeState(),
        final_state=FakeState(),
    )
    write_outputs(case, result)

    for name in [
        "reaction_rates.csv",
        "reaction_rate_validation.csv",
        "carbon_inventory.csv",
        "element_budget.csv",
        "regime_classification.csv",
        "surface_area_audit.csv",
        "workflow_comparison.csv",
    ]:
        assert (case.output_dir / name).is_file()


def test_redox_toggle_changes_only_redox_block() -> None:
    redox_off = _read_yaml(SYNTHETIC_CASE_PATH)
    redox_on = deepcopy(redox_off)
    redox_on["redox"] = {"enabled": True, "pe": 4.0, "apply_during": "kinetic_steps"}
    assert redox_on["redox"] == {"enabled": True, "pe": 4.0, "apply_during": "kinetic_steps"}
    assert redox_off["redox"] == {"enabled": False}
    redox_on["redox"] = redox_off["redox"]
    assert redox_on == redox_off

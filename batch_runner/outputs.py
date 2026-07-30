"""Orchestrate config-controlled output package writing."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from batch_runner.config import ResolvedCase
from batch_runner.manifest import build_manifest
from batch_runner.output_plots import write_plots
from batch_runner.output_tables import (
    AQUEOUS_SUMMARY_COLUMNS,
    MINERAL_SUMMARY_COLUMNS,
    SOLVER_HISTORY_COLUMNS,
    aqueous_summary_rows,
    mineral_summary_rows,
    timeseries_columns,
    timeseries_rows,
    write_csv,
)
from batch_runner.scientific_reports import (
    CARBON_INVENTORY_COLUMNS,
    ELEMENT_BUDGET_COLUMNS,
    KINEC_RATE_VALIDATION_COLUMNS,
    MINERAL_VOLUME_COLUMNS,
    POROSITY_PERMEABILITY_COLUMNS,
    REACTION_RATE_COLUMNS,
    REGIME_COLUMNS,
    SECONDARY_ASSEMBLAGE_COLUMNS,
    SURFACE_AREA_COLUMNS,
    VALIDATION_LEDGER_COLUMNS,
    carbon_inventory_rows,
    element_budget_rows,
    kinec_rate_validation_rows,
    mineral_volume_rows,
    porosity_permeability_rows,
    reaction_rate_rows,
    regime_classification_rows,
    secondary_mineral_assemblage_rows,
    surface_area_audit_rows,
    surrogate_dataset_columns,
    surrogate_dataset_rows,
    validation_ledger_rows,
    workflow_comparison_columns,
    workflow_comparison_rows,
)
from batch_runner.simulation import SimulationResult


MAPPING_COLUMNS = [
    "case_name",
    "mineral_name",
    "thermo_name",
    "kinetic_name",
    "kinetic",
    "thermodynamic_mineral_found",
    "kinec_yaml_record_found",
    "surface_area_present",
    "status",
    "reason",
]


def write_kinetic_mapping(case: ResolvedCase, mapping: list[dict]) -> Path:
    output_dir = case.output_dir
    output_dir.mkdir(parents=True, exist_ok=False)
    debug = case.config.outputs.debug
    if debug.enabled and debug.mineral_connection:
        debug_dir = output_dir / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        write_csv(debug_dir / "mineral_connection.csv", MAPPING_COLUMNS, mapping)
    return output_dir


def write_outputs(case: ResolvedCase, result: SimulationResult) -> Path:
    output_dir = case.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    if (output_dir / "results.csv").exists():
        raise FileExistsError(
            f"stale legacy results.csv exists in output directory; rerun into a fresh output_dir: {output_dir}"
        )
    written: list[Path] = []
    outputs = case.config.outputs
    completed = result.diagnostics["simulation_completed"]
    if not completed:
        result.diagnostics["partial_outputs_written"] = True
        result.diagnostics["scientific_outputs_omitted"] = True
        result.diagnostics["warnings"].append(
            "scientific summaries and plots were omitted because the simulation did not complete"
        )

    if outputs.timeseries.enabled:
        path = output_dir / "timeseries.csv"
        write_csv(path, timeseries_columns(case), timeseries_rows(case, result))
        written.append(path)
    if completed and outputs.summaries.mineral_summary:
        path = output_dir / "mineral_summary.csv"
        write_csv(path, MINERAL_SUMMARY_COLUMNS, mineral_summary_rows(case, result))
        written.append(path)
    if completed and outputs.summaries.aqueous_summary:
        path = output_dir / "aqueous_summary.csv"
        write_csv(path, AQUEOUS_SUMMARY_COLUMNS, aqueous_summary_rows(case, result))
        written.append(path)
    if completed and outputs.summaries.reaction_rates:
        path = output_dir / "reaction_rates.csv"
        write_csv(path, REACTION_RATE_COLUMNS, reaction_rate_rows(case, result))
        written.append(path)
    if completed and outputs.summaries.kinec_rate_validation:
        path = output_dir / "kinec_rate_validation.csv"
        write_csv(path, KINEC_RATE_VALIDATION_COLUMNS, kinec_rate_validation_rows(case, result))
        written.append(path)
    if completed and outputs.summaries.carbon_inventory:
        path = output_dir / "carbon_inventory.csv"
        write_csv(path, CARBON_INVENTORY_COLUMNS, carbon_inventory_rows(case, result))
        written.append(path)
    if completed and outputs.summaries.element_budget:
        path = output_dir / "element_budget.csv"
        write_csv(path, ELEMENT_BUDGET_COLUMNS, element_budget_rows(case, result))
        written.append(path)
    if completed and outputs.summaries.mineral_volume_change:
        path = output_dir / "mineral_volume_change.csv"
        write_csv(path, MINERAL_VOLUME_COLUMNS, mineral_volume_rows(case, result))
        written.append(path)
    if completed and outputs.summaries.regime_classification:
        path = output_dir / "regime_classification.csv"
        write_csv(path, REGIME_COLUMNS, regime_classification_rows(case, result))
        written.append(path)
    if completed and outputs.summaries.surface_area_audit:
        path = output_dir / "surface_area_audit.csv"
        write_csv(path, SURFACE_AREA_COLUMNS, surface_area_audit_rows(case))
        written.append(path)
    if completed and outputs.summaries.workflow_comparison:
        path = output_dir / "workflow_comparison.csv"
        write_csv(path, workflow_comparison_columns(case), workflow_comparison_rows(case, result))
        written.append(path)
    if completed and outputs.summaries.secondary_mineral_assemblage:
        path = output_dir / "secondary_mineral_assemblage.csv"
        write_csv(path, SECONDARY_ASSEMBLAGE_COLUMNS, secondary_mineral_assemblage_rows(case))
        written.append(path)
    if completed and outputs.summaries.surrogate_dataset:
        path = output_dir / "surrogate_dataset.csv"
        write_csv(path, surrogate_dataset_columns(case), surrogate_dataset_rows(case, result))
        written.append(path)
    if completed and outputs.summaries.validation_ledger:
        path = output_dir / "validation_ledger.csv"
        write_csv(path, VALIDATION_LEDGER_COLUMNS, validation_ledger_rows(case, result))
        written.append(path)
    if completed and outputs.summaries.porosity_permeability:
        path = output_dir / "porosity_permeability.csv"
        write_csv(path, POROSITY_PERMEABILITY_COLUMNS, porosity_permeability_rows(case, result))
        written.append(path)
    if outputs.solver_history.enabled:
        path = output_dir / "solver_history.csv"
        write_csv(path, SOLVER_HISTORY_COLUMNS, result.iter_solver_history())
        written.append(path)
    if outputs.diagnostics.enabled:
        path = output_dir / "diagnostics.json"
        _write_json(path, result.diagnostics)
        written.append(path)

    if completed:
        written.extend(write_plots(case, result, output_dir / "plots"))

    debug = outputs.debug
    if debug.enabled:
        debug_dir = output_dir / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        mapping_path = debug_dir / "mineral_connection.csv"
        if mapping_path.is_file():
            written.append(mapping_path)
        if debug.resolved_config:
            path = debug_dir / "resolved_config.yaml"
            with path.open("w", encoding="utf-8") as stream:
                yaml.safe_dump(case.as_dict(), stream, sort_keys=False)
            written.append(path)
        if debug.final_state:
            path = debug_dir / "final_state.txt"
            result.final_state.output(str(path))
            written.append(path)

    checkpoint_dir = output_dir / "checkpoints"
    if checkpoint_dir.is_dir():
        written.extend(sorted(path for path in checkpoint_dir.rglob("*") if path.is_file()))

    result.cleanup_streams()

    if outputs.manifest.enabled:
        path = output_dir / "manifest.json"
        relative_files = sorted(
            [str(item.relative_to(output_dir)).replace("\\", "/") for item in written] + ["manifest.json"]
        )
        _write_json(path, build_manifest(case, result, relative_files))
        written.append(path)
    return output_dir


def _write_json(path: Path, data: dict) -> None:
    with path.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")

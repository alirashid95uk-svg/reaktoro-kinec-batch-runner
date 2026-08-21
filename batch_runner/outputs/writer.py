"""Orchestrate config-controlled output package writing."""

from __future__ import annotations

import json
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Callable

import yaml

from batch_runner.config import ResolvedCase
from .audits import (
    CARBON_INVENTORY_COLUMNS,
    ELEMENT_BUDGET_COLUMNS,
    REACTION_RATE_COLUMNS,
    REACTION_RATE_VALIDATION_COLUMNS,
    SURFACE_AREA_COLUMNS,
    carbon_inventory_rows,
    element_budget_rows,
    reaction_rate_rows,
    reaction_rate_validation_rows,
    surface_area_audit_rows,
)
from .derived import (
    MINERAL_VOLUME_COLUMNS,
    POROSITY_PERMEABILITY_COLUMNS,
    REGIME_COLUMNS,
    SECONDARY_ASSEMBLAGE_COLUMNS,
    mineral_volume_rows,
    porosity_permeability_rows,
    regime_classification_rows,
    secondary_mineral_assemblage_rows,
    surrogate_dataset_columns,
    surrogate_dataset_rows,
    workflow_comparison_columns,
    workflow_comparison_rows,
)
from .manifest import build_manifest
from .plots import write_plots
from .tables import (
    AQUEOUS_SUMMARY_COLUMNS,
    MINERAL_SUMMARY_COLUMNS,
    aqueous_summary_rows,
    mineral_summary_rows,
    solver_history_columns,
    timeseries_columns,
    timeseries_rows,
)

if TYPE_CHECKING:
    from batch_runner.simulator import SimulationResult


MAPPING_COLUMNS = [
    "case_name",
    "mineral_name",
    "role",
    "kinetic_model",
    "thermodynamic_mineral_found",
    "kinetic_parameter_record_found",
    "surface_area_present",
    "status",
    "reason",
]


def write_kinetic_mapping(
    case: ResolvedCase,
    mapping: list[dict],
    csv_writer: Callable[..., None],
) -> Path:
    output_dir = case.output_dir
    output_dir.mkdir(parents=True, exist_ok=False)
    debug = case.config.outputs.debug
    if debug.enabled and debug.mineral_connection:
        debug_dir = output_dir / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        csv_writer(debug_dir / "mineral_connection.csv", MAPPING_COLUMNS, mapping)
    return output_dir


def write_outputs(
    case: ResolvedCase,
    result: SimulationResult,
    cancel_requested: Callable[[], bool] | None = None,
    *,
    csv_writer: Callable[..., None],
) -> Path:
    try:
        output_dir = _write_outputs(case, result, cancel_requested, csv_writer)
    except Exception as error:
        output_dir = case.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        output_traceback = traceback.format_exc()
        if result.exception_traceback:
            result.exception_traceback += f"\nSecondary output traceback:\n{output_traceback}"
        else:
            result.exception_traceback = output_traceback
        result.diagnostics["output_failure"] = {
            "failed_stage": "output_writing",
            "exception_type": type(error).__name__,
            "error_message": str(error),
        }
        result.diagnostics.update(
            {
                "partial_outputs_written": True,
                "scientific_outputs_omitted": True,
            }
        )
        result.diagnostics["warnings"].append(
            "output package writing failed; inspect output_completeness"
        )
        _discard_partial_surrogate(output_dir, result)
        _preserve_or_clean_staging_streams(output_dir, result)
        result.diagnostics["output_completeness"] = {
            "status": "partial",
            "files_written": _present_files(output_dir, include_manifest=False),
        }
        _write_json(output_dir / "diagnostics.json", result.diagnostics)
        result.diagnostics["output_completeness"]["files_written"] = _present_files(
            output_dir, include_manifest=False
        )
        _write_json(output_dir / "diagnostics.json", result.diagnostics)
        return output_dir

    status = (
        "complete"
        if result.diagnostics["simulation_completed"]
        and result.diagnostics.get("termination_reason") != "interrupted_during_output"
        else "partial"
    )
    result.diagnostics["output_completeness"] = {
        "status": status,
        "files_written": _present_files(output_dir),
    }
    if case.config.outputs.diagnostics.enabled:
        _write_json(output_dir / "diagnostics.json", result.diagnostics)
    if case.config.outputs.manifest.enabled:
        _write_json(
            output_dir / "manifest.json",
            build_manifest(case, result, _present_files(output_dir)),
        )
    return output_dir


def _write_outputs(
    case: ResolvedCase,
    result: SimulationResult,
    cancel_requested: Callable[[], bool] | None,
    csv_writer: Callable[..., None],
) -> Path:
    output_dir = case.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    if (output_dir / "results.csv").exists():
        raise FileExistsError(
            f"stale legacy results.csv exists in output directory; rerun into a fresh output_dir: {output_dir}"
        )
    written: list[Path] = []
    outputs = case.config.outputs
    completed = result.diagnostics["simulation_completed"]

    def scientific_output_allowed() -> bool:
        if not completed or result.diagnostics.get("termination_reason") == "interrupted_during_output":
            return False
        if cancel_requested is None or not cancel_requested():
            return True
        result.diagnostics.update(
            {
                "termination_reason": "interrupted_during_output",
                "cancellation_requested": True,
                "cancellation_boundary": "before_output_extraction",
                "partial_outputs_written": True,
                "scientific_outputs_omitted": True,
            }
        )
        result.diagnostics["warnings"].append(
            "cooperative cancellation requested before output extraction"
        )
        _discard_partial_surrogate(output_dir, result)
        return False

    if not completed:
        result.diagnostics["partial_outputs_written"] = True
        result.diagnostics["scientific_outputs_omitted"] = True
        result.diagnostics["warnings"].append(
            "scientific summaries and plots were omitted because the simulation did not complete"
        )

    if outputs.timeseries.enabled:
        path = output_dir / "timeseries.csv"
        csv_writer(path, timeseries_columns(case), timeseries_rows(case, result))
        written.append(path)
    if outputs.solver_history.enabled:
        path = output_dir / "solver_history.csv"
        csv_writer(path, solver_history_columns(case), result.iter_solver_history())
        written.append(path)
    scientific_output_allowed()
    if outputs.summaries.mineral_summary and scientific_output_allowed():
        path = output_dir / "mineral_summary.csv"
        csv_writer(path, MINERAL_SUMMARY_COLUMNS, mineral_summary_rows(case, result))
        written.append(path)
    if outputs.summaries.aqueous_summary and scientific_output_allowed():
        path = output_dir / "aqueous_summary.csv"
        csv_writer(path, AQUEOUS_SUMMARY_COLUMNS, aqueous_summary_rows(case, result))
        written.append(path)
    if outputs.summaries.reaction_rates and scientific_output_allowed():
        path = output_dir / "reaction_rates.csv"
        csv_writer(path, REACTION_RATE_COLUMNS, reaction_rate_rows(case, result))
        written.append(path)
    if outputs.summaries.reaction_rate_validation and scientific_output_allowed():
        path = output_dir / "reaction_rate_validation.csv"
        csv_writer(
            path,
            REACTION_RATE_VALIDATION_COLUMNS,
            reaction_rate_validation_rows(case, result),
        )
        written.append(path)
    if outputs.summaries.carbon_inventory and scientific_output_allowed():
        path = output_dir / "carbon_inventory.csv"
        csv_writer(path, CARBON_INVENTORY_COLUMNS, carbon_inventory_rows(case, result))
        written.append(path)
    if outputs.summaries.element_budget and scientific_output_allowed():
        path = output_dir / "element_budget.csv"
        csv_writer(path, ELEMENT_BUDGET_COLUMNS, element_budget_rows(case, result))
        written.append(path)
    if outputs.summaries.mineral_volume_change and scientific_output_allowed():
        path = output_dir / "mineral_volume_change.csv"
        csv_writer(path, MINERAL_VOLUME_COLUMNS, mineral_volume_rows(case, result))
        written.append(path)
    if outputs.summaries.regime_classification and scientific_output_allowed():
        path = output_dir / "regime_classification.csv"
        csv_writer(path, REGIME_COLUMNS, regime_classification_rows(case, result))
        written.append(path)
    if outputs.summaries.surface_area_audit and scientific_output_allowed():
        path = output_dir / "surface_area_audit.csv"
        csv_writer(path, SURFACE_AREA_COLUMNS, surface_area_audit_rows(case))
        written.append(path)
    if outputs.summaries.workflow_comparison and scientific_output_allowed():
        path = output_dir / "workflow_comparison.csv"
        csv_writer(path, workflow_comparison_columns(case), workflow_comparison_rows(case, result))
        written.append(path)
    if outputs.summaries.secondary_mineral_assemblage and scientific_output_allowed():
        path = output_dir / "secondary_mineral_assemblage.csv"
        csv_writer(path, SECONDARY_ASSEMBLAGE_COLUMNS, secondary_mineral_assemblage_rows(case))
        written.append(path)
    if outputs.summaries.surrogate_dataset and scientific_output_allowed():
        path = output_dir / "surrogate_dataset.csv"
        csv_writer(path, surrogate_dataset_columns(case), surrogate_dataset_rows(case, result))
        written.append(path)
    if outputs.summaries.porosity_permeability and scientific_output_allowed():
        path = output_dir / "porosity_permeability.csv"
        csv_writer(path, POROSITY_PERMEABILITY_COLUMNS, porosity_permeability_rows(case, result))
        written.append(path)
    if outputs.diagnostics.enabled:
        path = output_dir / "diagnostics.json"
        _write_json(path, result.diagnostics)
        written.append(path)

    if scientific_output_allowed():
        written.extend(write_plots(case, result, output_dir / "plots"))
    scientific_output_allowed()

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
        if debug.final_state and result.final_state is not None:
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


def _present_files(output_dir: Path, *, include_manifest: bool = True) -> list[str]:
    files = sorted(
        path.relative_to(output_dir).as_posix()
        for path in output_dir.rglob("*")
        if path.is_file() and (include_manifest or path.name != "manifest.json")
    )
    return files


def _write_json(path: Path, data: dict) -> None:
    with path.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")


def _discard_partial_surrogate(output_dir: Path, result: SimulationResult) -> None:
    try:
        (output_dir / "surrogate_dataset.csv").unlink(missing_ok=True)
    except OSError as error:
        result.diagnostics["warnings"].append(
            f"could not remove partial surrogate_dataset.csv: {error}"
        )


def _preserve_or_clean_staging_streams(
    output_dir: Path,
    result: SimulationResult,
) -> None:
    streams = (
        ("row_stream_path", "partial_timeseries.jsonl"),
        ("solver_history_stream_path", "partial_solver_history.jsonl"),
    )
    for attribute, evidence_name in streams:
        source = getattr(result, attribute)
        if source is None or not source.is_file():
            continue
        try:
            debug_dir = output_dir / "debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            source.replace(debug_dir / evidence_name)
            setattr(result, attribute, None)
        except OSError as error:
            result.diagnostics["warnings"].append(
                f"could not classify staging stream {source.name}: {error}"
            )

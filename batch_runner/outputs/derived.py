"""Derive optional batch-scale summaries from accepted output records.

All calculations use configured coefficients or existing rows; no scientific
values are inferred or looked up here.  These summaries deliberately preserve
their limits: regime labels are batch tendencies, porosity is a volume-based
inference only, and permeability/capillary pressure remain unevaluated without
an implemented constitutive model.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from batch_runner.config import ResolvedCase

if TYPE_CHECKING:
    from batch_runner.simulator import SimulationResult


MINERAL_VOLUME_COLUMNS = [
    "mineral",
    "initial_amount_mol",
    "final_amount_mol",
    "delta_mol",
    "molar_volume_cm3_per_mol",
    "initial_volume_cm3",
    "final_volume_cm3",
    "delta_volume_cm3",
    "source",
    "evaluation_status",
]
REGIME_COLUMNS = [
    "case_name",
    "regime_label",
    "minerals_with_dissolution",
    "minerals_with_precipitation",
    "scope_note",
]
SECONDARY_ASSEMBLAGE_COLUMNS = [
    "mineral",
    "initial_amount_mol",
    "selection_reason",
    "evaluation_status",
]
POROSITY_PERMEABILITY_COLUMNS = [
    "case_name",
    "bulk_volume_cm3",
    "mineral_volume_delta_cm3",
    "porosity_change",
    "porosity_status",
    "permeability_status",
    "capillary_entry_pressure_status",
    "scope_note",
]


def mineral_volume_rows(case: ResolvedCase, result: SimulationResult) -> list[dict[str, Any]]:
    """Convert mineral amount changes to volume using configured molar volumes.

    Amounts are mol, configured molar volumes cm3/mol, and derived volumes cm3.
    A mineral is marked unevaluated when an amount or nonzero configured molar
    volume is unavailable; no fallback value is supplied.
    """
    config = case.config.postprocessing.mineral_volume_change
    initial = result.initial_row
    final = result.final_row
    rows = []
    for mineral in case.config.minerals:
        name = mineral.name
        initial_amount = initial.get(f"mineral_amount_mol::{name}")
        final_amount = final.get(f"mineral_amount_mol::{name}")
        volume = config.molar_volumes_cm3_per_mol.get(name)
        status = "evaluated" if initial_amount is not None and final_amount is not None and volume else "not_evaluated"
        rows.append(
            {
                "mineral": name,
                "initial_amount_mol": initial_amount,
                "final_amount_mol": final_amount,
                "delta_mol": None if status != "evaluated" else final_amount - initial_amount,
                "molar_volume_cm3_per_mol": volume,
                "initial_volume_cm3": None if status != "evaluated" else initial_amount * volume,
                "final_volume_cm3": None if status != "evaluated" else final_amount * volume,
                "delta_volume_cm3": None if status != "evaluated" else (final_amount - initial_amount) * volume,
                "source": config.sources.get(name),
                "evaluation_status": status,
            }
        )
    return rows


def regime_classification_rows(case: ResolvedCase, result: SimulationResult) -> list[dict[str, Any]]:
    """Classify the sign pattern of requested batch mineral amount changes.

    ``delayed_precipitation`` denotes an intermediate negative delta followed
    by a positive final delta in stored rows.  The label is descriptive and is
    not a transport or fracture-sealing regime.
    """
    final = result.final_row
    dissolving = []
    precipitating = []
    delayed = []
    saw_negative = {name: False for name in case.config.postprocessing.requested_minerals}
    for row in result.iter_rows():
        for name in saw_negative:
            saw_negative[name] |= row[f"mineral_delta_mol::{name}"] < 0
    for name in case.config.postprocessing.requested_minerals:
        if final[f"mineral_delta_mol::{name}"] < 0:
            dissolving.append(name)
        if final[f"mineral_delta_mol::{name}"] > 0:
            precipitating.append(name)
        if saw_negative[name] and final[f"mineral_delta_mol::{name}"] > 0:
            delayed.append(name)
    if delayed:
        label = "delayed_precipitation"
    elif dissolving and precipitating:
        label = "mixed"
    elif dissolving:
        label = "net_dissolution"
    elif precipitating:
        label = "net_precipitation"
    else:
        label = "no_resolvable_change"
    return [
        {
            "case_name": case.config.case.name,
            "regime_label": label,
            "minerals_with_dissolution": ";".join(dissolving),
            "minerals_with_precipitation": ";".join(precipitating),
            "scope_note": "batch geochemical tendency only; not a transport or fracture-sealing prediction",
        }
    ]


def workflow_comparison_columns(case: ResolvedCase) -> list[str]:
    """Return ordered columns for cross-run workflow comparison rows."""
    return [
        "case_name",
        "workflow_mode",
        "co2_mode",
        "redox_enabled",
        "redox_apply_during",
        "initial_pH",
        "final_pH",
        "delta_pH",
        "final_time_s",
        "total_solver_wall_time_s",
        "max_solver_iterations",
    ] + [f"final_mineral_delta_mol::{name}" for name in case.config.postprocessing.requested_minerals]


def workflow_comparison_rows(case: ResolvedCase, result: SimulationResult) -> list[dict[str, Any]]:
    """Return one configured-workflow outcome and solver-cost summary row.

    The function summarizes this run only; meaningful cross-workflow comparison
    requires externally matched scientific inputs and accuracy assessment.
    """
    initial = result.initial_row
    final = result.final_row
    total_wall_time_s = 0.0
    max_iterations = None
    for record in result.iter_solver_history():
        total_wall_time_s += record["wall_time_s"]
        iterations = record["iterations"]
        if iterations is not None:
            max_iterations = iterations if max_iterations is None else max(max_iterations, iterations)
    row = {
        "case_name": case.config.case.name,
        "workflow_mode": case.config.solver.workflow.mode,
        "co2_mode": case.config.co2.mode,
        "redox_enabled": case.config.redox.enabled,
        "redox_apply_during": case.config.redox.apply_during,
        "initial_pH": initial["pH"],
        "final_pH": final["pH"],
        "delta_pH": final["pH"] - initial["pH"],
        "final_time_s": final["time_s"],
        "total_solver_wall_time_s": total_wall_time_s,
        "max_solver_iterations": max_iterations,
    }
    for name in case.config.postprocessing.requested_minerals:
        row[f"final_mineral_delta_mol::{name}"] = final[f"mineral_delta_mol::{name}"]
    return [row]


def secondary_mineral_assemblage_rows(case: ResolvedCase) -> list[dict[str, Any]]:
    """Document configured equilibrium minerals and their selection rationale.

    This is a configuration audit, not evidence that a secondary mineral
    appeared during simulation.
    """
    rows = []
    for mineral in case.config.minerals:
        if mineral.role != "equilibrium":
            continue
        rows.append(
            {
                "mineral": mineral.name,
                "initial_amount_mol": mineral.initial_amount.value if mineral.initial_amount else None,
                "selection_reason": mineral.selection_reason,
                "evaluation_status": "documented" if mineral.selection_reason else "missing_selection_reason",
            }
        )
    return rows


def surrogate_dataset_columns(case: ResolvedCase) -> list[str]:
    """Return ordered provenance and final-state columns for surrogate export."""
    return [
        "case_name",
        "output_schema_version",
        "database_source",
        "database_value",
        "database_sha256",
        "kinetic_model",
        "kinetic_parameter_sha256",
        "workflow_mode",
        "co2_mode",
        "redox_enabled",
        "validity_domain",
        "final_time_s",
        "final_pH",
        "final_ionic_strength_molal",
        "final_alkalinity_eq_per_l",
    ] + [f"final_species_molality_mol_kgw::{name}" for name in case.config.postprocessing.requested_species] + [
        f"final_mineral_delta_mol::{name}" for name in case.config.postprocessing.requested_minerals
    ]


def surrogate_dataset_rows(case: ResolvedCase, result: SimulationResult) -> list[dict[str, Any]]:
    """Return one provenance-rich final-state row for downstream datasets.

    The row preserves the configured validity-domain label and input hashes.
    Output writing withholds this artifact unless the simulation completed.
    """
    final = result.final_row
    row = {
        "case_name": case.config.case.name,
        "output_schema_version": result.diagnostics["output_schema_version"],
        "database_source": case.config.database.source,
        "database_value": str(case.database_path or case.config.database.name),
        "database_sha256": result.database_sha256,
        "kinetic_model": case.config.kinetics.model,
        "kinetic_parameter_sha256": result.kinetic_parameter_sha256,
        "workflow_mode": case.config.solver.workflow.mode,
        "co2_mode": case.config.co2.mode,
        "redox_enabled": case.config.redox.enabled,
        "validity_domain": case.config.postprocessing.surrogate_dataset.validity_domain,
        "final_time_s": final["time_s"],
        "final_pH": final["pH"],
        "final_ionic_strength_molal": final["ionic_strength_molal"],
        "final_alkalinity_eq_per_l": final["alkalinity_eq_per_l"],
    }
    for name in case.config.postprocessing.requested_species:
        row[f"final_species_molality_mol_kgw::{name}"] = final[f"species_molality_mol_kgw::{name}"]
    for name in case.config.postprocessing.requested_minerals:
        row[f"final_mineral_delta_mol::{name}"] = final[f"mineral_delta_mol::{name}"]
    return [row]


def porosity_permeability_rows(case: ResolvedCase, result: SimulationResult) -> list[dict[str, Any]]:
    """Return the optional mineral-volume porosity inference and explicit limits.

    When bulk volume and all requested molar volumes are configured, porosity
    change is ``-mineral_volume_delta / bulk_volume``.  Permeability and
    capillary entry pressure are always marked unevaluated because this batch
    model implements no update law for either quantity.
    """
    config = case.config.postprocessing.porosity_permeability
    volume_config = case.config.postprocessing.mineral_volume_change
    status = "not_evaluated"
    volume_delta = None
    porosity_change = None
    if config.bulk_volume_cm3 is not None and volume_config.enabled:
        missing = [
            name
            for name in case.config.postprocessing.requested_minerals
            if name not in volume_config.molar_volumes_cm3_per_mol
        ]
        if not missing:
            final = result.final_row
            volume_delta = sum(
                final[f"mineral_delta_mol::{name}"] * volume_config.molar_volumes_cm3_per_mol[name]
                for name in case.config.postprocessing.requested_minerals
            )
            porosity_change = -volume_delta / config.bulk_volume_cm3
            status = "evaluated"
    return [
        {
            "case_name": case.config.case.name,
            "bulk_volume_cm3": config.bulk_volume_cm3,
            "mineral_volume_delta_cm3": volume_delta,
            "porosity_change": porosity_change,
            "porosity_status": status,
            "permeability_status": "not_evaluated_no_update_law",
            "capillary_entry_pressure_status": "not_evaluated_no_update_law",
            "scope_note": "batch mineral-volume inference only; no transport or fracture permeability is inferred",
        }
    ]

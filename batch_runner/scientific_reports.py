"""Optional Objective 1 scientific audit tables."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from batch_runner.config import ResolvedCase
from batch_runner.simulation import SimulationResult


REACTION_RATE_COLUMNS = [
    "time_s",
    "time_days",
    "mineral",
    "rate_mol_s",
    "rate_surface_normalized",
    "saturation_index",
    "saturation_ratio",
    "surface_area_value",
    "surface_area_unit",
    "rate_evaluation_status",
]
KINEC_RATE_VALIDATION_COLUMNS = [
    "time_s",
    "time_days",
    "mineral",
    "saturation_index",
    "expected_rate_sign_from_si",
    "observed_rate_sign",
    "sign_check",
    "scope_note",
]
CARBON_INVENTORY_COLUMNS = [
    "time_s",
    "time_days",
    "aqueous_carbon_mol",
    "gas_carbon_mol",
    "mineral_carbon_mol",
    "total_carbon_mol",
    "initial_total_carbon_mol",
    "carbon_balance_error_mol",
    "carbon_balance_error_percent",
]
ELEMENT_BUDGET_COLUMNS = [
    "time_s",
    "time_days",
    "element",
    "aqueous_mol",
    "mineral_mol",
    "gas_mol",
    "total_mol",
    "initial_total_mol",
    "delta_mol",
    "relative_error_percent",
]
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
SURFACE_AREA_COLUMNS = [
    "mineral",
    "role",
    "surface_area_value",
    "surface_area_unit",
    "surface_area_basis",
    "surface_area_provenance",
    "comparability_status",
]
SECONDARY_ASSEMBLAGE_COLUMNS = [
    "mineral",
    "thermo_name",
    "initial_amount_mol",
    "selection_reason",
    "evaluation_status",
]
VALIDATION_LEDGER_COLUMNS = [
    "quantity",
    "target_value",
    "unit",
    "uncertainty",
    "source",
    "runtime_value",
    "difference",
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


def reaction_rate_rows(case: ResolvedCase, result: SimulationResult) -> list[dict[str, Any]]:
    rows = []
    kinetic_minerals = [mineral for mineral in case.config.minerals if mineral.role == "kinetic"]
    for row in result.rows:
        for mineral in kinetic_minerals:
            name = mineral.name
            rows.append(
                {
                    "time_s": row["time_s"],
                    "time_days": row["time_days"],
                    "mineral": name,
                    "rate_mol_s": row[f"reaction_rate_mol_s::{name}"],
                    "rate_surface_normalized": row[f"reaction_rate_surface_normalized::{name}"],
                    "saturation_index": row[f"saturation_index::{name}"],
                    "saturation_ratio": row[f"reaction_rate_saturation_ratio::{name}"],
                    "surface_area_value": mineral.surface_area.value,
                    "surface_area_unit": mineral.surface_area.unit,
                    "rate_evaluation_status": row[f"reaction_rate_status::{name}"],
                }
            )
    return rows


def kinec_rate_validation_rows(case: ResolvedCase, result: SimulationResult) -> list[dict[str, Any]]:
    rows = []
    for row in reaction_rate_rows(case, result):
        expected = _expected_rate_sign(row["saturation_index"])
        observed = _sign(row["rate_mol_s"])
        rows.append(
            {
                "time_s": row["time_s"],
                "time_days": row["time_days"],
                "mineral": row["mineral"],
                "saturation_index": row["saturation_index"],
                "expected_rate_sign_from_si": expected,
                "observed_rate_sign": observed,
                "sign_check": "passed" if expected == observed else "failed",
                "scope_note": "batch rate-sign diagnostic; not a transport or fracture-sealing result",
            }
        )
    return rows


def carbon_inventory_rows(case: ResolvedCase, result: SimulationResult) -> list[dict[str, Any]]:
    config = case.config.postprocessing.carbon_inventory
    rows = []
    initial_total = None
    for row in result.rows:
        aqueous = _weighted_sum(row, "species_amount_mol", config.carbon_species)
        gas = _weighted_sum(row, "species_amount_mol", config.carbon_gas_species)
        mineral = _weighted_sum(row, "mineral_amount_mol", config.carbon_minerals)
        total = aqueous + gas + mineral
        if initial_total is None:
            initial_total = total
        rows.append(
            {
                "time_s": row["time_s"],
                "time_days": row["time_days"],
                "aqueous_carbon_mol": aqueous,
                "gas_carbon_mol": gas,
                "mineral_carbon_mol": mineral,
                "total_carbon_mol": total,
                "initial_total_carbon_mol": initial_total,
                "carbon_balance_error_mol": total - initial_total,
                "carbon_balance_error_percent": _percent_error(total - initial_total, initial_total),
            }
        )
    return rows


def element_budget_rows(case: ResolvedCase, result: SimulationResult) -> list[dict[str, Any]]:
    config = case.config.postprocessing.element_budget
    initial_totals: dict[str, float] = {}
    rows = []
    for row in result.rows:
        for element in config.elements:
            aqueous = _element_sum(row, "species_amount_mol", config.species, element)
            mineral = _element_sum(row, "mineral_amount_mol", config.minerals, element)
            gas = _element_sum(row, "species_amount_mol", config.gas_species, element)
            total = aqueous + mineral + gas
            initial = initial_totals.setdefault(element, total)
            rows.append(
                {
                    "time_s": row["time_s"],
                    "time_days": row["time_days"],
                    "element": element,
                    "aqueous_mol": aqueous,
                    "mineral_mol": mineral,
                    "gas_mol": gas,
                    "total_mol": total,
                    "initial_total_mol": initial,
                    "delta_mol": total - initial,
                    "relative_error_percent": _percent_error(total - initial, initial),
                }
            )
    return rows


def mineral_volume_rows(case: ResolvedCase, result: SimulationResult) -> list[dict[str, Any]]:
    config = case.config.postprocessing.mineral_volume_change
    initial = result.rows[0]
    final = result.rows[-1]
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
    final = result.rows[-1]
    dissolving = []
    precipitating = []
    delayed = []
    for name in case.config.postprocessing.requested_minerals:
        deltas = [row[f"mineral_delta_mol::{name}"] for row in result.rows]
        if final[f"mineral_delta_mol::{name}"] < 0:
            dissolving.append(name)
        if final[f"mineral_delta_mol::{name}"] > 0:
            precipitating.append(name)
        if min(deltas) < 0 and final[f"mineral_delta_mol::{name}"] > 0:
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


def surface_area_audit_rows(case: ResolvedCase) -> list[dict[str, Any]]:
    kinetic = [mineral for mineral in case.config.minerals if mineral.role == "kinetic"]
    comparable = len({(m.surface_area.unit, m.surface_area_basis) for m in kinetic}) == 1 and all(
        m.surface_area_basis for m in kinetic
    )
    status = "comparable_within_case" if comparable else "mixed_or_not_configured"
    return [
        {
            "mineral": mineral.name,
            "role": mineral.role,
            "surface_area_value": mineral.surface_area.value if mineral.surface_area else None,
            "surface_area_unit": mineral.surface_area.unit if mineral.surface_area else None,
            "surface_area_basis": mineral.surface_area_basis,
            "surface_area_provenance": mineral.surface_area_provenance,
            "comparability_status": status if mineral.role == "kinetic" else "not_applicable",
        }
        for mineral in case.config.minerals
    ]


def workflow_comparison_columns(case: ResolvedCase) -> list[str]:
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
    initial = result.rows[0]
    final = result.rows[-1]
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
        "total_solver_wall_time_s": sum(record["wall_time_s"] for record in result.solver_history),
        "max_solver_iterations": max(
            (record["iterations"] for record in result.solver_history if record["iterations"] is not None),
            default=None,
        ),
    }
    for name in case.config.postprocessing.requested_minerals:
        row[f"final_mineral_delta_mol::{name}"] = final[f"mineral_delta_mol::{name}"]
    return [row]


def secondary_mineral_assemblage_rows(case: ResolvedCase) -> list[dict[str, Any]]:
    rows = []
    for mineral in case.config.minerals:
        if mineral.role != "equilibrium":
            continue
        rows.append(
            {
                "mineral": mineral.name,
                "thermo_name": mineral.thermo_name or mineral.name,
                "initial_amount_mol": mineral.initial_amount.value if mineral.initial_amount else None,
                "selection_reason": mineral.selection_reason,
                "evaluation_status": "documented" if mineral.selection_reason else "missing_selection_reason",
            }
        )
    return rows


def surrogate_dataset_columns(case: ResolvedCase) -> list[str]:
    return [
        "case_name",
        "output_schema_version",
        "database_source",
        "database_value",
        "database_sha256",
        "kinetic_yaml_sha256",
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
    final = result.rows[-1]
    row = {
        "case_name": case.config.case.name,
        "output_schema_version": result.diagnostics["output_schema_version"],
        "database_source": case.config.database.source,
        "database_value": str(case.database_path or case.config.database.name),
        "database_sha256": _sha256(case.database_path),
        "kinetic_yaml_sha256": _sha256(case.kinetics_path),
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


def validation_ledger_rows(case: ResolvedCase, result: SimulationResult) -> list[dict[str, Any]]:
    final = result.rows[-1]
    rows = []
    for target in case.config.validation.targets:
        runtime = final.get(target.quantity)
        diff = None if runtime is None else runtime - target.target_value
        if runtime is None:
            status = "not_evaluated"
        elif target.uncertainty is None:
            status = "evaluated_without_uncertainty"
        else:
            status = "within_uncertainty" if abs(diff) <= target.uncertainty else "outside_uncertainty"
        rows.append(
            {
                "quantity": target.quantity,
                "target_value": target.target_value,
                "unit": target.unit,
                "uncertainty": target.uncertainty,
                "source": target.source,
                "runtime_value": runtime,
                "difference": diff,
                "evaluation_status": status,
            }
        )
    return rows


def porosity_permeability_rows(case: ResolvedCase, result: SimulationResult) -> list[dict[str, Any]]:
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
            final = result.rows[-1]
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


def _weighted_sum(row: dict[str, Any], prefix: str, mapping: dict[str, float]) -> float:
    return sum(float(row[f"{prefix}::{name}"]) * coefficient for name, coefficient in mapping.items())


def _element_sum(
    row: dict[str, Any],
    prefix: str,
    mapping: dict[str, dict[str, float]],
    element: str,
) -> float:
    return sum(float(row[f"{prefix}::{name}"]) * stoich.get(element, 0.0) for name, stoich in mapping.items())


def _percent_error(delta: float, initial: float) -> float | None:
    return None if initial == 0 else 100.0 * delta / initial


def _sign(value: float) -> str:
    if value > 0:
        return "positive"
    if value < 0:
        return "negative"
    return "zero"


def _expected_rate_sign(saturation_index: float) -> str:
    if saturation_index < 0:
        return "positive"
    if saturation_index > 0:
        return "negative"
    return "zero"


def _sha256(path: Path | None) -> str | None:
    if path is None:
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

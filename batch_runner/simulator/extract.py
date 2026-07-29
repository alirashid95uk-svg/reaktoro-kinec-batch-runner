"""Deterministic result-row extraction."""

from typing import Any

import reaktoro as rkt

from batch_runner.Kinect_Custom_Rates import evaluate_kinec_rate
from batch_runner.config import ResolvedCase
from batch_runner.simulator.mapping import _kinetic_name, _thermo_name


def collect_row(
    case: ResolvedCase,
    state: Any,
    solver_record: dict[str, Any],
    initial_state: Any,
    kinec_params: Any | None = None,
) -> dict[str, Any]:
    time_s = float(solver_record["time_end_s"])
    aqueous = rkt.AqueousProps(state)
    row: dict[str, Any] = {
        "time_s": time_s,
        "time_days": time_s / 86400.0,
        "stage": solver_record["stage"],
        "pH": float(aqueous.pH()),
        "ionic_strength_molal": float(aqueous.ionicStrength()),
        "alkalinity_eq_per_l": float(aqueous.alkalinity()),
    }

    for species_name in case.config.postprocessing.requested_species:
        row[f"species_amount_mol::{species_name}"] = float(state.speciesAmount(species_name))
        row[f"species_molality_mol_kgw::{species_name}"] = float(aqueous.speciesMolality(species_name))

    for species_name in _budget_species_names(case):
        row.setdefault(f"species_amount_mol::{species_name}", float(state.speciesAmount(species_name)))

    minerals = {mineral.name: mineral for mineral in case.config.minerals}
    for display_name in _mineral_amount_names(case):
        mineral = minerals[display_name]
        thermo_name = _thermo_name(mineral)
        amount = float(state.speciesAmount(thermo_name))
        initial_amount = float(initial_state.speciesAmount(thermo_name))
        row[f"mineral_amount_mol::{display_name}"] = amount
        row[f"mineral_delta_mol::{display_name}"] = amount - initial_amount
        row[f"saturation_index::{display_name}"] = float(aqueous.saturationIndex(thermo_name))

    if case.config.postprocessing.reaction_rates:
        if kinec_params is None:
            raise ValueError("reaction-rate diagnostics require loaded Kinec parameters")
        props = rkt.ChemicalProps(state)
        for mineral in case.config.minerals:
            if mineral.role != "kinetic":
                continue
            display_name = mineral.name
            kinetic_name = _kinetic_name(mineral)
            diagnostic = evaluate_kinec_rate(kinec_params[kinetic_name], _thermo_name(mineral), props)
            row[f"reaction_rate_mol_s::{display_name}"] = diagnostic["rate_mol_s"]
            row[f"reaction_rate_surface_normalized::{display_name}"] = diagnostic[
                "surface_normalized_rate"
            ]
            row[f"reaction_rate_saturation_ratio::{display_name}"] = diagnostic["saturation_ratio"]
            row[f"reaction_rate_status::{display_name}"] = "evaluated"

    row["solver_succeeded"] = solver_record["solver_succeeded"]
    row["solver_iterations"] = solver_record["iterations"]
    row["dt_s"] = solver_record["dt_s"]
    return row


def _budget_species_names(case: ResolvedCase) -> list[str]:
    post = case.config.postprocessing
    names = set(post.element_budget.species) | set(post.carbon_inventory.carbon_species)
    names |= set(post.element_budget.gas_species) | set(post.carbon_inventory.carbon_gas_species)
    return sorted(names)


def _mineral_amount_names(case: ResolvedCase) -> list[str]:
    post = case.config.postprocessing
    names = set(post.requested_minerals)
    names |= set(post.element_budget.minerals)
    names |= set(post.carbon_inventory.carbon_minerals)
    names |= set(post.mineral_volume_change.molar_volumes_cm3_per_mol)
    if post.reaction_rates:
        names |= {mineral.name for mineral in case.config.minerals if mineral.role == "kinetic"}
    return [mineral.name for mineral in case.config.minerals if mineral.name in names]

"""Deterministic result-row extraction."""

from typing import Any

import reaktoro as rkt

from batch_runner.config import ResolvedCase


def collect_row(
    case: ResolvedCase,
    state: Any,
    solver_record: dict[str, Any],
    initial_state: Any,
    *,
    include_reaction_rates: bool = True,
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

    for element_name in case.config.postprocessing.requested_elements:
        row[f"element_molality_mol_kgw::{element_name}"] = float(
            aqueous.elementMolality(element_name)
        )

    for species_name in _budget_species_names(case):
        row.setdefault(f"species_amount_mol::{species_name}", float(state.speciesAmount(species_name)))

    minerals = {mineral.name: mineral for mineral in case.config.minerals}
    for name in _mineral_amount_names(case):
        mineral = minerals[name]
        mineral_index = _solid_species_index(state.system(), mineral.name)
        amount = float(state.speciesAmount(mineral_index))
        initial_amount = float(initial_state.speciesAmount(mineral_index))
        row[f"mineral_amount_mol::{name}"] = amount
        row[f"mineral_delta_mol::{name}"] = amount - initial_amount
        row[f"saturation_index::{name}"] = float(aqueous.saturationIndex(mineral.name))

    if include_reaction_rates and case.config.postprocessing.reaction_rates:
        row.update(collect_reaction_rate_fields(case, state))

    row["solver_succeeded"] = solver_record["solver_succeeded"]
    row["solver_iterations"] = solver_record["iterations"]
    row["dt_s"] = solver_record["dt_s"]
    return row


def collect_reaction_rate_fields(case: ResolvedCase, state: Any) -> dict[str, Any]:
    aqueous = rkt.AqueousProps(state)
    props = rkt.ChemicalProps(state)
    fields: dict[str, Any] = {}
    for mineral in case.config.minerals:
        if mineral.role != "kinetic":
            continue
        name = mineral.name
        rate_mol_s = float(props.reactionRate(name))
        surface_area_m2 = float(props.surfaceArea(name))
        fields[f"reaction_rate_mol_s::{name}"] = rate_mol_s
        fields[f"reaction_rate_mol_m2_s::{name}"] = (
            rate_mol_s / surface_area_m2 if surface_area_m2 > 0.0 else None
        )
        fields[f"reaction_rate_saturation_ratio::{name}"] = float(
            aqueous.saturationRatio(name)
        )
        fields[f"reaction_rate_surface_area_m2::{name}"] = surface_area_m2
        fields[f"reaction_rate_status::{name}"] = (
            "evaluated" if surface_area_m2 > 0.0 else "zero_live_surface_area"
        )
    return fields


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


def _solid_species_index(system: Any, name: str) -> int:
    species_index = 0

    for phase in system.phases():
        phase_species = phase.species()

        if phase.name() == name:
            if len(phase_species) != 1:
                raise ValueError(f"mineral phase is not a pure phase: {name}")
            return species_index

        species_index += len(phase_species)

    raise ValueError(f"mineral phase is not present in the chemical system: {name}")

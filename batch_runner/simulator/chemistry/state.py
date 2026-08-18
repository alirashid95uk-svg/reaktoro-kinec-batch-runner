"""Direct Reaktoro state and constraint construction."""

from typing import Any

import reaktoro as rkt

from batch_runner.config import ResolvedCase


def build_chemical_state(case: ResolvedCase, system: Any) -> Any:
    state = rkt.ChemicalState(system)
    state.temperature(case.config.physical.temperature_c, "celsius")
    state.pressure(case.config.physical.pressure_bar, "bar")

    for species_name, amount in case.config.brine.species_amounts.items():
        _require_system_species(system, species_name)
        state.set(species_name, amount.value, amount.unit)

    for mineral in case.config.minerals:
        if mineral.initial_amount is not None:
            mineral_index = _require_system_mineral(system, mineral.name)
            state.set(mineral_index, mineral.initial_amount.value, mineral.initial_amount.unit)

    if case.config.co2.mode == "finite":
        species_name = case.config.co2.gas_species
        amount = case.config.co2.initial_amount
        _require_system_species(system, species_name)
        state.set(species_name, amount.value, amount.unit)

    for species_name in case.config.postprocessing.requested_species:
        _require_system_species(system, species_name)
    for species_name in _postprocessing_species_amount_names(case):
        _require_system_species(system, species_name)
    return state


def _require_system_species(system: Any, name: str) -> None:
    try:
        system.species().index(name)
    except RuntimeError as exc:
        raise ValueError(f"species is not present in the constructed chemical system: {name}") from exc


def _require_system_mineral(system: Any, name: str) -> int:
    species_index = 0

    for phase in system.phases():
        phase_species = phase.species()

        if phase.name() == name:
            if len(phase_species) != 1:
                raise ValueError(f"mineral phase is not a pure phase: {name}")
            return species_index

        species_index += len(phase_species)

    raise ValueError(
        f"mineral phase is not present in the constructed chemical system: {name}"
    )


def _postprocessing_species_amount_names(case: ResolvedCase) -> set[str]:
    post = case.config.postprocessing
    names = set(post.element_budget.species)
    names.update(post.element_budget.gas_species)
    names.update(post.carbon_inventory.carbon_species)
    names.update(post.carbon_inventory.carbon_gas_species)
    return names

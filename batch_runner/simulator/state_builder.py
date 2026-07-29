"""Direct Reaktoro state and constraint construction."""

from typing import Any

import reaktoro as rkt

from batch_runner.config import ResolvedCase
from batch_runner.simulator.mapping import _thermo_name
from batch_runner.simulator.workflows import (
    ConstraintStage,
    constraints_apply,
    fixed_fugacity_applies,
    redox_applies,
)


def build_chemical_state(case: ResolvedCase, system: Any) -> Any:
    state = rkt.ChemicalState(system)
    state.temperature(case.config.physical.temperature_c, "celsius")
    state.pressure(case.config.physical.pressure_bar, "bar")

    for species_name, amount in case.config.brine.species_amounts.items():
        _require_system_species(system, species_name)
        state.set(species_name, amount.value, amount.unit)

    for mineral in case.config.minerals:
        if mineral.initial_amount is not None:
            thermo_name = _thermo_name(mineral)
            _require_system_species(system, thermo_name)
            state.set(thermo_name, mineral.initial_amount.value, mineral.initial_amount.unit)

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


def build_conditions(
    case: ResolvedCase,
    system: Any,
    state: Any,
    stage: ConstraintStage,
) -> tuple[Any | None, Any | None]:
    if not constraints_apply(case, stage):
        return None, None

    specs = rkt.EquilibriumSpecs.TP(system)
    if fixed_fugacity_applies(case, stage):
        specs.fugacity(case.config.co2.gas_species)
    if redox_applies(case, stage):
        specs.pE()

    conditions = rkt.EquilibriumConditions(specs)
    conditions.temperature(case.config.physical.temperature_c, "celsius")
    conditions.pressure(case.config.physical.pressure_bar, "bar")
    if fixed_fugacity_applies(case, stage):
        conditions.fugacity(case.config.co2.gas_species, case.config.co2.fugacity_bar, "bar")
    if redox_applies(case, stage):
        conditions.pE(case.config.redox.pe)
    conditions.setInitialComponentAmountsFromState(state)
    return specs, conditions


def _require_system_species(system: Any, name: str) -> None:
    try:
        system.species().index(name)
    except RuntimeError as exc:
        raise ValueError(f"species is not present in the constructed chemical system: {name}") from exc


def _postprocessing_species_amount_names(case: ResolvedCase) -> set[str]:
    post = case.config.postprocessing
    names = set(post.element_budget.species)
    names.update(post.element_budget.gas_species)
    names.update(post.carbon_inventory.carbon_species)
    names.update(post.carbon_inventory.carbon_gas_species)
    return names

"""Construct the configured Reaktoro phases, reactions, and surfaces.

Simulation preparation calls this module after database and kinetic-parameter
loading.  The construction is deliberately explicit: PHREEQC aqueous
activities, optional finite-CO2 gas, configured mineral phases, and one
reaction/surface pair per kinetic mineral.  It owns attachment, not the
scientific parameter values supplied by the selected kinetics source.
"""

from typing import Any

import reaktoro as rkt

from batch_runner.config import ResolvedCase
from batch_runner.simulator.kinetics.mapping import _require_thermodynamic_mineral
from batch_runner.simulator.kinetics.parameters import build_rate_model


def build_chemical_system(case: ResolvedCase, database: Any, params: Any | None = None) -> Any:
    """Return a Reaktoro ``ChemicalSystem`` matching the resolved case.

    Args:
        case: Validated case whose phase names and surface areas are used.
        database: Already-loaded PHREEQC thermodynamic database.
        params: Loaded parameter set when kinetics is enabled.

    Raises:
        ValueError: A mineral is absent from the database or enabled kinetics
            has no parameter set.
        Exception: Reaktoro cannot construct a phase, rate model, surface, or
            activity model from the configured inputs.

    Surface-area values and units are passed directly to Reaktoro; no basis
    conversion or inferred surface area is performed here.
    """
    for mineral in case.config.minerals:
        _require_thermodynamic_mineral(database, mineral.name)

    aqueous = rkt.AqueousPhase(rkt.speciate(case.config.brine.aqueous_elements))
    aqueous.setActivityModel(rkt.ActivityModelPhreeqc(database))
    components: list[Any] = [aqueous]

    if case.config.co2.mode == "finite":
        gas = rkt.GaseousPhase([case.config.co2.gas_species])
        gas.setActivityModel(rkt.ActivityModelPengRobinsonPhreeqc())
        components.append(gas)

    components.append(rkt.MineralPhases([mineral.name for mineral in case.config.minerals]))

    if case.config.kinetics.enabled:
        if params is None:
            raise ValueError("enabled kinetics requires loaded kinetic parameters")
        for mineral in case.config.minerals:
            if mineral.role != "kinetic":
                continue
            reaction = rkt.MineralReaction(mineral.name)
            reaction.setRateModel(build_rate_model(case, params, mineral.name))
            components.append(reaction)
            components.append(
                rkt.MineralSurface(
                    mineral.name,
                    mineral.surface_area.value,
                    mineral.surface_area.unit,
                )
            )

    return rkt.ChemicalSystem(database, *components)

"""Direct Reaktoro chemical-system construction."""

from typing import Any

import reaktoro as rkt

from batch_runner.Kinect_Custom_Rates import KinecParams, ReactionRateModelKinec
from batch_runner.config import ResolvedCase
from batch_runner.simulator.mapping import (
    _kinetic_name,
    _require_thermodynamic_mineral,
    _thermo_name,
)


def build_chemical_system(case: ResolvedCase, database: Any, params: KinecParams | None = None) -> Any:
    for mineral in case.config.minerals:
        _require_thermodynamic_mineral(database, _thermo_name(mineral))

    aqueous = rkt.AqueousPhase(rkt.speciate(case.config.brine.aqueous_elements))
    aqueous.setActivityModel(rkt.ActivityModelPhreeqc(database))
    components: list[Any] = [aqueous]

    if case.config.co2.mode == "finite":
        gas = rkt.GaseousPhase([case.config.co2.gas_species])
        gas.setActivityModel(rkt.ActivityModelPengRobinsonPhreeqc())
        components.append(gas)

    components.append(rkt.MineralPhases([_thermo_name(mineral) for mineral in case.config.minerals]))

    if case.config.kinetics.enabled:
        if params is None:
            raise ValueError("enabled kinetics requires loaded Kinec parameters")
        for mineral in case.config.minerals:
            if mineral.role != "kinetic":
                continue
            thermo_name = _thermo_name(mineral)
            kinetic_name = _kinetic_name(mineral)
            if kinetic_name not in params.data:
                raise KeyError(f"missing Kinec kinetic record: {kinetic_name}")
            reaction = rkt.MineralReaction(thermo_name)
            reaction.setRateModel(ReactionRateModelKinec(params, kinetic_name, thermo_name=thermo_name))
            components.append(reaction)
            components.append(
                rkt.MineralSurface(
                    thermo_name,
                    mineral.surface_area.value,
                    mineral.surface_area.unit,
                )
            )

    return rkt.ChemicalSystem(database, *components)

# Minimal Reaktoro Patterns

This note records the intended direct construction order for the simple
Reaktoro batch simulation runner. Values must come from a validated resolved
config; no scientific values should be introduced in code.

Official sources were checked sufficiently to guide these patterns. Use
`PSEUDOCODE` labels where syntax has not been locally tested. Exact Reaktoro
Python syntax must be verified during implementation.

## 1. Load the PHREEQC Database

```python
from reaktoro import PhreeqcDatabase

if config.database.source == "embedded":
    db = PhreeqcDatabase(config.database.name)
elif config.database.source == "local":
    db = PhreeqcDatabase.fromFile(config.database.path)
else:
    raise ValueError("unsupported database source")
```

Validate the config and local path before calling Reaktoro. No automatic
database selection. No fallback database. If a local database path fails,
stop and report the exact path.

## 2. Construct Phases

```python
from reaktoro import (
    ActivityModelPhreeqc,
    AqueousPhase,
    GaseousPhase,
    MineralPhases,
)

aqueous = AqueousPhase(config.brine.aqueous_species)
aqueous.setActivityModel(ActivityModelPhreeqc(db))

minerals = MineralPhases(config.minerals.names)
```

Create a gas phase only when explicitly enabled:

```python
from reaktoro import ActivityModelPengRobinsonPhreeqc

gas = GaseousPhase(config.co2.gas_species)
gas.setActivityModel(ActivityModelPengRobinsonPhreeqc())
```

The gas activity model must be an explicit config/project decision. Do not
create a gas phase for a gas-disabled case.

## 3. Construct the Chemical System

Equilibrium-only construction is direct:

```python
from reaktoro import ChemicalSystem

system = ChemicalSystem(db, aqueous, minerals)
```

When gas is enabled, include `gas` explicitly.

For kinetics, the chemical system must also include the configured reactions
and mineral surfaces. The exact Python assembly form should be verified
against the installed Reaktoro version:

```python
# PSEUDOCODE: assemble db, enabled phases, reactions, and surfaces
system = ChemicalSystem(db, enabled_phases, reactions, surfaces)
```

## 4. Construct the Chemical State

```python
from reaktoro import ChemicalState

state = ChemicalState(system)
state.temperature(config.physical.temperature_c, "celsius")
state.pressure(config.physical.pressure_bar, "bar")
state.set("H2O", config.brine.water_kg, "kg")

for species_name, amount in config.brine.species_amounts:
    state.set(species_name, amount.value, amount.unit)

for mineral_name, amount in config.minerals.initial_amounts:
    state.set(mineral_name, amount.value, amount.unit)
```

Every amount and unit must be explicit in the resolved config.

## 5. Run an Equilibrium Step

For a closed equilibrium calculation at the state's temperature and pressure:

```python
from reaktoro import equilibrate

result = equilibrate(state)
```

Fixed fugacity and other constraints require explicit equilibrium conditions.
Treat their exact setup as a separate feature and verify the official
constraint API before implementation.

## 6. Attach Mineral Kinetics

The default path uses Reaktoro's native Palandri-Kharaka model. Reaktoro
matches the reaction's exact mineral species name through the local YAML
record's `Mineral` and `OtherNames` fields:

```python
params = Params.local(config.kinetics.path)
reaction = MineralReaction(mineral_name)
reaction.setRateModel(ReactionRateModelPalandriKharaka(params))
surface = MineralSurface(mineral_name, surface_value, surface_unit)
```

When `model: kinec` is explicitly selected, `KinecParams` and
`ReactionRateModelKinec` are provided by the user-supplied adapter:

```python
from reaktoro import MineralReaction, MineralSurface

params = KinecParams.local(config.kinetics.path)

reaction = MineralReaction(mineral_name)
reaction.setRateModel(ReactionRateModelKinec(params, mineral_name))

surface = MineralSurface(mineral_name, surface_value, surface_unit)
```

Before a scientific run, validate the selected rate model, units, and
dissolution/precipitation sign against the selected Reaktoro interface. A
missing parameter record, kinetic-mineral surface area, or thermodynamic
mineral is a hard failure. No silent skipping.

## 7. Run a Kinetic Step

```python
from reaktoro import KineticsSolver

solver = KineticsSolver(system)
precondition_result = solver.precondition(state)
step_result = solver.solve(state, dt_s)
```

`dt_s` must come from the resolved config or deterministic timestep logic.
Check solver results and diagnostics after every step.

## 8. Extract Standard Outputs

```python
from reaktoro import AqueousProps

props = state.props()
aqueous_props = AqueousProps(state)

pH = aqueous_props.pH()
pE = aqueous_props.pE()
species_amount_mol = state.speciesAmount(species_name)
species_activity = props.speciesActivity(species_name)
mineral_amount_mol = state.speciesAmount(mineral_name)
saturation_ratio = aqueous_props.saturationRatio(mineral_name)
```

For configured reaction rates, use the attached runtime model:

```python
rate_mol_s = props.reactionRate(mineral_name)
live_surface_area_m2 = props.surfaceArea(mineral_name)
```

Record units, solver status, and the resolved config with outputs.

"""
Equilibrate Jayasekara brine with a fixed CO2 fugacity.

Target:
    Reaktoro 2.13.0
    T = 40 °C
    P = 100 bar
    fCO2 = 57.77 bar
    1 kg H2O
    4.5 wt% NaCl -> 0.8062660077228978 mol Na+ and Cl-

No minerals are present.

Outputs:
    1. Equilibrium pH
    2. Total aqueous carbon molality
    3. Element amounts after CO2 equilibration
    4. Non-zero equilibrated aqueous species amounts
    5. Two CSV files for later use
"""

import csv

import reaktoro as rkt


TEMPERATURE_C = 40.0
PRESSURE_BAR = 100.0
CO2_FUGACITY_BAR = 57.77

# Existing Jayasekara repository interpretation of 4.5 wt% NaCl
NACL_MOL = 0.8062660077228978


# ---------------------------------------------------------------------
# Thermodynamic database and aqueous system
# ---------------------------------------------------------------------

database = rkt.PhreeqcDatabase.withName("llnl.dat")

aqueous = rkt.AqueousPhase(
    rkt.speciate(["H", "O", "Na", "Cl", "C"])
)
aqueous.setActivityModel(rkt.ActivityModelPhreeqc(database))

# No gas phase is added.
# CO2 is an external reservoir imposed through its fugacity.
system = rkt.ChemicalSystem(database, aqueous)


# ---------------------------------------------------------------------
# Unequilibrated Jayasekara brine
# ---------------------------------------------------------------------

state = rkt.ChemicalState(system)

state.temperature(TEMPERATURE_C, "celsius")
state.pressure(PRESSURE_BAR, "bar")

state.set("H2O", 1.0, "kg")
state.set("Na+", NACL_MOL, "mol")
state.set("Cl-", NACL_MOL, "mol")


# ---------------------------------------------------------------------
# Equilibrium specification:
# T, P and CO2 fugacity fixed
# ---------------------------------------------------------------------

specs = rkt.EquilibriumSpecs.TP(system)
specs.fugacity("CO2(g)")

conditions = rkt.EquilibriumConditions(specs)

conditions.temperature(TEMPERATURE_C, "celsius")
conditions.pressure(PRESSURE_BAR, "bar")
conditions.fugacity("CO2(g)", CO2_FUGACITY_BAR, "bar")

# Initialise conserved components from the NaCl brine.
# CO2 can then enter from the external reservoir until fCO2 = 57.77 bar.
conditions.setInitialComponentAmountsFromState(state)


# ---------------------------------------------------------------------
# Equilibrate
# ---------------------------------------------------------------------

solver = rkt.EquilibriumSolver(specs)
result = solver.solve(state, conditions)

if not result.succeeded():
    raise RuntimeError("Reaktoro equilibrium calculation failed.")


# ---------------------------------------------------------------------
# Equilibrium aqueous properties
# ---------------------------------------------------------------------

props = rkt.AqueousProps(state)

print("\n=== EQUILIBRATED JAYASEKARA BRINE ===")
print(f"Temperature       : {TEMPERATURE_C:.6g} °C")
print(f"Pressure          : {PRESSURE_BAR:.6g} bar")
print(f"CO2 fugacity      : {CO2_FUGACITY_BAR:.6g} bar")
print(f"pH                : {float(props.pH()):.12g}")
print(
    f"Total aqueous C   : "
    f"{float(props.elementMolality('C')):.12g} mol/kgw"
)


# ---------------------------------------------------------------------
# Element amounts AFTER equilibration
#
# These become the conserved element inventory if this equilibrated
# brine is subsequently used as the initial state of a CLOSED system.
# ---------------------------------------------------------------------

element_amounts = state.elementAmounts()

element_rows = []

print("\n=== ELEMENT AMOUNTS AFTER CO2 EQUILIBRATION ===")
print(f"{'Element':<12} {'Amount (mol)':>20}")

for i, element in enumerate(system.elements()):
    symbol = str(element.symbol())
    amount = float(element_amounts[i])

    if abs(amount) > 1.0e-14:
        element_rows.append((symbol, amount))
        print(f"{symbol:<12} {amount:>20.12e}")


# ---------------------------------------------------------------------
# Equilibrated aqueous species amounts
#
# These are also printed because the batch-runner YAML currently
# initialises brine using species_amounts, not element amounts.
# ---------------------------------------------------------------------

species_rows = []

print("\n=== NON-ZERO AQUEOUS SPECIES AMOUNTS ===")
print(f"{'Species':<30} {'Amount (mol)':>20}")

for species in system.species():
    name = str(species.name())
    amount = float(state.speciesAmount(name))

    if abs(amount) > 1.0e-14:
        species_rows.append((name, amount))
        print(f"{name:<30} {amount:>20.12e}")


# ---------------------------------------------------------------------
# Save machine-readable results
# ---------------------------------------------------------------------

with open(
    "Quick_lab\\jayasekara_equilibrated_brine_elements.csv",
    "w",
    newline="",
    encoding="utf-8",
) as f:
    writer = csv.writer(f)
    writer.writerow(["element", "amount_mol"])
    writer.writerows(element_rows)


with open(
    "Quick_lab\\jayasekara_equilibrated_brine_species.csv",
    "w",
    newline="",
    encoding="utf-8",
) as f:
    writer = csv.writer(f)
    writer.writerow(["species", "amount_mol"])
    writer.writerows(species_rows)


print("\nWritten:")
print("  jayasekara_equilibrated_brine_elements.csv")
print("  jayasekara_equilibrated_brine_species.csv")

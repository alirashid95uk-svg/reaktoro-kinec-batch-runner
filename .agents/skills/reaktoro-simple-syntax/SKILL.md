---
name: reaktoro-simple-syntax
description: Use when adding or reviewing Reaktoro construction and solver code in this repository; enforce direct official-style Reaktoro syntax and reject unnecessary abstractions.
---

# Reaktoro Simple Syntax

Use simple visible Reaktoro calls that follow the documented Reaktoro
construction order.

## Required Patterns

- Load PHREEQC-style thermodynamics with `PhreeqcDatabase`.
- Construct the aqueous phase with `AqueousPhase`.
- Use `GaseousPhase` only when gas is explicitly enabled.
- Construct pure mineral phases with `MineralPhases`.
- Assemble the system with `ChemicalSystem`.
- Construct and populate the initial state with `ChemicalState`.
- Use `ActivityModelPhreeqc` for PHREEQC aqueous systems unless an explicit,
  documented project decision changes it.
- When gas is enabled, choose the gas activity model explicitly. A
  PHREEQC-compatible option is `ActivityModelPengRobinsonPhreeqc`.
- Use direct state-setting patterns such as temperature, pressure, and
  explicit species amounts with units.
- Keep phase construction, state construction, solver calls, and property
  extraction visible and readable.

## Guardrails

- Do not hide basic Reaktoro setup behind unnecessary classes.
- Do not hide scientific settings or units in helper defaults.
- Use pseudocode labels where syntax has not been locally tested.
- Exact Reaktoro Python syntax must be verified during implementation.

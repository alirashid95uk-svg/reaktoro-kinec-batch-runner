---
name: reaktoro-simple-syntax
description: Use when adding or reviewing Reaktoro construction code and the main concern is keeping setup direct, visible, and free of unnecessary abstraction. Use reaktoro-runtime-validation separately only when exact 2.13 API semantics are uncertain.
---

# Reaktoro Simple Syntax

Keep the scientific construction order visible:

```text
PhreeqcDatabase
-> AqueousPhase / optional GaseousPhase / MineralPhases
-> ChemicalSystem
-> ChemicalState
-> solver
-> ChemicalProps / AqueousProps
```

Use explicit database, phase, state, amount, unit, solver, and property calls.
Use `ActivityModelPhreeqc` for PHREEQC aqueous systems unless an explicit
project decision changes it. Configure a gas activity model explicitly when a
gas phase exists.

Do not hide scientific settings or units behind helper defaults, factories,
registries, dynamic imports, or generic simulator abstractions.

Prefer short focused helpers only when they remove ordinary code duplication
without obscuring Reaktoro objects or scientific choices.

If exact Python syntax, overload behaviour, sign, units, state mutation, or
solver semantics are uncertain, use `reaktoro-runtime-validation`. Do not run a
runtime probe merely because this style skill was triggered.

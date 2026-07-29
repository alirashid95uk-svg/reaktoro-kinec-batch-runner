---
name: reaktoro-runtime-validation
description: Probe exact Reaktoro 2.13 Python behavior in the verified Conda environment. Use when changing or questioning reaction-rate callbacks, mineral sign conventions, surface areas, solver calls, constraints, state mutation, or other runtime API behavior.
---

# Reaktoro Runtime Validation

Use `fypr-reaktoro`; base Python is not an acceptable substitute:

```powershell
conda run -n fypr-reaktoro python -c "import reaktoro as rkt; print(rkt.__version__)"
```

## Probe Workflow

1. State the exact API claim being tested.
2. Use the smallest real `ChemicalSystem`, `ChemicalState`, and solver call
   that can falsify it.
3. Print or assert the relevant equation coefficient, state change, units,
   result status, and iteration count.
4. Convert the probe into one focused test if it protects project behavior.
5. Run the targeted test and report the observed Reaktoro version.

For the Kinec adapter's current general `ReactionRateModel` overload, run:

```powershell
conda run -n fypr-reaktoro python -m pytest -q tests/test_first_version.py -k general_reaction_rate_contract
```

That test proves a `mol/s` callback with positive sign dissolves Calcite for
the exact binding used here. The mineral-specific callback has different API
semantics; do not mix the two.

`ChemicalProps.surfaceArea(mineral)` is live total area in `m2`. A
`MineralSurface` value in normalized units creates an amount-dependent area
model, so report configured area and live area as different quantities.

Use unit/config tests as the default smoke check. Run the multi-minute
three-mineral development case only when its scientific runtime behavior is
actually under review.

---
name: reaktoro-runtime-validation
description: Probe exact Reaktoro 2.13 Python behaviour when a task depends on solver calls, reaction-rate callbacks, sign/units, surface areas, constraints, state mutation, or other binding semantics. Do not use for ordinary refactors or documentation work.
---

# Reaktoro Runtime Validation

Use `fypr-reaktoro`; base Python is not an acceptable substitute:

```powershell
conda run -n fypr-reaktoro python -c "import reaktoro as rkt; print(rkt.__version__)"
```

## Probe Rule

Use a runtime probe only when an exact Reaktoro API/semantic claim is material.
Prefer the smallest probe that can falsify the claim.

1. State the exact claim being tested.
2. Use the smallest real `ChemicalSystem`, `ChemicalState`, and solver call
   needed.
3. Print or assert the relevant coefficient, state change, units, result status,
   or iteration evidence.
4. Convert the probe into a focused test only when it protects persistent
   project behaviour.

Do not run a large scientific case merely to confirm an API contract.

## Existing Contracts

For the custom Kinec adapter's general `ReactionRateModel` overload:

```powershell
conda run -n fypr-reaktoro python -m pytest -q tests/test_first_version.py -k general_reaction_rate_contract
```

That focused test establishes the currently used callback's unit/sign contract.
The mineral-specific callback has different semantics; do not transfer its sign
convention.

`ChemicalProps.surfaceArea(mineral)` is live total area in `m2`. A
`MineralSurface` specified in normalized units creates amount-dependent area
behaviour, so configured area and live total area are distinct quantities.

Use unit/config tests as the default smoke check. Run the multi-minute
three-mineral development case only when its actual scientific runtime behaviour
is under investigation.

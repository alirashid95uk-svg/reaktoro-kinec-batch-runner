---
name: timestep-units-audit
description: Use for a focused dimensional/units audit of adaptive timestep formulas, configuration, diagnostics, and Reaktoro interfaces. Do not annotate the whole repository; verify only the affected numerical path.
metadata:
  inspiration: trailofbits/skills dimensional-analysis
---

# Timestep Units Audit

## Goal

Catch dimensional mismatches before they become silent timestep/controller bugs.

## Audit table

Verify from active code/API rather than assumption:

| Quantity | Expected dimension |
|---|---|
| `h`, `dt_min`, `dt_max`, output/checkpoint times | time; canonical internal unit should be seconds |
| reaction rate | verify exact Reaktoro callback/interface; project custom Kinec general callback is documented as mol/s |
| reaction extent / mineral amount | amount of substance if used as controlled quantity; verify exact exposed API |
| absolute tolerance `atol` | same unit as controlled quantity |
| relative tolerance `rtol` | dimensionless |
| reference floor | same unit as controlled quantity |
| normalized error `E_j`, `E` | dimensionless |
| safety/growth/shrink factors | dimensionless |
| saturation index | dimensionless |
| pH | dimensionless/logarithmic diagnostic |

## Checks

- convert user time units once through the authoritative conversion path;
- do controller arithmetic in canonical seconds;
- never mix days/years/seconds in event prediction without explicit conversion;
- ensure `atol + rtol * scale` is dimensionally valid;
- ensure persisted history labels units unambiguously;
- reject NaN/Inf before unit conversion or controller arithmetic;
- check that year length uses the project's explicit `year_definition_days` semantics.

## Red flags

- comparing moles directly with molality/concentration tolerances;
- using an SI/pH tolerance as if it were a mole tolerance;
- applying a time-unit conversion twice;
- treating configured normalized surface area as live total surface area;
- silently using a default year length where the schema requires explicit definition.

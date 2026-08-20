---
name: nonlinear-solver-diagnostics
description: Use when a Reaktoro kinetic solve fails or stagnates and the question is whether the issue is nonlinear convergence, bad state/configuration, or timestep size. Do not redesign Reaktoro internals or tune scientific inputs automatically.
metadata:
  inspiration: HeshamFS/materials-simulation-skills nonlinear-solvers
---

# Nonlinear Solver Diagnostics

## Separation of errors

Keep these distinct:

- nonlinear/algebraic convergence error inside the Reaktoro solve;
- outer temporal discretisation error estimated by Richardson;
- scientific/model error.

A successful nonlinear solve does not prove small temporal error. A Richardson rejection does not mean Reaktoro failed.

## Diagnostic workflow

1. Capture exact Reaktoro result status/exception and iteration evidence available to the project.
2. Confirm the starting ChemicalState is valid and is the last accepted state.
3. Reproduce with the same state and timestep.
4. Retry only the timestep according to the controller's failure policy.
5. If smaller `h` resolves the failure reproducibly, classify it as timestep-sensitive nonlinear difficulty.
6. If failure persists at `dt_min`, inspect configuration, constraints, rate callback, units, and state construction.
7. Use `reaktoro-runtime-validation` for uncertain 2.13 semantics.

## Do not

- loosen Reaktoro tolerances merely to make the outer controller succeed;
- change thermodynamic/kinetic data during numerical diagnosis;
- infer Jacobian structure not exposed by Reaktoro;
- claim a root cause without a minimal reproducing probe.

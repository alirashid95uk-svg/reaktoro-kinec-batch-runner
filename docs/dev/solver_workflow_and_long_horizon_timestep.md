# Solver Workflow and Timestep Contract

## Runtime Status

The active runner implements:

- standard Reaktoro equilibrium and kinetics solvers;
- explicit equilibrium/kinetics workflow staging;
- fixed, legacy adaptive, and explicit Richardson error-controlled timestep modes;
- scheduled output times;
- accepted-state checkpoints;
- adaptive rollback and retry;
- solver-attempt history.

Only implemented runtime behaviour belongs in this contract. Smart-solver
backends, automatic restart, generic solver-safety blocks, global solver
conservation blocks, and surface-area-evolution controls are not active schema
features and must not be represented as disabled placeholders.

## 1. Purpose

This file defines the active solver and timestep behaviour of the Reaktoro
batch runner.

It covers:

```text
solver workflows
CO2 constraint staging
redox constraint staging
fixed timestep
adaptive timestep
state rollback
output-time landing
checkpoint writing
solver history
reaction-rate extraction policy
```

Output-file contracts are defined in `output_package_design.md`. YAML schema
rules are defined in `config_schema_feature_options.md`.

## 2. Core Solver Rules

- Use direct Reaktoro calls and simple Python control flow.
- Do not modify Reaktoro internals.
- Do not silently change scientific inputs or solver controls.
- `runner.py` remains orchestration only.
- Do not add backend factories, plugin managers, dynamic solver selection, or
  silent fallback.

### 2.1 Launcher Preflight

Preflight follows the same construction chain as execution:

```text
configuration resolution
-> database loading
-> kinetic-parameter loading
-> exact mineral mapping
-> chemical-system construction
-> initial-state construction
```

Preflight stops before equilibrium or kinetic solver execution and writes no
scientific outputs. A blocked case must report the exact failing stage.

## 3. Scientific Scope Boundary

Long-duration output remains a batch geochemical trajectory under the configured
boundary conditions. It is not a reactive-transport, leakage, fracture-flow,
geomechanics, permeability, or pressure-evolution model.

## 4. Solver Workflow Modes

The active workflow modes are:

```text
equilibrium_only
closed_kinetics
fixed_fugacity_initial_equilibrium_then_closed_kinetics
fixed_fugacity_during_kinetic_steps
```

### 4.1 `equilibrium_only`

Run a single equilibrium calculation and stop.

Requirements:

```text
kinetics.enabled: false
```

### 4.2 `closed_kinetics`

Run kinetic timesteps without fixed-fugacity or pE constraints during each
kinetic solve.

Expected Reaktoro pattern:

```python
solver = rkt.KineticsSolver(system)
solver.solve(state, dt_s)
```

Requirements:

```text
kinetics.enabled: true
```

### 4.3 `fixed_fugacity_initial_equilibrium_then_closed_kinetics`

Condition the initial state using fixed CO2 fugacity, save that conditioned
state as time zero, then run closed kinetic timesteps.

Expected pattern:

```python
equilibrium_solver = rkt.EquilibriumSolver(specs)
equilibrium_solver.solve(state, conditions)

kinetic_solver = rkt.KineticsSolver(system)
kinetic_solver.solve(state, dt_s)
```

Requirements:

```text
kinetics.enabled: true
co2.mode: fixed_fugacity
```

This is the recommended fixed-fugacity kinetic workflow.

### 4.4 `fixed_fugacity_during_kinetic_steps`

Preserve constrained kinetic behaviour when explicitly requested.

Expected pattern:

```python
solver = rkt.KineticsSolver(specs)
solver.solve(state, dt_s, conditions)
```

Requirements:

```text
kinetics.enabled: true
co2.mode: fixed_fugacity
```

## 5. Redox Constraint Staging

When redox is enabled, `redox.apply_during` controls whether pE is applied only
at initial equilibrium or during kinetic steps.

```text
initial_equilibrium_only
kinetic_steps
```

Rules:

```text
redox.enabled: false
-> do not apply pE constraints

redox.apply_during: initial_equilibrium_only
-> apply pE during initial conditioning only

redox.apply_during: kinetic_steps
-> pass pE conditions only to workflow paths that support constrained kinetics
```

## 6. Solver Backend

The active backend is **standard Reaktoro only**:

```text
EquilibriumSolver
KineticsSolver
```

There is no `solver.backend` YAML block and no smart-solver fallback policy in
the active runtime contract.

## 7. Time Ownership

`kinetics` defines whether kinetic reactions exist and where kinetic parameters
come from. `solver.timestep` owns simulation duration and timestep control.

Canonical runtime time is seconds after deterministic preprocessing.

## 8. Timestep Modes

The active modes are:

```text
fixed
adaptive
adaptive_error_controlled
```

### 8.1 Fixed Timestep

Fixed stepping is retained for debugging, reproducibility, regression testing,
and timestep-convergence studies.

Runtime rules:

- use constant configured `dt` on the absolute fixed-step grid;
- shorten the final interval to land exactly on final time;
- split intervals at output/checkpoint targets without resetting the fixed grid;
- emit chemistry rows only at requested output targets;
- never interpolate states;
- write checkpoints only at accepted checkpoint targets;
- generate steps lazily;
- reject impossible `max_internal_steps` cases before solver construction;
- snapshot the accepted state before a trial;
- on solver failure restore that state, keep accepted time unchanged, record the
  failure, and stop;
- fixed-step failures are not retried with a smaller `dt`.

Time ownership is:

```text
step_size.dt        = absolute fixed-grid spacing
output_schedule     = accepted states written to trajectory outputs
checkpoint_schedule = accepted states written as checkpoints
solver_history      = every accepted or failed solver attempt
```

### 8.2 Adaptive Timestep

Adaptive stepping uses only Reaktoro solver success or failure.

Core algorithm:

```text
1. Snapshot the accepted state.
2. Attempt the proposed dt.
3. If `KineticsSolver.solve(state, dt)` succeeds:
   - advance accepted time;
   - emit output/checkpoint records when targeted;
   - grow controller dt subject to dt_max.
4. If Reaktoro raises or reports an unsuccessful result:
   - restore the accepted state;
   - keep accepted time unchanged;
   - shrink dt;
   - retry.
5. Fail cleanly when retries are exhausted or retry control cannot continue.
```

Every attempt is written to `solver_history.csv`. Failed attempts retain
`time_end_s == time_start_s`.

The controller caps an attempted target at the next output time, checkpoint
time, or final time. Exact target landing takes precedence over `dt_min`; `dt_min`
controls retry shrinkage rather than forced event landing.

Before solver construction, adaptive preflight partitions the duration at all
forced targets and uses `dt_max` to calculate a lower bound on required accepted
steps. Cases that cannot fit within `max_internal_steps` are rejected.

Long simulations use ordinary adaptive mode with the same duration, output,
checkpoint, and timestep controls.

### 8.3 Richardson Error-Controlled Adaptive Timestep

`mode: adaptive_error_controlled` is a separate controller. It does not replace
or reinterpret `mode: adaptive`.

The current implementation supports direct kinetic workflows. Configurations
that require an initial-equilibrium stage are rejected so the saved time-zero
row and the runtime state cannot disagree.

Each outer trial follows:

```text
copy the last accepted state independently for full and half branches
-> solve one full h branch with a fresh KineticsSolver
-> solve h/2 then h/2 with a second fresh KineticsSolver
-> accept the genuine two-half-step state only when E <= 1
```

The controller makes no separate startup call. Each branch uses the ordinary
`KineticsSolver.solve(...)` path so Reaktoro retains ownership of its native
solver startup behaviour.

The controlled quantities are configured kinetic-mineral amounts in mol. The
installed Reaktoro 2.13 Python API does not expose integrated reaction extent as
a clean public state quantity.

For mineral `j`:

```text
e_j = abs(n_H,j - n_F,j) / (2**p - 1)
T_j = atol_j + rtol * max(abs(n_H,j), floor_j)
E_j = e_j / T_j
E = max(E_j)
```

All three branch solves must succeed and `E <= 1` before accepted time advances.
The I-controller is:

```text
h_next = safety_factor * h * E**(-1/(p + 1))
```

with explicit zero-error handling and configured shrink, growth, `dt_min`, and
`dt_max` bounds. Non-finite error rejects. A temporal-error rejection cannot
increase its effective trial step.

Solver failure and temporal-error rejection are disjoint:

```text
branch solver failure -> restore accepted state -> solver_failure_shrink_factor
valid branches with E > 1 -> restore accepted state -> I-controller shrink
```

A temporally acceptable fine branch is still rejected independently when its
observed pH, mineral amounts, saturation indices, or reaction rates are
non-finite, or when a mineral amount is more negative than the explicit molar
`negative_amount_tolerance`. This state-admissibility rejection restores the
accepted state and uses the configured controller shrink.

`max_internal_steps` counts outer Richardson trials. Actual Reaktoro calls are
tracked separately; a complete Richardson trial uses three.

Hard kinetic-mineral exhaustion may reject a valid trial and retry at a
linearly localised event interval. A crossing detected exactly at the trial
endpoint is treated as landed. Exhausting the configured localisation limit
fails without accepting the crossing state. An accepted exhaustion resets the
proposal to the explicit configured restart timestep. Soft SI, pH,
reaction-rate, and first secondary-mineral appearance indications only cap a
subsequent proposal; they are not LTE acceptance variables.

The same exact output/checkpoint/final target helpers used by legacy adaptive
execution cap the new trial. Chemistry is never interpolated. Half-step solves
are branch work, not accepted physical timesteps.

## 9. Checkpoint Semantics

Checkpointing means writing an accepted intermediate state plus enough metadata
for diagnostics and evidence.

Checkpointing does **not** mean resumable execution. Automatic restart is not
part of the current configuration schema, and `solver.restart` is not a valid
placeholder field.

## 10. Failed-Step State Safety

An adaptive solver failure must restore the last accepted `ChemicalState`. The active
implementation uses a copied state before each trial and native assignment on
failure. Accepted time advances only after a successful Reaktoro solve.

Numerical rollback is a solver-safety mechanism; it is not a scientific fix.

## 11. Reaction-Rate Extraction

When reaction-rate postprocessing is enabled, use accepted-state Reaktoro
runtime properties:

```text
ChemicalProps.reactionRate(mineral.name) -> total reaction rate in mol/s
ChemicalProps.surfaceArea(mineral.name)   -> live total area in m2
```

Only divide by live surface area when it is nonzero. Do not independently
recompute kinetic equations for routine diagnostics.

For the time-zero row only, construct the live `KineticsSolver` before rate
evaluation. Extract the non-rate observations from the live state, then rebuild
the same configured system in a disposable process, verify ordered species
identity, copy temperature, pressure, and the complete species-amount vector,
and merge only the resulting rate fields into the live row. The process boundary
isolates a reproducible state/order-dependent interaction in Reaktoro 2.13 whose
exact internal mechanism is not established. Later accepted-state rates remain
on the live state.

Rate extraction does not control timestep acceptance.

## 12. Conservation and Balance Status

There is no generic `solver.conservation` YAML block in the active schema.

Existing postprocessing element/carbon budgets are reconstructed diagnostics;
they are not solver acceptance criteria.

The current solver/output contract does not provide an authoritative whole-state
material, component, or charge-balance diagnostic.

## 13. Surface Area

Configured kinetic surface areas remain explicit scientific inputs. There is no
`solver.geochemical_controls.surface_area_update` block and no automatic
surface-area evolution law in the active runtime.

Do not silently evolve surface area; runtime uses the configured surface-area
semantics only.

## 14. Solver Success Criteria

The active solver contract is satisfied when:

1. workflow staging is explicit and validated;
2. the standard Reaktoro backend is used directly;
3. CO2 and redox constraints are applied only at their configured stages;
4. fixed and adaptive modes follow their schemas;
5. timestep ownership remains under `solver.timestep`;
6. scheduled outputs/checkpoints are landed on without interpolation;
7. failed adaptive attempts do not corrupt accepted state;
8. fixed-step failure restores the accepted state before termination;
9. every solver attempt is traceable in solver history;
10. checkpointing remains distinct from unsupported restart;
11. unsupported blocks are not exposed as disabled schema options;
12. no Reaktoro internals or scientific inputs are silently modified.

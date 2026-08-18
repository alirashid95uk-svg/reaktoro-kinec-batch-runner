# Solver Workflow and Timestep Contract

## Runtime Status

The active runner implements:

- standard Reaktoro equilibrium and kinetics solvers;
- explicit equilibrium/kinetics workflow staging;
- fixed, adaptive, and `adaptive_long_horizon` timestep modes;
- scheduled output times;
- accepted-state checkpoints;
- adaptive rollback and retry;
- solver-attempt history;
- configured adaptive state acceptance checks.

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
adaptive_long_horizon timestep
state rollback
output-time landing
checkpoint writing
adaptive acceptance
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
adaptive_long_horizon
```

`adaptive_long_horizon` is an implemented mode using the same adaptive
controller with additional schema requirements.

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

Adaptive stepping uses solver success plus configured scientific/numerical state
checks.

Core algorithm:

```text
1. Snapshot the accepted state.
2. Attempt the proposed dt.
3. Evaluate solver success and configured acceptance checks.
4. If accepted:
   - advance accepted time;
   - emit output/checkpoint records when targeted;
   - grow controller dt subject to dt_max.
5. If rejected:
   - restore the accepted state;
   - keep accepted time unchanged;
   - shrink dt;
   - retry.
6. Fail cleanly when retries are exhausted or retry control cannot continue.
```

Every attempt is written to `solver_history.csv`. Rejected attempts retain
`time_end_s == time_start_s`.

The controller caps an attempted target at the next output time, checkpoint
time, or final time. Exact target landing takes precedence over `dt_min`; `dt_min`
controls retry shrinkage rather than forced event landing.

Before solver construction, adaptive preflight partitions the duration at all
forced targets and uses `dt_max` to calculate a lower bound on required accepted
steps. Cases that cannot fit within `max_internal_steps` are rejected.

### 8.3 `adaptive_long_horizon`

This mode uses the same adaptive controller with additional long-horizon policy
requirements:

- `every_internal_step` output is forbidden;
- `include_final` must be true;
- checkpointing must be enabled;
- human-readable year units require explicit `year_definition_days`;
- output/checkpoint targets are landed on exactly without interpolation.

Long horizons still require scientifically justified `dt_initial`, `dt_min`,
`dt_max`, growth/shrink factors, and acceptance thresholds. The mode does not
make a long-duration simulation scientifically valid by itself.

## 9. Adaptive Acceptance Checks

Current configurable acceptance checks include:

```text
non-finite state values
negative species amount tolerance
maximum delta pH
maximum delta saturation index
selected-species amount change tolerance
mineral amount change tolerance
element conservation tolerance
```

`max_relative_rate_change` must remain null because rate-based adaptive
acceptance has not been verified in the active controller.

Thresholds are numerical/scientific controls and must not be invented or tuned
silently.

Element conservation inside adaptive acceptance is valid only where the chosen
workflow is closed with respect to the checked elements. Fixed-fugacity kinetic
steps can exchange material with an external reservoir and therefore cannot use
blind closed-system element-conservation rejection.

## 10. Checkpoint Semantics

Checkpointing means writing an accepted intermediate state plus enough metadata
for diagnostics and evidence.

Checkpointing does **not** mean resumable execution. Automatic restart is not
part of the current configuration schema, and `solver.restart` is not a valid
placeholder field.

## 11. Rejected-Step State Safety

Adaptive rejection must restore the last accepted `ChemicalState`. The active
implementation uses a copied state before each trial and native assignment on
rejection. Accepted time advances only after solver success and all configured
acceptance checks pass.

Numerical rollback is a solver-safety mechanism; it is not a scientific fix.

## 12. Reaction-Rate Extraction

When reaction-rate postprocessing is enabled, use accepted-state Reaktoro
runtime properties:

```text
ChemicalProps.reactionRate(mineral.name) -> total reaction rate in mol/s
ChemicalProps.surfaceArea(mineral.name)   -> live total area in m2
```

Only divide by live surface area when it is nonzero. Do not independently
recompute kinetic equations for routine diagnostics.

Rate extraction does not by itself enable rate-based timestep acceptance.

## 13. Conservation and Balance Status

There is no generic `solver.conservation` YAML block in the active schema.

The adaptive controller currently supports its specific
`acceptance.element_conservation` check. Existing postprocessing element/carbon
budgets are separate reconstructed diagnostics.

The current solver/output contract does not provide an authoritative whole-state
material, component, or charge-balance diagnostic.

## 14. Surface Area

Configured kinetic surface areas remain explicit scientific inputs. There is no
`solver.geochemical_controls.surface_area_update` block and no automatic
surface-area evolution law in the active runtime.

Do not silently evolve surface area; runtime uses the configured surface-area
semantics only.

## 15. Solver Success Criteria

The active solver contract is satisfied when:

1. workflow staging is explicit and validated;
2. the standard Reaktoro backend is used directly;
3. CO2 and redox constraints are applied only at their configured stages;
4. fixed, adaptive, and `adaptive_long_horizon` modes follow their schemas;
5. timestep ownership remains under `solver.timestep`;
6. scheduled outputs/checkpoints are landed on without interpolation;
7. rejected adaptive steps do not corrupt accepted state;
8. fixed-step failure restores the accepted state before termination;
9. every solver attempt is traceable in solver history;
10. checkpointing remains distinct from unsupported restart;
11. unsupported blocks are not exposed as disabled schema options;
12. no Reaktoro internals or scientific inputs are silently modified.

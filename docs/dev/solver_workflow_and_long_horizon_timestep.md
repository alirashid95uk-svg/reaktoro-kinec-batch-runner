# Solver Workflow and Long-Horizon Timestep Design — Final Codex Contract

## Runtime V1 Status

The active runner implements fixed, adaptive, and `adaptive_long_horizon`
timesteps with standard Reaktoro solvers, independent scheduled timeseries
output, accepted-state checkpoints, rollback, attempt logging, and configured
state-change acceptance checks. Smart solvers and automatic restart remain
disabled.

## 1. Purpose

This file defines how the Reaktoro batch runner should execute simulations.

It covers:

```text
solver workflows
standard and smart solver backends
CO₂ constraint staging
redox constraint staging
fixed timestep
adaptive timestep
adaptive_long_horizon timestep
state rollback
checkpointing
restart distinction
solver safety
conservation diagnostics
reaction-rate extraction policy
```

This file does **not** define full output table schemas. Output files are defined in:

```text
output_package_design.md
```

YAML schema and validation rules are defined in:

```text
config_schema_feature_options.md
```

---

## 2. Core Solver Design Rule

```text
All solver features are controlled from YAML.
Defaults remain safe.
No hidden solver behaviour.
No Reaktoro internals are modified.
```

Use direct Reaktoro calls and simple Python control flow.

Do not introduce:

```text
plugin managers
abstract simulator engines
generic backend factories
dynamic imports
automatic scientific choices
silent fallbacks
```

---

## 3. Scientific Scope Warning

A 10,000-year batch simulation is a long-duration closed-system or specified-boundary kinetic geochemical path.

It is not a full geological leakage prediction.

Unless explicitly added later, the model does not represent:

```text
transport
fresh-brine renewal
diffusion through caprock
fracture flow
pressure evolution
porosity/permeability feedback
geomechanics
leakage flux
```

Diagnostics and reports must describe long-duration results as batch kinetic trajectories under configured assumptions.

---

## 4. Current Solver Problem

The known performance risk is constrained kinetic solving:

```text
fixed CO₂ fugacity / pE conditions
→ passed into KineticsSolver.solve(...) during every timestep
→ constrained kinetic solve
→ possible stall or very slow convergence
```

The code must support constrained kinetics as an explicit option, but the recommended fixed-fugacity workflow should be:

```text
initial fixed-fugacity equilibrium conditioning
→ closed kinetic timesteps
```

The same principle applies to redox/pE if redox is enabled.

---

## 5. Solver Workflow Modes

Required workflow modes:

```text
equilibrium_only
closed_kinetics
fixed_fugacity_initial_equilibrium_then_closed_kinetics
fixed_fugacity_during_kinetic_steps
```

### 5.1 `equilibrium_only`

Purpose:

```text
Run a single equilibrium calculation and stop.
```

Validation:

```text
requires kinetics.enabled: false
```

### 5.2 `closed_kinetics`

Purpose:

```text
Run kinetic timesteps without external fixed-fugacity or pE constraints during the kinetic solve.
```

Expected pattern:

```python
solver = rkt.KineticsSolver(system)
solver.solve(state, dt_s)
```

Validation:

```text
requires kinetics.enabled: true
must not pass fixed-fugacity or pE conditions into kinetic timesteps
```

### 5.3 `fixed_fugacity_initial_equilibrium_then_closed_kinetics`

Purpose:

```text
Use fixed CO₂ fugacity only to condition the initial aqueous state, then run closed kinetic timesteps afterward.
```

Workflow:

```text
1. Build initial chemical state.
2. Build fixed-fugacity equilibrium specifications and conditions.
3. Run EquilibriumSolver to condition the initial state.
4. Save conditioned state as time zero.
5. Run KineticsSolver(system).solve(state, dt) for kinetic timesteps.
6. Do not pass fixed-fugacity conditions into KineticsSolver.solve().
```

Expected pattern:

```python
equilibrium_solver = rkt.EquilibriumSolver(specs)
equilibrium_solver.solve(state, conditions)

kinetic_solver = rkt.KineticsSolver(system)
kinetic_solver.solve(state, dt_s)
```

Validation:

```text
requires kinetics.enabled: true
requires co2.mode: fixed_fugacity
```

This should be the recommended default for fixed-fugacity CO₂ kinetic cases.

### 5.4 `fixed_fugacity_during_kinetic_steps`

Purpose:

```text
Preserve constrained kinetic behaviour for comparison or special cases.
```

Expected pattern:

```python
solver = rkt.KineticsSolver(specs)
solver.solve(state, dt_s, conditions)
```

Validation:

```text
requires kinetics.enabled: true
requires co2.mode: fixed_fugacity
```

This mode should be available, but not default.

---

## 6. Redox Constraint Staging

Redox/pE can create constrained-equilibrium behaviour similar to fixed CO₂ fugacity.

Add explicit redox application control.

Recommended config:

```yaml
redox:
  enabled: true
  pe: 4.0
  apply_during: initial_equilibrium_only
```

Allowed `apply_during` values:

```text
initial_equilibrium_only
kinetic_steps
```

Runtime rules:

```text
redox.enabled: false
→ do not apply pE constraints

redox.apply_during: initial_equilibrium_only
→ apply pE during initial equilibrium conditioning only
→ run kinetic timesteps without pE condition

redox.apply_during: kinetic_steps
→ preserve constrained kinetic pE behaviour
→ pass pE conditions into kinetic solve only in workflows that support constrained kinetics
```

Validation rules are defined in `config_schema_feature_options.md`.

Record runtime behaviour in:

```text
diagnostics.json
manifest.json
```

Recommended diagnostic fields:

```text
redox_enabled_runtime
redox_apply_during_runtime
```

---

## 7. Solver Backend Options

Required backend types:

```text
standard
smart
```

### 7.1 `standard`

Use:

```text
EquilibriumSolver
KineticsSolver
```

Default backend.

### 7.2 `smart`

Use Reaktoro smart solvers if available in the installed Python binding:

```text
SmartEquilibriumSolver
SmartKineticsSolver
```

Rules:

```text
experimental only
not default
must require allow_experimental_smart_solver: true
must log backend use
must log fallback events
must not silently change scientific results
```

Runtime behaviour:

```text
If smart solvers are available:
    use smart backend and record backend type.

If unavailable and fallback_to_standard is true:
    use standard backend and record warning.

If unavailable and fallback_to_standard is false:
    fail clearly.
```

Local checks:

```python
hasattr(rkt, "SmartEquilibriumSolver")
hasattr(rkt, "SmartKineticsSolver")
```

---

## 8. Time Ownership Rule

Do not let `kinetics` own duration and timestep control.

Correct separation:

```text
kinetics = whether kinetic reactions exist and where kinetic parameters come from
solver.timestep = how time integration is performed
```

Recommended:

```yaml
kinetics:
  enabled: true
  model: palandri_kharaka  # optional; default
  path: data/kinetics/PalandriKharaka_local.yaml  # optional override

solver:
  timestep:
    mode: adaptive_long_horizon
    time:
      duration_value: 10000
      duration_unit: years
      year_definition_days: 360  # explicit project choice; no implicit default
```

Do not generate fixed `step_sizes_s()` from kinetics for adaptive or long-horizon modes.

---

## 9. Timestep Modes

Required modes:

```text
fixed
adaptive
adaptive_long_horizon
```

Each mode must have a separate clean config schema.

### 9.1 Fixed Timestep

Purpose:

```text
Simple debugging, reproducibility, and regression testing.
```

Example:

```yaml
solver:
  timestep:
    mode: fixed
    time:
      duration_value: 10
      duration_unit: seconds
    step_size:
      dt: { value: 1, unit: second }
```

Runtime:

```text
Use constant dt until duration is reached.
Shorten final step if needed to land exactly on final time.
Split a fixed interval at output or checkpoint targets without resetting the absolute fixed-step grid.
Emit chemistry rows only at requested output targets; never interpolate states.
Write checkpoints only at independent checkpoint targets after solver acceptance.
Generate fixed steps lazily from integer indices; do not allocate the full schedule.
Reject non-finite time values and runs above max_internal_steps before solver construction.
Stream accepted result rows and solver records instead of retaining the full trajectory.
Snapshot the accepted ChemicalState before each trial solve.
If a trial fails, restore that snapshot, keep accepted time unchanged, write failure diagnostics, and stop.
```

This is failure-safe fixed stepping, not adaptive stepping: failed trials are
not retried with a smaller `dt`.

Fixed-step time ownership is separated as follows:

```text
step_size.dt          = absolute fixed-grid spacing
output_schedule       = accepted states written to timeseries and downstream trajectory outputs
checkpoint_schedule   = accepted states written under checkpoints/
solver_history        = every accepted or failed solver attempt
```

All three schedules use canonical seconds after preprocessing. Output and
checkpoint targets are sorted and de-duplicated independently, then their union
splits fixed-grid intervals. The configured duration remains the final solver
target even when `include_final: false` suppresses the final timeseries row.

### 9.2 Adaptive Timestep

Purpose:

```text
Short-to-medium duration adaptive stepping using solver success and scientific acceptance checks.
```

Example:

```yaml
solver:
  timestep:
    mode: adaptive
    time:
      duration_value: 1
      duration_unit: day
    step_size:
      dt_initial: { value: 1, unit: second }
      dt_min: { value: 1.0e-6, unit: second }
      dt_max: { value: 1, unit: hour }
      growth_factor: 1.25
      shrink_factor: 0.5
      max_retries_per_step: 8
    acceptance:
      enabled: true
      fail_on_non_finite: true
      negative_amount_tolerance_mol: TBD_SOURCE_REQUIRED
      max_delta_pH: 0.10
      max_delta_saturation_index: 0.25
      selected_species_change:
        absolute_tolerance_mol: TBD_SOURCE_REQUIRED
        relative_tolerance: TBD_SOURCE_REQUIRED
        reference_floor_mol: TBD_SOURCE_REQUIRED
      mineral_change:
        absolute_tolerance_mol: TBD_SOURCE_REQUIRED
        relative_tolerance: TBD_SOURCE_REQUIRED
        reference_floor_mol: TBD_SOURCE_REQUIRED
      element_conservation:
        enabled: false
        relative_tolerance: null
        absolute_tolerance_mol: null
      max_relative_rate_change: null
    max_internal_steps: 100000
    output_schedule:
      mode: explicit
      include_initial: true
      include_final: true
      explicit_times: []
    checkpoint_schedule: { enabled: false, times: [] }
```

Core algorithm:

```text
1. Save accepted state.
2. Attempt dt.
3. If solve succeeds and acceptance checks pass:
       accept step;
       advance time;
       grow dt if allowed.
4. If solve fails or acceptance checks fail:
       restore previous state;
       shrink dt;
       retry.
5. If retries exceed limit:
       fail cleanly.
```

Mandatory:

```text
Rejected steps must not corrupt accepted state.
```

The implementation uses `ChemicalState(state)` before every trial and
`state.assign(snapshot)` after rejection. The accepted timestamp advances only
after both solver success and configured acceptance checks pass. Every trial is
written to `solver_history.csv`; rejected rows retain
`time_end_s == time_start_s`. Target calculation uses canonical seconds and is
capped at the next output, checkpoint, or final time. A target-shortened step
may be smaller than `dt_min`; `dt_min` governs retry shrinkage, while exact event
landing takes precedence.

A real Reaktoro 2.13 rejection/retry probe demonstrated exact species and
element-total equivalence, with equal retry iteration counts, between the
reused solver and a newly constructed solver after native state restoration.
The controller therefore reuses the solver; reconstruction is not justified by
the observed runtime behaviour.

Before solver construction, adaptive preflight computes the lower bound on
accepted steps by splitting the duration at all forced output/checkpoint times
and summing `ceil(interval / dt_max)`. A case whose lower bound exceeds
`max_internal_steps` cannot complete and is rejected.

### 9.3 Adaptive Long-Horizon Timestep

Purpose:

```text
Run kinetic simulations from seconds to thousands of years using adaptive timesteps, scheduled output times, checkpointing, and scientific acceptance criteria.
```

This mode is required for possible 10,000-year PhD simulations.

#### Human-Readable Time Units

Allowed units:

```text
second
seconds
minute
minutes
hour
hours
day
days
year
years
```

Any use of `year` or `years` requires an explicit positive
`year_definition_days`; there is no implicit year length.

#### Scheduled Output Times

Long simulations must not write a result row for every internal solver step.

The controller must support:

```text
fixed output times
logarithmic output times
hybrid output schedule
include initial time
include final time
```

First implementation rule:

```text
Do not interpolate output states.
Shorten dt to land exactly on scheduled output times.
```

#### Exact Output-Time Landing

If an adaptive step would pass a scheduled output time, shorten it.

Example:

```text
current time = 90 years
adaptive dt = 20 years
next output time = 100 years
actual dt used = 10 years
```

#### Scientific Acceptance Checks

A step should not be accepted only because Reaktoro solved successfully.

Checks:

```text
delta pH
delta saturation index
mineral fractional change
selected species fractional change
relative reaction-rate change, if rate extraction is verified
NaN/Inf
negative amounts
```

All thresholds must be configurable and recorded in `manifest.json`.

Rate-based acceptance is conditional:

```text
If reaction_rates: false or rate extraction is not verified:
    do not use max_relative_rate_change.
    record that rate-based acceptance was disabled.
```

#### Quasi-Steady Long-Time Growth

The controller may grow timesteps aggressively only when chemistry is slow.

Slow-change evidence may include:

```text
small pH change
small saturation-index change
small mineral fractional change
small or slowly changing reaction rates, if available
repeated solver success
```

Large steps must still obey:

```text
scheduled output times
checkpoint rules
acceptance checks
dt_max
```

#### Checkpointing

Checkpointing is required for `adaptive_long_horizon` under
`solver.timestep.checkpoint_schedule`.

Suggested path:

```text
outputs/<case_name>/checkpoints/
```

Minimum checkpoint content:

```text
checkpoint metadata
current time
current timestep
latest accepted timeseries row
readable ChemicalState export
solver-controller state
```

Readable checkpoints are acceptable for first implementation. Binary restart is optional only if straightforward and tested.

#### Checkpointing vs Restart

Checkpointing means:

```text
save readable intermediate state for diagnostics, evidence, and possible manual recovery.
```

Restart means:

```text
automatically resume simulation from a checkpoint.
```

First implementation rule:

```text
checkpointing can be implemented before restart;
automatic restart must remain disabled unless explicitly implemented and tested.
```

Suggested restart placeholder:

```yaml
solver:
  restart:
    enabled: false
    from_checkpoint: null
```

If restart is enabled before implementation exists, fail clearly.

#### State Rollback

Required:

```text
Before attempting dt:
    save reliable rollback copy or reconstruction source.

If rejected:
    restore previous accepted state;
    reduce dt;
    retry.
```

Adaptive stepping is not complete until rollback is tested.

If direct `ChemicalState` copying is unreliable, reconstruct from saved species/mineral amounts and conditions.

#### Rejected-Step Logging

Long-horizon mode records rejected and accepted attempts in the same
deterministic `solver_history.csv` schema. A separate rejected-step table is
unnecessary because `accepted` provides a lossless filter.

#### Long-Horizon Controller Algorithm

```text
1. Build scheduled output times.
2. Set current time = 0.
3. Set dt = dt_initial.
4. Save initial accepted state.
5. While current time < final time:

   a. Limit dt so it does not exceed:
      - final simulation time;
      - next scheduled output time;
      - configured dt_max.

   b. Save rollback copy of current accepted state.

   c. Attempt kinetic solve for dt.

   d. Extract trial-state quantities:
      - pH
      - selected species
      - mineral amounts
      - saturation indices
      - reaction rates if enabled and verified.

   e. Run acceptance checks.

   f. If accepted:
      - advance time;
      - write row if current time is a scheduled output time;
      - write solver history;
      - checkpoint if required;
      - grow dt according to controller rules.

   g. If rejected:
      - restore rollback state;
      - write rejected-step record;
      - shrink dt;
      - retry.

   h. If dt < dt_min or retries exceeded:
      - fail cleanly;
      - write diagnostics;
      - stop or return partial results depending on config.
```

#### Long-Horizon Warning

Do not use very small `dt_max` for 10,000-year runs.

Example:

```text
dt_max = 3600 s
```

is not usable for 10,000 years.

For long simulations, `dt_max` must support days, years, or decades depending on reaction progress and configured acceptance checks.

---

## 10. Roadmap Solver Safety Features

This separate `solver.safety` block is not active. Implemented finite and
negative-amount checks live under `solver.timestep.acceptance`; blocking
wall-time interruption and depletion actions remain future work.

Recommended safety block:

```yaml
solver:
  safety:
    stop_on_failure: true
    max_wall_time_per_step_s: 30
    max_total_wall_time_s: 600
    fail_on_nan: true
    fail_on_negative_amounts: true
    mineral_depletion:
      enabled: true
      tolerance_mol: 1.0e-18
      action: stop
```

### Wall-Time Warning

A blocking Reaktoro C++ solver call may not be interruptible using only normal Python timing logic.

Implement:

```text
internal wall-time recording
clear diagnostics after over-threshold return
subprocess-level timeout only if robust hard timeout is required
```

Do not claim that `time.time()` can always stop a blocking solver call.

### Mineral Depletion

Do not silently continue with negative mineral amount.

Recommended:

```text
action: stop
```

Do not silently clamp in scientific runs unless explicitly requested and documented.

---

## 11. Conservation Diagnostics

Long simulations require conservation diagnostics.

Recommended block:

```yaml
solver:
  conservation:
    enabled: true
    element_balance: true
    charge_balance: true
    carbon_balance: false
```

Record:

```text
element_balance_error
charge_balance_error
water_mass_change
carbon_balance_error, if carbon inventory enabled
```

These may be diagnostics only at first. If used as acceptance criteria later, thresholds must be explicit.

---

## 12. Reaction-Rate Extraction Policy

Reaction-rate plots and rate-based acceptance are useful but must not be assumed available automatically.

Use Reaktoro's accepted-state runtime properties for both supported models:

```text
ChemicalProps.reactionRate(mineral.name) → total rate in mol/s
ChemicalProps.surfaceArea(mineral.name) → live area in m²
```

Only divide by the live surface area when it is nonzero. Do not independently
recompute either kinetic model's equations for standard diagnostics.

Rules:

```text
postprocessing.reaction_rates: false
→ do not write reaction-rate columns or use rate-based acceptance

postprocessing.reaction_rates: true
→ verify rate extraction implementation
→ write reaction-rate diagnostics
→ allow rate-based acceptance if configured
```

---

## 13. Optional Surface-Area Evolution Placeholder

For long simulations, constant surface area is a major assumption.

Add disabled-by-default placeholder:

```yaml
solver:
  geochemical_controls:
    surface_area_update:
      enabled: false
      mode: constant
```

Allowed mode for now:

```text
constant
```

Future modes may include:

```text
proportional_to_remaining_moles
geometric_scaling
user_defined_table
```

No surface-area evolution should be silently applied.

---

## 14. Three-Mineral Development Case

Use a development case with:

```text
Calcite
Quartz
Illite
```

Purpose:

```text
small enough to debug
chemically meaningful
carbonate + silicate + clay
avoids eight-mineral stiffness during feature development
```

This case should exercise:

```text
selected kinetic-parameter loading
mineral mapping
fixed-fugacity initial equilibrium
redox apply_during validation, if redox enabled
closed kinetic stepping
adaptive_long_horizon validation
scheduled output generation
checkpointing
solver history
rejected-step logging
base output package
optional plot toggles
```

Do not use the eight-mineral case as the first solver-performance development case.

---

## 15. Solver Success Criteria

The solver upgrade is successful if:

```text
1. All solver workflows are selectable from YAML.
2. Standard backend works by default.
3. Smart backend is optional, guarded, and logged.
4. CO₂ fixed-fugacity staging is explicit.
5. Redox/pE staging is explicit.
6. Fixed, adaptive, and adaptive_long_horizon timestep modes are supported.
7. Duration and timestep control live under solver.timestep, not kinetics.
8. Long-horizon simulations can use human-readable time units.
9. Scheduled output times are respected exactly without interpolation.
10. Rejected steps are logged and do not corrupt accepted state.
11. Checkpoints are written when enabled.
12. Restart is not implied by checkpointing.
13. Safety, conservation, and acceptance checks are configurable.
14. No Reaktoro internal code is modified.
15. The code remains simple, explicit, and user-editable.
```

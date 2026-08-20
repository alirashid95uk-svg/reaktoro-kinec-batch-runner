# Config Schema and Feature Options — Active Contract

## Runtime Status

The active `CaseConfig` implements fixed timesteps, the legacy solver-feasibility
adaptive controller, and an explicitly selected Richardson error-controlled
adaptive controller. It also implements scheduled timeseries output, explicit
accepted-state checkpoints, standard Reaktoro solvers, and the current
postprocessing/output blocks.

This document describes only fields accepted by the strict runtime schema.
Unsupported or unimplemented concepts must not appear as disabled YAML
placeholders.

In particular, there is currently no active:

```text
solver.backend
solver.restart
solver.safety
solver.conservation
solver.geochemical_controls
```

A schema field belongs here only when the same implementation change provides
and verifies its runtime behaviour.

## 1. Purpose

This file defines the user-facing YAML configuration contract and validation
rules. Runtime Pydantic models under `batch_runner/config/` remain the final
implementation authority.

Output meanings are defined in `output_package_design.md`. Solver algorithms are
defined in `solver_workflow_and_long_horizon_timestep.md`.

## 2. Core Configuration Rules

- Unknown fields fail validation.
- Invalid combinations fail validation.
- Scientific behaviour must be explicit.
- Do not invent scientific values or defaults.
- Duration and timestep control live under `solver.timestep`.
- Database, kinetic parameters, mineral identities, surface areas, boundary
  conditions, redox controls, and timestep controls must not be silently
  changed.

Launcher preflight adds no scientific YAML option. It may override only the
output directory used for read-only construction checks.

## 3. Active Top-Level Blocks

```yaml
case:
paths:
database:
activity_models:
physical:
brine:
co2:
redox:
kinetics:
minerals:
solver:
postprocessing:
validation:
outputs:
```

## 4. `kinetics`

`kinetics` controls whether kinetic reactions exist, which supported rate model
is selected, and where its parameters come from.

```yaml
kinetics:
  enabled: true
  model: palandri_kharaka
  path: data/kinetics/PalandriKharaka_local.yaml
```

Rules:

```text
kinetics.enabled: true
-> model defaults to palandri_kharaka when omitted
-> path defaults from the selected model when omitted

kinetics.model: kinec
-> path defaults to data/kinetics/kinec_rates_minimal.yaml

kinetics.enabled: false
-> model/path are forbidden

model selection
-> never infer from filename
-> no fallback between parameter files
```

Duration or timestep fields are forbidden under `kinetics`.

## 5. `redox`

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

Rules:

```text
redox.enabled: false
-> pe forbidden
-> apply_during forbidden

redox.enabled: true
-> pe required
-> apply_during required
```

The selected stage controls when pE conditions are passed to Reaktoro.

## 6. `solver`

The active solver block has exactly two owners:

```yaml
solver:
  workflow:
    mode: fixed_fugacity_initial_equilibrium_then_closed_kinetics

  timestep:
    mode: fixed
    ...
```

Unknown sibling blocks under `solver` must fail validation. Checkpointing is
owned by `solver.timestep.checkpoint_schedule`; it is not a separate output or
restart feature.

## 7. `solver.workflow`

Allowed modes:

```text
equilibrium_only
closed_kinetics
fixed_fugacity_initial_equilibrium_then_closed_kinetics
fixed_fugacity_during_kinetic_steps
```

Rules:

```text
equilibrium_only
-> kinetics.enabled: false

closed_kinetics
-> kinetics.enabled: true

fixed_fugacity_initial_equilibrium_then_closed_kinetics
-> kinetics.enabled: true
-> co2.mode: fixed_fugacity

fixed_fugacity_during_kinetic_steps
-> kinetics.enabled: true
-> co2.mode: fixed_fugacity
```

Redox compatibility is validated against `redox.apply_during`.

## 8. `solver.timestep`

Active modes:

```text
fixed
adaptive
adaptive_error_controlled
```

Each mode uses strict fields. Fields from another mode are rejected.
`adaptive_error_controlled` currently supports direct kinetic workflows only;
configurations that require an initial-equilibrium stage are rejected.

### 8.1 Fixed

```yaml
solver:
  timestep:
    mode: fixed
    max_internal_steps: 100000
    time:
      duration_value: 10
      duration_unit: seconds
    step_size:
      dt: {value: 1, unit: second}
    output_schedule:
      mode: every_internal_step
      include_initial: true
      include_final: true
      explicit_times: []
      logarithmic: null
    checkpoint_schedule:
      enabled: false
      times: []
```

Rules:

- `time.duration_value` and `time.duration_unit` are required;
- `step_size.dt` is required;
- `max_internal_steps` is positive and defaults to `100000`;
- configured and converted time values must be finite;
- fixed mode forbids adaptive-only fields;
- output/checkpoint timestamps must lie inside the duration;
- schedule duplicates are removed during resolution;
- impossible fixed-grid/schedule combinations are rejected before execution.

### 8.2 Output Schedule

Allowed modes:

```text
every_internal_step
explicit
logarithmic
hybrid
```

`every_internal_step` is the compatibility default. `explicit` uses
`explicit_times`; `logarithmic` requires a logarithmic definition; `hybrid`
requires both.

`include_initial` and `include_final` control whether those boundary states are
written to the timeseries. They do not change the solver's final-time target.

### 8.3 Checkpoint Schedule

```yaml
checkpoint_schedule:
  enabled: true
  times:
    - {value: 100, unit: years}
```

Rules:

- enabled checkpointing requires at least one time;
- disabled checkpointing forbids times;
- checkpoint times are independent of timeseries output times;
- checkpoints are written only for accepted states;
- checkpointing does not provide restart capability.

### 8.4 Adaptive

```yaml
solver:
  timestep:
    mode: adaptive
    time:
      duration_value: 1
      duration_unit: day
    step_size:
      dt_initial: {value: 1, unit: second}
      dt_min: {value: 1.0e-6, unit: second}
      dt_max: {value: 1, unit: hour}
      growth_factor: 1.25
      shrink_factor: 0.5
      max_retries_per_step: 8
    max_internal_steps: 100000
    output_schedule:
      mode: explicit
      include_initial: true
      include_final: true
      explicit_times: []
      logarithmic: null
    checkpoint_schedule:
      enabled: false
      times: []
```

Rules:

- `dt_min <= dt_initial <= dt_max` after resolution;
- `growth_factor > 1`;
- `0 < shrink_factor < 1`;
- successful Reaktoro solves are accepted and grow the controller timestep;
- failed or raised Reaktoro solves restore the accepted state, shrink the
  timestep, and retry from the same accepted time;
- adaptive preflight rejects cases whose lower bound on accepted steps exceeds
  `max_internal_steps`.

`mode: adaptive` retains this solver-feasibility algorithm. It does not invoke
Richardson trials and requires no configuration migration.

### 8.5 Richardson Error-Controlled Adaptive

```yaml
solver:
  timestep:
    mode: adaptive_error_controlled
    time:
      duration_value: REQUIRED
      duration_unit: REQUIRED
    step_size:
      dt_initial: {value: REQUIRED, unit: REQUIRED}
      dt_min: {value: REQUIRED, unit: REQUIRED}
      dt_max: {value: REQUIRED, unit: REQUIRED}
      safety_factor: REQUIRED
      growth_factor: REQUIRED
      shrink_factor: REQUIRED
      solver_failure_shrink_factor: REQUIRED
      max_retries_per_step: REQUIRED
    error_control:
      temporal_order: REQUIRED
      relative_tolerance: REQUIRED
      negative_amount_tolerance: {value: REQUIRED, unit: mol}
      controlled_minerals:
        - name: REQUIRED_KINETIC_MINERAL
          absolute_tolerance: {value: REQUIRED, unit: mol}
          reference_floor: {value: REQUIRED, unit: mol}
    events:
      hard_mineral_exhaustion: null
      soft: null
    max_internal_steps: 100000
    output_schedule: REQUIRED
    checkpoint_schedule: {enabled: false, times: []}
```

Rules:

- every configured kinetic mineral appears exactly once in
  `error_control.controlled_minerals`;
- `temporal_order` is required, finite, positive, and has no default; it is a
  configured estimator assumption until representative temporal-convergence
  evidence establishes it;
- absolute tolerances, reference floors, and the non-negative admissibility
  tolerance are explicit finite molar values;
- the tolerance scale must remain positive at zero mineral amount;
- `0 < safety_factor < 1`, `growth_factor > 1`, and both shrink factors lie
  strictly between zero and one;
- `dt_min <= dt_initial <= dt_max` after unit resolution;
- solver-failure shrinkage is distinct from temporal-error rejection;
- `events.hard_mineral_exhaustion` and `events.soft` are explicitly configured
  or set to `null`; no event thresholds are hidden defaults;
- a hard-exhaustion block requires a strictly positive molar amount tolerance,
  time tolerance, post-event restart timestep, and localisation limit;
- a soft-event block may enable SI crossings, a maximum pH change, secondary
  mineral appearance in mol, and paired reaction-rate threshold/floor values in
  mol/s; all soft events cap only a subsequent proposal;
- output/checkpoint/final target landing can shorten a trial below `dt_min`, but
  a rejected sub-minimum exact-landing trial is never retried with a larger step.

## 9. Time Units

Allowed units:

```text
second / seconds
minute / minutes
hour / hours
day / days
year / years
```

Canonical runtime time is seconds. Any configured use of `year`/`years`
requires a positive explicit `year_definition_days`; there is no implicit year
length.

## 10. Postprocessing

The active `postprocessing` block owns result selection and optional diagnostic
products, including requested species/minerals, aqueous molalities, saturation
indices, reaction rates, element budgets, carbon inventory, mineral-volume
change, regime classification, surface-area audit, workflow comparison,
secondary-mineral assemblage, surrogate-dataset export, and
porosity/permeability inference status.

These postprocessing products do not create new solver physics. In particular,
existing element/carbon budgets are reconstructed reporting diagnostics; they
must not be represented as a generic `solver.conservation` feature.

## 11. Validation

The active validation target/ledger machinery records configured comparison
targets and outcomes. It is reporting functionality, not automatic calibration,
experiment fitting, or permission to alter scientific inputs.

## 12. Outputs

The `outputs` block controls the active manifest, diagnostics, timeseries,
summaries, solver history, plots, debug artifacts, and terminal monitor. Output
meanings and completeness rules are defined in `output_package_design.md`.

The optional presentation block defaults to an enabled pH monitor:

```yaml
outputs:
  monitor:
    enabled: true
    refresh_interval_s: 0.5
    scalars: [pH]
    species: [Ca+2, HCO3-]
    minerals: [Calcite]
    result_times:
      - {value: 14, unit: days}
```

Allowed scalar names are `pH`, `ionic_strength_molal`, and
`alkalinity_eq_per_l`. Monitor species and minerals must already be selected by
`postprocessing.requested_species` and `postprocessing.requested_minerals`.
Each monitor result time must already exist in the resolved scientific output
schedule; it is rejected otherwise and never becomes a solver target. These
fields select presentation only and do not change accepted states or output
times.

## 13. Removed and Unsupported Fields

These fields are deliberately **not** part of active case YAML and must be
rejected as unknown fields:

```text
solver.backend
solver.restart
solver.safety
solver.conservation
solver.geochemical_controls
solver.workflow.precondition_kinetics
solver.timestep.acceptance
solver.timestep.mode: adaptive_long_horizon
```

They are not part of the current runtime contract and remain invalid
configuration fields or modes.

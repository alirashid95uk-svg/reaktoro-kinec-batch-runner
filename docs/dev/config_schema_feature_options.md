# Config Schema and Feature Options — Active Contract

## Runtime Status

The active `CaseConfig` implements fixed, adaptive, and
`adaptive_long_horizon` timesteps, scheduled timeseries output, explicit
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
    precondition_kinetics: true

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
adaptive_long_horizon
```

Each mode uses strict fields. Fields from another mode are rejected.

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
    acceptance:
      enabled: true
      fail_on_non_finite: true
      negative_amount_tolerance_mol: null
      max_delta_pH: null
      max_delta_saturation_index: null
      selected_species_change: null
      mineral_change: null
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
      logarithmic: null
    checkpoint_schedule:
      enabled: false
      times: []
```

At least one acceptance check must be active. The example above therefore needs
another configured check if `fail_on_non_finite` is set false.

Rules:

- `dt_min <= dt_initial <= dt_max` after resolution;
- `growth_factor > 1`;
- `0 < shrink_factor < 1`;
- amount-change checks use absolute + relative tolerance with an explicit
  reference floor;
- `max_relative_rate_change` must remain null because rate-based adaptive
  acceptance is not verified;
- adaptive preflight rejects cases whose lower bound on accepted steps exceeds
  `max_internal_steps`.

Element-conservation acceptance must not be applied blindly to externally
constrained/open kinetic workflows.

### 8.5 `adaptive_long_horizon`

This is the active adaptive controller with additional requirements:

```text
output_schedule.mode != every_internal_step
include_final: true
checkpoint_schedule.enabled: true
```

Human-readable year units are supported only with explicit
`year_definition_days`.

`adaptive_long_horizon` is an implemented timestep mode using the same adaptive
controller plus these additional schema requirements.

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
summaries, solver history, plots, and debug artifacts. Output meanings and
completeness rules are defined in `output_package_design.md`.

## 13. Removed Placeholder Fields

These fields are deliberately **not** part of active case YAML and must be
rejected as unknown fields:

```text
solver.backend
solver.restart
solver.safety
solver.conservation
solver.geochemical_controls
```

They were placeholders without implemented runtime behaviour and remain invalid
configuration fields.

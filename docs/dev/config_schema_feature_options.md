# Config Schema and Feature Options — Final Codex Contract

## Runtime V1 Status

The active `CaseConfig` implements fixed, adaptive, and
`adaptive_long_horizon` timesteps, scheduled timeseries output, explicit
accepted-state checkpoints, standard Reaktoro solvers, and optional Objective
1 audit outputs. Adaptive rollback and state-change acceptance checks are
active. Smart solvers and automatic restart remain disabled.

## 1. Purpose

This file defines the YAML configuration options and validation rules for the configurable solver and output feature upgrade.

It answers:

```text
What can the user turn on/off from the case file?
What fields are required?
What fields are forbidden?
What combinations are invalid?
What config blocks should Codex implement in config.py?
```

Output-file meanings are defined in:

```text
output_package_design.md
```

Solver algorithms are defined in:

```text
solver_workflow_and_long_horizon_timestep.md
```

---

## 2. Core Config Rule

```text
All optional features must be controlled from YAML.
Unknown fields must fail validation.
Invalid combinations must fail validation.
No hidden defaults for scientific behaviour.
```

Use strict Pydantic models.

---

## 3. Top-Level Config Blocks

Recommended top-level YAML blocks:

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
outputs:
```

---

## 4. `kinetics` Block

### 4.1 Purpose

Define whether kinetic reactions exist and where kinetic parameters come from.

Do **not** define duration or timestep here.

### 4.2 Recommended Schema

```yaml
kinetics:
  enabled: true
  path: data/kinetics/kinec_rates_minimal.yaml
```

### 4.3 Validation

```text
kinetics.enabled: true
→ requires path

kinetics.enabled: false
→ forbids path

duration and dt fields
→ forbidden under kinetics
→ must live under solver.timestep
```

---

## 5. `redox` Block

### 5.1 Purpose

Define whether pE/redox constraint is used and when it is applied.

### 5.2 Recommended Schema

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

### 5.3 Validation

```text
redox.enabled: false
→ forbids pe
→ forbids apply_during

redox.enabled: true
→ requires pe
→ requires apply_during

redox.apply_during: initial_equilibrium_only
→ apply pE during initial equilibrium conditioning only
→ do not pass pE condition to kinetic timesteps

redox.apply_during: kinetic_steps
→ pass pE condition to kinetic solve only in constrained kinetic workflows that support it
```

Record runtime behaviour in:

```text
manifest.json
diagnostics.json
```

---

## 6. `solver` Block

Recommended structure:

```yaml
solver:
  backend:
    type: standard
    allow_experimental_smart_solver: false
    fallback_to_standard: false

  workflow:
    mode: fixed_fugacity_initial_equilibrium_then_closed_kinetics
    precondition_kinetics: true

  timestep:
    mode: adaptive_long_horizon
    ...

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

  conservation:
    enabled: true
    element_balance: true
    charge_balance: true
    carbon_balance: false

  geochemical_controls:
    surface_area_update:
      enabled: false
      mode: constant

  restart:
    enabled: false
    from_checkpoint: null
```

Do not add a separate checkpoint-output flag. Checkpoint creation and writing
are controlled by `solver.timestep.checkpoint_schedule`.

---

## 7. `solver.backend`

### 7.1 Allowed Values

```text
standard
smart
```

### 7.2 Schema

```yaml
solver:
  backend:
    type: standard
    allow_experimental_smart_solver: false
    fallback_to_standard: false
```

### 7.3 Validation

```text
backend.type: standard
→ allow_experimental_smart_solver may be false
→ fallback_to_standard should be false or ignored

backend.type: smart
→ allow_experimental_smart_solver must be true
```

Runtime checks:

```text
If backend.type: smart and Smart solvers are unavailable:
    fallback_to_standard: true  → use standard backend and record warning
    fallback_to_standard: false → fail clearly
```

---

## 8. `solver.workflow`

### 8.1 Allowed Values

```text
equilibrium_only
closed_kinetics
fixed_fugacity_initial_equilibrium_then_closed_kinetics
fixed_fugacity_during_kinetic_steps
```

### 8.2 Schema

```yaml
solver:
  workflow:
    mode: fixed_fugacity_initial_equilibrium_then_closed_kinetics
    precondition_kinetics: true
```

### 8.3 Validation

```text
workflow.mode: equilibrium_only
→ requires kinetics.enabled: false

workflow.mode: closed_kinetics
→ requires kinetics.enabled: true

workflow.mode: fixed_fugacity_initial_equilibrium_then_closed_kinetics
→ requires kinetics.enabled: true
→ requires co2.mode: fixed_fugacity

workflow.mode: fixed_fugacity_during_kinetic_steps
→ requires kinetics.enabled: true
→ requires co2.mode: fixed_fugacity
```

Redox validation is handled by the `redox.apply_during` rules.

---

## 9. `solver.timestep`

Required modes:

```text
fixed
adaptive
adaptive_long_horizon
```

Each mode must have a clean schema. Do not allow fields from unrelated modes.

### 9.1 Fixed Timestep Schema

```yaml
solver:
  timestep:
    mode: fixed
    max_internal_steps: 100000
    time:
      duration_value: 10
      duration_unit: seconds
    step_size:
      dt: { value: 1, unit: second }
    output_schedule:
      mode: hybrid
      include_initial: true
      include_final: true
      explicit_times:
        - { value: 30, unit: seconds }
      logarithmic:
        start: { value: 1, unit: second }
        end: { value: 10, unit: seconds }
        points_per_decade: 4
    checkpoint_schedule:
      enabled: false
      times: []
```

Validation:

```text
requires time.duration_value
requires time.duration_unit
requires step_size.dt
max_internal_steps must be a positive integer; default 100000
duration, dt, converted seconds, and year_definition_days must be finite
output_schedule.mode is every_internal_step, explicit, logarithmic, or hybrid
every_internal_step is the compatibility default and is generated lazily
explicit uses explicit_times; logarithmic uses logarithmic; hybrid requires both
include_initial and include_final add those boundary rows when true
all resolved output timestamps must be within duration; duplicates are removed
reject an oversized logarithmic schedule before generating its timestamps
checkpoint_schedule.enabled true requires explicit times within duration
checkpoint timestamps are independent of timeseries output timestamps
reject before execution when fixed-grid plus schedule-split steps exceed max_internal_steps
forbids adaptive-only fields
forbids long-horizon-only fields
```

Canonical runtime time is seconds. All schedule values use the same configured
`year_definition_days` and Decimal-based conversion as duration and `dt`.
Existing fixed-step YAML without `output_schedule` retains one row per fixed
step plus the initial row through the documented `every_internal_step` default.

### 9.2 Adaptive Timestep Schema

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
      fail_on_negative_amounts: true
      max_delta_pH: 0.10
      max_delta_saturation_index: 0.25
      max_mineral_fraction_change: 0.05
      max_selected_species_fraction_change: 0.10
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

Validation:

```text
requires time
requires step_size.dt_initial
requires step_size.dt_min
requires step_size.dt_max
requires growth_factor
requires shrink_factor
requires max_retries_per_step
requires acceptance
forbids fixed step_size.dt
requires at least one active acceptance check
requires dt_min <= dt_initial <= dt_max
growth_factor must exceed 1; shrink_factor must be between 0 and 1
max_relative_rate_change must remain null because runtime rate acceptance is unverified
element conservation is forbidden for fixed-fugacity kinetic steps
```

### 9.3 Adaptive Long-Horizon Timestep Schema

```yaml
solver:
  timestep:
    mode: adaptive_long_horizon
    time:
      duration_value: 10000
      duration_unit: years
      year_definition_days: 360
    step_size:
      dt_initial: { value: 1, unit: second }
      dt_min: { value: 1.0e-6, unit: second }
      dt_max: { value: 100, unit: years }
      growth_factor: 1.5
      shrink_factor: 0.5
      max_retries_per_step: 12
    acceptance:
      enabled: true
      fail_on_non_finite: true
      fail_on_negative_amounts: true
      max_delta_pH: 0.10
      max_delta_saturation_index: 0.25
      max_mineral_fraction_change: 0.02
      max_selected_species_fraction_change: 0.10
      element_conservation:
        enabled: false
        relative_tolerance: null
        absolute_tolerance_mol: null
      max_relative_rate_change: null
    max_internal_steps: 100000
    output_schedule:
      mode: logarithmic
      include_initial: true
      include_final: true
      explicit_times: []
      logarithmic:
        start: { value: 1, unit: day }
        end: { value: 10000, unit: years }
        points_per_decade: 8
    checkpoint_schedule:
      enabled: true
      times:
        - { value: 100, unit: years }
```

Validation:

```text
requires time
requires output_schedule
requires include_final: true
requires step_size
requires acceptance
requires checkpoint_schedule.enabled: true
forbids every_internal_step output

if duration or any configured timestep value uses year/years
→ requires year_definition_days

forbids fixed step_size.dt
```

---

## 10. Time Units

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

Rules:

```text
Convert internally to seconds.
Record year_definition_days in manifest.
Do not force the user to manually write 10,000 years in seconds.
```

---

## 11. `solver.safety`

Schema:

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

Validation:

```text
max_wall_time_per_step_s > 0
max_total_wall_time_s > 0
mineral_depletion.action allowed values: stop, warn
```

Do not silently clamp mineral amounts unless a future explicit config option is added.

---

## 12. `solver.conservation`

Schema:

```yaml
solver:
  conservation:
    enabled: true
    element_balance: true
    charge_balance: true
    carbon_balance: false
```

Validation:

```text
carbon_balance: true
→ requires postprocessing.carbon_inventory.enabled: true
```

---

## 13. `solver.geochemical_controls`

Schema:

```yaml
solver:
  geochemical_controls:
    surface_area_update:
      enabled: false
      mode: constant
```

Allowed modes now:

```text
constant
```

Validation:

```text
surface_area_update.enabled: false
→ mode must be constant

surface_area_update.enabled: true
→ only mode constant is currently supported unless future modes are implemented
```

No surface-area evolution should be silently applied.

---

## 14. `solver.restart`

Schema:

```yaml
solver:
  restart:
    enabled: false
    from_checkpoint: null
```

Validation:

```text
restart.enabled: false
→ from_checkpoint must be null

restart.enabled: true
→ requires from_checkpoint
→ fail clearly if restart support is not implemented
```

Checkpointing does not automatically imply restart support.

---

## 15. `postprocessing`

Recommended structure:

```yaml
postprocessing:
  requested_species:
    - H+
    - Ca+2
    - SiO2
    - Al+3

  requested_minerals:
    - Calcite
    - Quartz
    - Illite

  aqueous_molalities: true
  saturation_indices: true
  reaction_rates: false

  element_budget:
    enabled: false
    elements: []

  carbon_inventory:
    enabled: false
    carbon_species: []
    carbon_minerals: {}
```

Validation:

```text
requested_species must not be empty if aqueous_summary or species output is enabled

requested_minerals must not be empty if mineral_summary, mineral plot, or saturation-index plot is enabled

reaction_rates: true
→ requires rate extraction implementation to be available

element_budget.enabled: true
→ requires elements list

carbon_inventory.enabled: true
→ requires carbon_species or carbon_minerals mapping
```

---

## 16. `outputs`

Recommended structure:

```yaml
outputs:
  manifest:
    enabled: true
    include_input_snapshot: true

  diagnostics:
    enabled: true

  timeseries:
    enabled: true
    include_species_amounts: true
    include_species_molalities: true
    include_mineral_amounts: true
    include_mineral_deltas: true
    include_saturation_indices: true
    include_solver_columns: true

  summaries:
    mineral_summary: true
    aqueous_summary: true
    carbon_inventory: false
    element_budget: false

  solver_history:
    enabled: true
    include_rejected_steps: true

  plots:
    enabled: true
    pH: true
    mineral_change: true
    saturation_index: true
    species_molality: false
    reaction_rate: false
    solver_dt: true
    solver_iterations: true

  debug:
    enabled: true
    mineral_connection: true
    resolved_config: false
    final_state: true

```

Validation:

```text
outputs.plots.enabled: false
→ no plot files written

outputs.plots.enabled: true
→ at least one plot flag must be true

plots.solver_dt: true
→ outputs.solver_history.enabled must be true

plots.solver_iterations: true
→ outputs.solver_history.enabled must be true

plots.reaction_rate: true
→ postprocessing.reaction_rates must be true

summaries.carbon_inventory: true
→ postprocessing.carbon_inventory.enabled must be true

summaries.element_budget: true
→ postprocessing.element_budget.enabled must be true

checkpoint files are controlled only by solver.timestep.checkpoint_schedule.enabled
```

---

## 17. `co2`

Existing CO₂ modes should remain:

```text
disabled
finite
fixed_fugacity
```

Required validation with solver workflow:

```text
workflow.fixed_fugacity_initial_equilibrium_then_closed_kinetics
→ requires co2.mode: fixed_fugacity

workflow.fixed_fugacity_during_kinetic_steps
→ requires co2.mode: fixed_fugacity
```

---

## 18. `minerals`

Supported roles:

```text
equilibrium
kinetic
```

No diagnostic mineral role.

Validation:

```text
kinetic mineral
→ requires initial_amount
→ requires surface_area
→ requires kinetic YAML record
→ requires thermodynamic mineral

equilibrium mineral
→ requires thermodynamic mineral
→ does not require surface_area
→ does not require kinetic YAML record
```

Missing kinetic record, missing surface area, and missing thermodynamic mineral remain hard failures.

---

## 19. Historical Roadmap Profile (Not Runtime YAML)

The following older design sketch contains unimplemented fields and is not
accepted by the strict `CaseConfig`. Use the mode schemas in Section 9 for
runnable timestep YAML.

Use this as the first enhanced development profile.

```yaml
solver:
  backend:
    type: standard
    allow_experimental_smart_solver: false
    fallback_to_standard: false

  workflow:
    mode: fixed_fugacity_initial_equilibrium_then_closed_kinetics
    precondition_kinetics: true

  timestep:
    mode: adaptive_long_horizon

    time:
      duration_value: 10000
      duration_unit: years
      year_definition_days: 365.25
      output_schedule:
        mode: hybrid
        include_initial: true
        include_final: true
        log_times:
          enabled: true
          start: { value: 1, unit: day }
          end: { value: 10000, unit: years }
          points_per_decade: 8

    step_size:
      dt_initial: { value: 1, unit: second }
      dt_min: { value: 1.0e-6, unit: second }
      dt_max: { value: 100, unit: years }
      growth_factor: 1.5
      shrink_factor: 0.5
      max_growth_after_accept: 2.0
      max_retries_per_step: 12

    acceptance:
      enabled: true
      max_delta_pH: 0.10
      max_delta_saturation_index: 0.25
      max_mineral_fraction_change: 0.02
      max_species_fraction_change: 0.10
      max_relative_rate_change: null
      fail_on_nan: true
      fail_on_negative_amounts: true

    long_horizon:
      enable_quasi_steady_growth: true
      rate_floor_mol_s: 1.0e-20
      near_equilibrium_si_tolerance: 0.05
      require_si_stability_for_large_steps: true
      allow_large_steps_when_slow: true
      require_checkpoint_before_large_step: true

    checkpoints:
      enabled: true
      interval: { value: 100, unit: years }
      save_before_dt_larger_than: { value: 1, unit: year }
      keep_last_n: 20

    failure_recovery:
      restore_previous_state_on_reject: true
      write_rejected_steps: true
      stop_if_dt_below_min: true
      stop_if_repeated_failures: true
      max_consecutive_rejected_steps: 20

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

  conservation:
    enabled: true
    element_balance: true
    charge_balance: true
    carbon_balance: false

  geochemical_controls:
    surface_area_update:
      enabled: false
      mode: constant

  restart:
    enabled: false
    from_checkpoint: null

postprocessing:
  requested_species:
    - H+
    - Ca+2
    - SiO2
    - Al+3
  requested_minerals:
    - Calcite
    - Quartz
    - Illite
  aqueous_molalities: true
  saturation_indices: true
  reaction_rates: false
  element_budget:
    enabled: false
    elements: []
  carbon_inventory:
    enabled: false
    carbon_species: []
    carbon_minerals: {}

outputs:
  manifest:
    enabled: true
    include_input_snapshot: true
  diagnostics:
    enabled: true
  timeseries:
    enabled: true
    include_species_amounts: true
    include_species_molalities: true
    include_mineral_amounts: true
    include_mineral_deltas: true
    include_saturation_indices: true
    include_solver_columns: true
  summaries:
    mineral_summary: true
    aqueous_summary: true
    carbon_inventory: false
    element_budget: false
  solver_history:
    enabled: true
    include_rejected_steps: true
  plots:
    enabled: true
    pH: true
    mineral_change: true
    saturation_index: true
    species_molality: false
    reaction_rate: false
    solver_dt: true
    solver_iterations: true
  debug:
    enabled: true
    mineral_connection: true
    resolved_config: false
    final_state: true
  checkpoints:
    enabled: true
```

---

## 20. Required Config Tests

Suggested tests:

```text
1. backend.type smart requires allow_experimental_smart_solver true.
2. redox.enabled false forbids pe and apply_during.
3. redox.enabled true requires pe and apply_during.
4. fixed timestep config forbids adaptive-only fields.
5. adaptive timestep config forbids fixed dt and long-horizon-only fields.
6. adaptive_long_horizon requires time, step_size, acceptance, output_schedule, and checkpoint_schedule.
7. adaptive_long_horizon requires include_final: true.
8. fixed-fugacity staged workflow requires co2.mode fixed_fugacity.
9. plots.solver_dt requires solver_history enabled.
10. plots.reaction_rate requires postprocessing.reaction_rates true.
11. carbon_inventory output requires carbon_inventory postprocessing config.
12. element_budget output requires element_budget postprocessing config.
13. checkpoint files require solver.timestep.checkpoint_schedule.enabled.
14. restart.enabled true requires from_checkpoint and fails if restart is not implemented.
15. three-mineral development case validates.
16. output feature toggles suppress disabled files.
17. smart solver unavailable with fallback false fails clearly.
18. smart solver unavailable with fallback true records warning and uses standard backend.
19. long-horizon schedule generation includes initial and final times and is strictly increasing.
20. scheduled output controller shortens dt to land exactly on output time.
21. adaptive rejected step restores previous accepted state.
22. forced mineral depletion triggers configured action.
23. rate-based acceptance is disabled when reaction-rate extraction is not verified.
24. diagnostics include backend, workflow, redox apply_during, accepted/rejected counts, and termination reason.
```

---

## 21. Config Success Criteria

The config upgrade is successful if:

```text
1. Every optional feature can be enabled/disabled from YAML.
2. Unknown fields fail validation.
3. Invalid combinations fail validation.
4. Solver workflows are selectable from YAML.
5. CO₂ fixed-fugacity staging is explicit.
6. Redox/pE staging is explicit.
7. Timestep duration and control live under solver.timestep, not kinetics.
8. Fixed, adaptive, and adaptive_long_horizon schemas are separate and clear.
9. Output files and plots are controlled by outputs.
10. Scientific quantities are controlled by postprocessing.
11. Optional outputs require their required postprocessing config.
12. Checkpointing and restart are separate.
13. The three-mineral development case validates cleanly.
```

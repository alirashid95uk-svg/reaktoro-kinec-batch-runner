# Output Package Design — Final Codex Contract

## Runtime V1 Status

The active runner schema implements the base V1 output package, streamed
scheduled timeseries/solver-history staging, explicit accepted-state
checkpoints, machine-readable fixed/adaptive failure diagnostics, and optional
Objective 1 audit outputs for reaction rates, Kinec sign checks, carbon
inventory, element budgets, mineral-volume change, regime classification,
surface-area audit, workflow comparison, secondary-mineral assemblage,
surrogate-dataset export, validation ledger, and porosity/permeability
inference status. Adaptive and adaptive-long-horizon control are implemented;
automatic restart and smart solvers remain disabled.

## 1. Purpose

This file defines the simulation-output design for the Reaktoro batch runner.

It answers:

```text
What files should the simulator write?
What should each output file contain?
What should remain out of scientific result tables?
How should outputs support AI-assisted analysis, PhD interpretation, reproducibility, and long-horizon simulation review?
```

This file does **not** define solver algorithms. Solver workflows and timestep control are defined in:

```text
solver_workflow_and_long_horizon_timestep.md
```

The YAML options controlling this output design are defined in:

```text
config_schema_feature_options.md
```

---

## 2. Output Philosophy

### 2.1 Core Principle

```text
case YAML      = full editable source of truth
manifest.json = compact case card + input snapshot + traceability
CSV/plots     = simulation results and numerical behaviour
debug/        = validation and troubleshooting artifacts
checkpoints/  = long-horizon diagnostic/recovery state records
```

Outputs should show what the simulator actually produced.

They should not scatter repeated input configuration values across every result table.

### 2.2 Correct Use of Input Information

Input information is allowed in one place:

```text
manifest.json
```

This is intentional. The manifest should be self-contained enough that an external AI agent, examiner, collaborator, or future user can understand the simulated case without opening the original YAML.

Input information should **not** be repeated across:

```text
timeseries.csv
mineral_summary.csv
aqueous_summary.csv
solver_history.csv
plots
```

### 2.3 Runtime Baselines Are Allowed

Initial runtime values are not considered input duplication when they come from the constructed or conditioned Reaktoro state.

Allowed examples:

```text
initial pH
initial ionic strength
initial species amounts/molalities
initial mineral amounts
initial saturation indices
initial state after CO₂ equilibrium conditioning
```

These are runtime baselines, not raw YAML copies.

---

## 3. Recommended Output Folder Structure

For each run:

```text
outputs/
└── <case_name>/
    ├── manifest.json
    ├── diagnostics.json
    ├── timeseries.csv
    ├── mineral_summary.csv
    ├── aqueous_summary.csv
    ├── solver_history.csv
    ├── reaction_rates.csv                 # optional
    ├── carbon_inventory.csv               # optional
    ├── element_budget.csv                 # optional
    ├── plots/
    │   ├── pH_vs_time.png
    │   ├── mineral_change_vs_time.png
    │   ├── saturation_index_vs_time.png
    │   ├── species_molality_vs_time.png   # optional
    │   ├── reaction_rate_vs_time.png      # optional
    │   ├── solver_dt_vs_time.png          # optional
    │   └── solver_iterations_vs_time.png  # optional
    ├── debug/
    │   ├── mineral_connection.csv
    │   ├── resolved_config.yaml           # optional
    │   └── final_state.txt                # optional
    └── checkpoints/                       # optional for long-horizon runs
```

All files must be controlled from the YAML config.

No output file should be blindly written if its feature is disabled.

---

## 4. Required Base Outputs

The core output package should support these files:

```text
manifest.json
diagnostics.json
timeseries.csv
mineral_summary.csv
aqueous_summary.csv
solver_history.csv
plots/pH_vs_time.png
plots/mineral_change_vs_time.png
plots/saturation_index_vs_time.png
debug/mineral_connection.csv
```

These files should still be configurable. The code should not force all outputs if the user turns them off.

---

## 5. Optional Outputs

The output system should also support optional files when requested by config:

```text
reaction_rates.csv
carbon_inventory.csv
element_budget.csv
plots/species_molality_vs_time.png
plots/reaction_rate_vs_time.png
plots/solver_dt_vs_time.png
plots/solver_iterations_vs_time.png
debug/resolved_config.yaml
debug/final_state.txt
checkpoints/
```

If an optional output requires extra postprocessing configuration, validation must fail if that configuration is missing.

Examples:

```text
reaction_rates.csv
→ requires reaction_rates extraction to be enabled and verified

carbon_inventory.csv
→ requires carbon inventory postprocessing config

element_budget.csv
→ requires element budget postprocessing config

reaction_rate plot
→ requires reaction_rates.csv or equivalent verified rate data

solver_dt plot
→ requires solver_history enabled

solver_iterations plot
→ requires solver_history enabled
```

---

## 6. Deterministic CSV Column-Ordering Rule

CSV column order must be deterministic and stable across runs.

Recommended order:

```text
1. fixed core columns;
2. requested species in YAML order;
3. requested minerals in YAML order;
4. solver columns;
5. optional diagnostics;
6. optional conservation/budget columns.
```

Do not allow output column order to depend on Python dictionary iteration, set ordering, database species ordering, or filesystem ordering.

---

## 7. Zero-Initial-Value Handling

Percent-change calculations must explicitly handle zero initial values.

For mineral and aqueous summaries:

```text
if initial_amount_mol > 0:
    delta_percent = 100 * delta_mol / initial_amount_mol

if initial_amount_mol = 0 and final_amount_mol > 0:
    delta_percent = null
    interpretation = precipitation_from_zero or increase_from_zero

if initial_amount_mol = 0 and final_amount_mol = 0:
    delta_percent = null
    interpretation = unchanged_zero
```

For normalized mineral-change plots:

```text
if n0 > 0:
    plot 100 * (n(t) - n0) / n0

if n0 = 0:
    do not plot percent change;
    use absolute delta_mol or omit that mineral from the normalized plot with a warning.
```

Do not divide by zero.

---

## 8. `manifest.json`

### 8.1 Purpose

`manifest.json` is the self-contained case context file.

It should record enough input information, file hashes, software versions, selected solver settings, and output-file locations for an external AI agent or reviewer to understand what was simulated without opening the original YAML.

### 8.2 Required Manifest Groups

Recommended top-level groups:

```text
run_identity
traceability
input_snapshot
solver_configuration
output_configuration
software_environment
output_files
```

### 8.3 `run_identity`

Include:

```text
case_name
run_id, if available
run_started_at
run_finished_at
simulation_completed
```

### 8.4 `traceability`

Include:

```text
source_config_path
source_config_sha256
database_path
database_sha256
kinetic_yaml_path
kinetic_yaml_sha256
code_version or git commit, if available
```

### 8.5 `input_snapshot`

Include compact input context.

Recommended groups:

```text
model_scope
physical_conditions
CO₂ setup
redox setup
brine setup
mineral setup
kinetics setup
```

The mineral snapshot may include:

```text
mineral display name
thermodynamic name
kinetic name
role
initial amount
surface area, if kinetic
```

The brine snapshot may include:

```text
aqueous elements
specified species amounts
water amount
deterministic preprocessing summary, if used
```

Do not insert an uncontrolled raw YAML dump unless explicitly placed under debug.

### 8.6 Long-Horizon Metadata

For long-horizon runs, also include:

```text
year_definition_days
timestep_mode
output_schedule_mode
acceptance_thresholds
checkpointing_enabled
restart_enabled
backend_type
smart_backend_fallback_status
```

The active manifest records a `time_semantics` group containing the canonical
second, resolved duration, mode-specific timestep bounds, output-state rule,
resolved output schedule, independent checkpoint schedule, and disabled
restart configuration.
Explicit/logarithmic/hybrid schedules list their sorted unique timestamps in
seconds. The compatibility `every_internal_step` schedule is recorded as a
lazy fixed-grid rule plus its resolved count rather than expanded into a large
manifest array.

### 8.7 Rules

```text
manifest.json may include compact input context.
CSV files should not repeat the input snapshot.
Use hashes for reproducibility.
Keep manifest structure stable for AI parsing.
```

---

## 9. `diagnostics.json`

### 9.1 Purpose

Report run status, failure location, termination reason, solver backend, workflow mode, and high-level runtime facts.

### 9.2 Recommended Fields

```text
simulation_completed
failed_stage
error_message
termination_reason
final_time_reached_s
final_time_reached_days
final_time_reached_years
number_of_accepted_steps
number_of_rejected_steps
number_of_result_rows
requested_internal_steps
max_internal_steps
estimated_solver_calls
estimated_result_rows
partial_run
number_of_failed_steps
failed_attempt_target_time_s
failed_attempt_dt_s
accepted_state_restored
largest_dt_s
smallest_dt_s
average_dt_s
checkpoint_count
restart_enabled
restart_used
solver_backend_type
smart_backend_used
smart_backend_fallback_used
workflow_mode
co2_runtime_workflow
redox_enabled_runtime
redox_apply_during_runtime
warnings
```

If conservation diagnostics are enabled:

```text
element_balance_error
charge_balance_error
water_mass_change
carbon_balance_error
```

### 9.3 Rules

```text
Report runtime facts.
Do not repeat the full input snapshot.
Include failure stage and error message whenever possible.
Keep machine-readable JSON.
```

For an incomplete fixed or adaptive run, write diagnostics plus configured partial
timeseries and solver history from accepted states. Record the failed trial in
solver history with `accepted: false` and no advance in `time_end_s`. Do not
write scientific summaries, plots, validation ledgers, or surrogate datasets
from an incomplete trajectory.

---

## 10. `timeseries.csv`

### 10.1 Purpose

Store computed geochemical and numerical results through time.

### 10.2 Recommended Core Columns

```text
time_s
time_days
time_years, if long-horizon mode is used
stage
pH
ionic_strength_molal
alkalinity_eq_per_l, if available and reliable
solver_succeeded
solver_iterations
dt_s, if solver columns enabled
```

### 10.3 Selected Species Columns

For selected species:

```text
species_amount_mol::<species>
species_molality_mol_kgw::<species>
```

Molality is important for aqueous geochemical interpretation and later comparison against experimental or literature concentration data.

### 10.4 Mineral Columns

For configured/requested minerals:

```text
mineral_amount_mol::<mineral>
mineral_delta_mol::<mineral>
saturation_index::<mineral>
```

### 10.5 Saturation Index Definition

```text
SI = log10(saturation_ratio)
```

Interpretation:

```text
SI < 0  undersaturated
SI = 0  equilibrium
SI > 0  supersaturated
```

A near-equilibrium band may be used, but the tolerance must be explicit and configurable.

### 10.6 Rules

```text
Do not repeat constant input values.
Include time-zero state because it is the actual initialized state.
When `include_initial` is false, retain the initialized state internally for summaries but omit its timeseries row.
Write rows only at resolved output timestamps, not every internal or checkpoint step.
When `include_final` is false, the solver still reaches duration exactly but the final row is omitted.
Track only requested species, not every species automatically.
Keep units in column names.
Prefer molality plus amount for selected species.
Use deterministic column ordering.
```

---

## 11. `mineral_summary.csv`

### 11.1 Purpose

Summarize initial-to-final mineral changes and final saturation-state interpretation.

### 11.2 Recommended Columns

```text
mineral
initial_amount_mol
final_amount_mol
delta_mol
delta_percent
initial_SI
final_SI
final_saturation_state
net_change
```

### 11.3 Allowed `net_change` Values

```text
dissolution
precipitation
unchanged
precipitation_from_zero
unchanged_zero
```

### 11.4 Allowed `final_saturation_state` Values

```text
undersaturated
near_equilibrium
supersaturated
```

The near-equilibrium tolerance must be documented if used.

### 11.5 Rules

```text
Initial/final values must come from simulation state.
Do not include mineral surface area; it belongs in manifest input snapshot.
Do not include kinetic-source metadata; it belongs in debug/mineral_connection.csv.
Amount change alone is not enough; include saturation-state interpretation.
Handle zero initial amount explicitly.
```

---

## 12. `aqueous_summary.csv`

### 12.1 Purpose

Summarize initial-to-final changes in selected aqueous species.

### 12.2 Recommended Columns

```text
species
initial_amount_mol
final_amount_mol
delta_amount_mol
initial_molality_mol_kgw
final_molality_mol_kgw
delta_molality_mol_kgw
delta_percent
interpretation
```

### 12.3 Rules

```text
Include only requested species.
Initial/final values must come from simulation state.
Do not output every species automatically.
Add mg/L later only if density and molar-mass assumptions are explicit.
Handle zero initial amount explicitly.
```

---

## 13. `solver_history.csv`

### 13.1 Purpose

Record every solver attempt, accepted or rejected.

### 13.2 Recommended Columns

```text
step_index
attempt_index
time_start_s
time_end_s
dt_s
stage
accepted
solver_succeeded
iterations
wall_time_s
failure_reason
acceptance_reason
next_dt_s
delta_pH
max_delta_saturation_index
max_selected_species_fraction_change
max_mineral_fraction_change
minimum_species_amount_mol
max_element_balance_error_mol
max_element_balance_error_ratio
worst_element
trial_charge_mol
```

### 13.3 Rules

```text
Numerical information only.
Do not include chemistry variables here.
Support fixed, adaptive, and adaptive_long_horizon modes.
```

---

## 14. Rejected attempt view

### 14.1 Purpose

Rejected adaptive attempts are rows in `solver_history.csv` with
`accepted: false`; no duplicate `rejected_steps.csv` is written.

### 14.2 Recommended Columns

```text
attempt_index
time_start_s
dt_attempt_s
reason
solver_succeeded
delta_pH
max_delta_SI
max_mineral_fraction_change
max_selected_species_fraction_change
wall_time_s
next_dt_s
```

### 14.3 Rules

```text
Every rejected trial is written.
Rejected steps must not corrupt accepted state.
```

---

## 15. `reaction_rates.csv`

### 15.1 Purpose

Store diagnostic mineral reaction rates for accepted states.

This file supports reaction-rate plots and optional rate-based timestep acceptance.

### 15.2 Recommended Columns

```text
time_s
time_days
time_years
mineral
rate_mol_s
rate_mol_m2_s, if surface-normalized rate is available
saturation_index
surface_area_value
surface_area_unit
rate_evaluation_status
```

### 15.3 Rules

```text
Only write if postprocessing.reaction_rates: true.
Rates must be recomputed from the same Kinec formula or otherwise verified.
Do not rely only on mutable callback state.
Plots should be generated from reaction_rates.csv.
If rate extraction is unavailable, fail validation or disable rate outputs explicitly.
```

---

## 16. `carbon_inventory.csv`

### 16.1 Purpose

Track carbon distribution and balance when carbon inventory is explicitly requested.

### 16.2 Recommended Columns

```text
time_s
time_days
time_years
aqueous_carbon_mol
gas_carbon_mol
mineral_carbon_mol
total_carbon_mol
initial_total_carbon_mol
carbon_balance_error_mol
carbon_balance_error_percent
```

### 16.3 Rules

```text
Only write if postprocessing.carbon_inventory.enabled: true.
Requires explicit carbon species and carbon-bearing minerals.
Do not infer carbon-bearing minerals automatically unless explicitly configured.
Do not write this file by default.
```

---

## 17. `element_budget.csv`

### 17.1 Purpose

Track selected element budgets through time.

### 17.2 Recommended Columns

```text
time_s
time_days
time_years
element
aqueous_mol
mineral_mol
gas_mol
total_mol
initial_total_mol
delta_mol
relative_error_percent
```

### 17.3 Rules

```text
Only write if postprocessing.element_budget.enabled: true.
Requires explicit selected elements.
Do not compute every database element automatically.
Do not write this file by default.
```

---

## 18. `debug/mineral_connection.csv`

### 18.1 Purpose

Record runtime validation of mineral naming and kinetic attachment.

This file supports debugging of explicit aliases, thermodynamic minerals, Kinec YAML records, and surface-area availability.

### 18.2 Recommended Columns

```text
mineral_name
thermo_name
kinetic_name
role
thermodynamic_mineral_found
kinec_yaml_record_found
surface_area_present
status
reason
```

### 18.3 Rules

```text
Debug/validation output, not a scientific result table.
Identify missing thermodynamic phases, missing Kinec records, missing surface areas.
Preserve explicit alias information.
Use only supported mineral roles.
```

---

## 19. Plots

### 19.1 Required First-Version Plots

```text
pH_vs_time.png
mineral_change_vs_time.png
saturation_index_vs_time.png
```

### 19.2 Optional Plots

```text
species_molality_vs_time.png
reaction_rate_vs_time.png
solver_dt_vs_time.png
solver_iterations_vs_time.png
```

### 19.3 Plot Definitions

#### `pH_vs_time.png`

```text
x-axis: time
y-axis: pH
```

Use days or years depending on simulation duration.

#### `mineral_change_vs_time.png`

Plot normalized mineral change:

```text
100 * (n(t) - n0) / n0
```

If `n0 = 0`, do not plot percent change. Use absolute `delta_mol` or omit that mineral from the normalized plot with a warning.

#### `saturation_index_vs_time.png`

Plot saturation index for each requested mineral.

Include a reference line:

```text
SI = 0
```

#### `solver_dt_vs_time.png`

Requires solver history.

#### `solver_iterations_vs_time.png`

Requires solver history.

#### `reaction_rate_vs_time.png`

Requires `reaction_rates.csv`.

### 19.4 Rules

```text
Do not generate all plots automatically.
Keep one plot per concept.
Plots should be reproducible from CSV files.
```

---

## 20. Checkpoints and Restart Distinction

### 20.1 Checkpointing

Checkpointing means:

```text
save readable intermediate state for diagnostics, evidence, and possible manual recovery.
```

Suggested path:

```text
outputs/<case_name>/checkpoints/
```

Restart-ready long-horizon checkpoint target:

```text
checkpoint metadata
current time
current timestep
latest accepted timeseries row
readable ChemicalState export
solver-controller state
```

The active fixed/adaptive implementation writes `checkpoints/index.jsonl` and one
readable `checkpoint_<index>_state.txt` per configured accepted checkpoint.
The index records checkpoint index, absolute `time_s`, preceding accepted
`dt_s`, and state filename. Checkpoint times do not create timeseries rows
unless they also occur in the output schedule. It is a diagnostic checkpoint,
not yet the full restart-ready controller-state package listed above.

### 20.2 Restart

Restart means:

```text
automatically resume simulation from a checkpoint.
```

First implementation:

```text
checkpointing required for long-horizon mode when enabled;
automatic restart optional and disabled by default.
```

Suggested config placeholder:

```yaml
solver:
  restart:
    enabled: false
    from_checkpoint: null
```

Validation:

```text
restart.enabled: true
→ requires from_checkpoint
→ fail clearly if restart support is not implemented
```

### 20.3 Rules

```text
Do not imply automatic restart support just because checkpoints exist.
Checkpoint format must be documented.
Readable checkpoints are acceptable for first implementation.
Binary restart is optional only if straightforward and tested.
```

---

## 21. Output Configuration

Output files must be controlled from YAML.

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

  checkpoints:
    enabled: true
```

---

## 22. Output Validation Rules

```text
plots.enabled: false
→ no plot files written

plots.enabled: true
→ at least one plot flag must be true

plots.solver_dt: true
→ solver_history.enabled must be true

plots.solver_iterations: true
→ solver_history.enabled must be true

plots.reaction_rate: true
→ postprocessing.reaction_rates must be true
→ reaction_rates.csv must be available

summaries.carbon_inventory: true
→ carbon inventory postprocessing must be enabled and configured

summaries.element_budget: true
→ element budget postprocessing must be enabled and configured

outputs.checkpoints.enabled: true
→ solver.timestep.checkpoint_schedule.enabled must be true for long-horizon checkpoint files
```

---

## 23. Success Criteria

The output package is successful if:

```text
1. Output files can be enabled/disabled from YAML.
2. manifest.json is AI-agent-ready and includes compact input context.
3. CSV files report runtime results, not repeated input tables.
4. timeseries.csv includes pH, ionic strength, selected species amounts/molalities, mineral amounts, and saturation indices when requested.
5. mineral_summary.csv reports initial/final mineral change and saturation-state interpretation.
6. aqueous_summary.csv reports initial/final amount and molality changes for selected species.
7. solver_history.csv contains accepted and rejected attempts for numerical-method review.
8. reaction_rates.csv, carbon_inventory.csv, and element_budget.csv are defined and only written when configured.
9. plots are controlled by config and reproducible from CSV outputs.
10. debug outputs are separated from scientific result tables.
11. Checkpoints are available for long-horizon runs when enabled.
12. CSV column order is deterministic.
13. Zero initial values are handled without divide-by-zero.
```

# Output Package Design — Active Contract

## Runtime Status

The active runner always writes the base output package and automatically adds
the standard table for each enabled postprocessing analysis. Fixed and legacy
adaptive execution retain
the original solver-history columns. The explicit Richardson mode adds its
branch/controller fields to the same `solver_history.csv` artifact.

Only implemented output fields belong in this document. Smart-solver and
restart fields are not active output requirements.

The active output contract remains `objective1_audit_v4`. Removing the
obsolete optional `validation_ledger.csv` capability does not change required
package artifacts or scientific table schemas, so no schema-version bump is
needed. Downstream validation artifacts remain outside the package.

## 1. Purpose

This document defines what the simulator writes, how those artifacts are
interpreted, and which information belongs in manifest/diagnostic metadata
rather than scientific result tables.

Solver behaviour is defined in `solver_workflow_and_long_horizon_timestep.md`.
YAML configuration is defined in `config_schema_feature_options.md`.

## 2. Output Principles

```text
case YAML      = editable source of truth
manifest.json  = compact traceability and configuration context
diagnostics.json = lifecycle/runtime status
CSV/plots      = numerical/scientific results and solver behaviour
debug/         = troubleshooting artifacts
checkpoints/   = accepted intermediate state records
```

Rules:

- outputs must describe what the simulator actually produced;
- scientific inputs should not be repeated across every result table;
- accepted-state runtime baselines may appear in result tables;
- output column order must be deterministic;
- disabled postprocessing analyses, plots, and debug artifacts must not be written;
- no result should imply transport, calibration, restart, or unsupported solver
  behaviour that did not occur.

## 3. Base Output Package

The base package supports:

```text
manifest.json
diagnostics.json
simulation.log
timeseries.csv
solver_history.csv
```

Requested species and minerals automatically add their standard timeseries
columns plus `aqueous_summary.csv` and `mineral_summary.csv`, respectively.
There is no second table-output switch.

Selected debug files include:

```text
debug/mineral_connection.csv
debug/resolved_config.yaml
debug/final_state.txt
```

Accepted-state checkpoints are written under `checkpoints/` only when the
configured checkpoint schedule requires them.

`simulation.log` is the concise chronological human-run record. It contains
stage changes, retry/recovery warnings, configured accepted-result milestones,
checkpoint/output status, and final status. Per-attempt numerical detail remains
in `solver_history.csv`; the log does not record each successful solver call.
The runner also writes this file in JSONL machine mode, but never mixes its text
into machine stdout.

## 4. Optional Objective 1 Outputs

The current writer emits the corresponding table when an analysis is explicitly
enabled and configured under `postprocessing`:

```text
reaction_rates.csv
reaction_rate_validation.csv
carbon_inventory.csv
element_budget.csv
mineral_volume_change.csv
regime_classification.csv
surface_area_audit.csv
workflow_comparison.csv
secondary_mineral_assemblage.csv
surrogate_dataset.csv
porosity_permeability.csv
```

These files are reporting/postprocessing products. They do not add solver
physics.

Existing carbon and element budgets are reconstructed from explicitly
configured species/mineral/gas mappings. They must not be described as an
authoritative full-state material-conservation proof.

## 5. Deterministic Tables

Column order must remain stable. Use:

```text
1. fixed core columns
2. requested species in YAML order
3. requested minerals in YAML order
4. solver columns
5. optional diagnostics
6. optional budget/conservation columns
```

Do not let set ordering, database species ordering, or filesystem ordering
change CSV schemas.

Percent-change calculations must handle zero initial values explicitly. Do not
divide by zero or invent a percentage for precipitation from zero.

## 6. `manifest.json`

The manifest is the compact, self-contained case context and traceability file.

Required logical groups include:

```text
output_schema_version
run_identity
traceability
input_snapshot
solver_configuration
time_semantics
output_configuration
software_environment
output_files
```

The source YAML no longer exposes an `outputs` block. For compatibility with
the `objective1_audit_v4` package auditor, `output_configuration` remains a
derived record of the artifacts actually selected or produced; it is not an
accepted source-configuration registry.

### 6.1 Traceability

Record, where available:

```text
source_config_path
source_config_sha256
database path/name
database_sha256
kinetic_model
kinetic_parameter_path
kinetic_parameter_sha256
Python version
Reaktoro version
platform
```

### 6.2 Solver Configuration

The active manifest records:

```text
backend_type = standard
workflow
timestep configuration
redox_apply_during
```

There is no configurable smart backend.

### 6.3 Time Semantics

Record canonical seconds, resolved duration, mode-specific timestep bounds,
resolved output/checkpoint schedules, accepted-state output rule, and internal
step metadata where applicable.

For schema compatibility, `time_semantics.restart` remains a fixed capability
marker:

```json
{"enabled": false, "from_checkpoint": null}
```

It is **not** sourced from case YAML and does not expose restart functionality.
Removing or changing that manifest shape requires an explicit output-schema
version change.

## 7. `diagnostics.json`

Diagnostics report lifecycle and solver status. Current fields include the
runtime case identity/provenance plus facts such as:

```text
simulation_completed
failed_stage
exception_type
error_message
termination_reason
final_time_reached_s
final_time_reached_days
number_of_accepted_steps
number_of_rejected_steps
number_of_failed_steps
number_of_internal_attempts
number_of_solver_failed_attempts
number_of_reaktoro_solve_calls
number_of_temporal_error_rejections
number_of_event_localizations
requested_internal_steps
base_internal_steps
max_internal_steps
minimum_possible_accepted_steps
estimated_solver_calls
estimated_result_rows
requested_output_rows
requested_checkpoint_count
checkpoint_count
smallest_dt_s
largest_dt_s
average_dt_s
failed_attempt_target_time_s
failed_attempt_dt_s
accepted_state_restored
partial_run
partial_outputs_written
scientific_outputs_omitted
output_completeness
solver_backend_type
workflow_mode
timestep_mode
co2_runtime_workflow
redox_enabled_runtime
redox_apply_during_runtime
warnings
```

`smart_backend_used`, `smart_backend_fallback_used`, `restart_enabled`, and
`restart_used` are not active diagnostic requirements.

## 8. `timeseries.csv`

Timeseries rows represent accepted Reaktoro states only.

Rules:

- never interpolate chemical states;
- fixed mode writes according to its configured output schedule;
- adaptive modes shorten attempted steps as needed to land exactly on forced
  output times;
- failed adaptive attempts never become chemistry rows;
- output times are canonical seconds after resolution.

The terminal monitor reads these accepted rows. It does not inspect failed trial
states or calculate additional chemistry.

Requested species and minerals automatically add their amount, molality,
change, and saturation-index columns in stable YAML order. Solver status,
iteration, and timestep columns are standard.

## 9. `solver_history.csv`

`solver_history.csv` is the numerical-control audit trail. It records every
Reaktoro solver attempt required by the active controller, including failed
adaptive attempts and failed fixed attempts.

At minimum the record must preserve enough information to reconstruct:

```text
stage
attempt start time
attempt target/end time
attempt dt
solver success
accepted/failed status
iterations when available
failure reason when available
```

For `mode: adaptive_error_controlled`, the mode-specific extension also records:

```text
accepted time before/after the outer trial
controller-proposed and effective h
full, first-half, and second-half success/iterations/wall time
actual Reaktoro calls in the outer record
Richardson E, worst controlled mineral, raw molar error, and molar scale
rejection reason and separate solver_failure/temporal_error_rejection flags
event cap or detection type and its predicted/detected target time
retry count, next h, solver reconstruction, and controller-history reset
```

One composite outer-trial row contains the subsolve evidence. The two half-step
calls are not emitted as accepted physical timesteps. The time-zero state is
emitted through the same boundary path as the other timestep modes.
For `adaptive_error_controlled`, enabled time-zero reaction rates are extracted
on a disposable identically configured system so observation cannot change the
system-level rate cache used by the kinetic branches.

Failed adaptive attempts keep accepted time unchanged. A solver history record
is numerical evidence; it is not scientific validation.

## 10. Checkpoints

Checkpoint files are written only after an accepted state reaches a configured
checkpoint time.

Checkpointing provides diagnostics/evidence and possible manual inspection. It
does not provide resumable execution. There is no active `solver.restart` YAML
block.

## 11. Reaction-Rate Outputs

When reaction-rate diagnostics are enabled, use Reaktoro accepted-state runtime
properties and the live total surface area. Do not independently recompute the
kinetic equations for routine diagnostics.

The custom Kinec and native Palandri-Kharaka paths must preserve their verified
rate/sign/unit semantics.

## 12. Carbon and Element Budgets

Current budget tables are configured inventories/reconstructions. Report their
changes and errors honestly, but do not invent pass/fail tolerances.

They are not authoritative whole-state component, material, or charge-balance
checks and must not be used as scientific acceptance criteria on that basis.

## 13. Post-Simulation Validation

When configured, a trusted validation script runs in a separate process only
after the simulation and authoritative output package have completed. The
runner passes the resolved package directory as `--results-dir`.

Derived validation artifacts belong in the timestamped run's sibling
`validation/` directory, outside `results/`. They are not listed in or allowed
to modify the simulation manifest. Validation success/failure is reported
separately and cannot change Reaktoro simulation status or package
completeness.

Machine output uses the existing `stage_started`/`stage_completed` events with
`stage: post_simulation_validation` and a separate
`completed`/`failed`/`skipped` status. A failed hook does not emit a worker or
solver failure event, and the runner still exits according to simulation and
package completion.

## 14. Porosity, Permeability, and Capillary Fields

Where required source-supported inputs/update laws are absent, these outputs
must report `not_evaluated`. Batch mineral changes alone must not be converted
into transport or sealing claims.

## 15. Output Completeness

A clean output package establishes traceability and package coherence only. It
does not prove:

```text
calibration
experimental agreement
timestep convergence
full material conservation
reactive transport
fracture sealing
```

Those claims require their own explicit evidence.

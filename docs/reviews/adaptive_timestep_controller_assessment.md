# Adaptive Timestep Controller Assessment

## Scope and implementation

The runner now supports `fixed`, `adaptive`, and `adaptive_long_horizon`
timestep modes through strict, discriminated YAML schemas. The adaptive modes
use the standard Reaktoro `KineticsSolver`; no Reaktoro internals, scientific
settings, backend layer, or adaptive-solver dependency were added. Fixed mode
retains its deterministic absolute-grid implementation and is the regression
baseline.

Each adaptive trial follows one explicit loop:

1. Copy the last accepted `ChemicalState`.
2. Cap the proposed absolute target at the next output, checkpoint, or final
   timestamp and solve only that interval.
3. Require both Reaktoro solver success and every enabled acceptance check.
4. On acceptance, advance time, emit the attempt, write any due output or
   checkpoint, and grow the controller timestep within `dt_max`.
5. On rejection, restore the copy with `state.assign`, emit the failed attempt
   without advancing accepted time, shrink the timestep, and retry.

`max_internal_steps`, `max_retries_per_step`, and rejection at `dt_min` produce
explicit termination reasons in `diagnostics.json`. An event-shortened step may
be smaller than `dt_min` so that an accepted state can land exactly on an event;
if that trial is rejected, the minimum-timestep guard stops the run. All time
conversion remains Decimal-based and canonical runtime time is seconds.

`adaptive_long_horizon` intentionally reuses the same controller. Its only
additional policy is sparse scheduled output with final-time inclusion and an
enabled explicit checkpoint schedule. This avoids a second controller and
prevents output-row count from following internal attempt count.

## Reaktoro state evidence

Direct probes used Reaktoro 2.13.0 in `fypr-reaktoro`:

- `ChemicalState(state)` created an independent copy, and
  `state.assign(snapshot)` restored species amounts, temperature, and pressure.
- Reconstruction into a new state using the same system, temperature,
  pressure, and `setSpeciesAmounts` reproduced species and Reaktoro
  element/component totals exactly in the controlled case. Reconstruction is
  retained as evidence, not added to the runtime path because native copy and
  assign are simpler and verified.
- A constant-rate Calcite probe gave `0.99999998999999 mol` after one 1 s step
  and `0.9999999899999901 mol` after two 0.5 s steps. The maximum observed
  element-total difference was `1.42e-14 mol`. This is a controlled API and
  rollback check, not a timestep-convergence claim for Kinec chemistry.

## Acceptance checks

The YAML can enable these trial-state checks:

- finite species amounts, temperature, pressure, pH, saturation indices,
  Reaktoro element/component totals, and charge diagnostic;
- strictly negative species amounts;
- absolute pH change;
- maximum absolute saturation-index change across configured minerals;
- symmetric fractional change in selected output species and all configured
  minerals, `|after-before| / max(|before|, |after|)`;
- closed-system Reaktoro element/component conservation with configurable
  absolute and relative tolerances.

Element conservation is rejected for fixed-fugacity kinetic-step workflows,
where external exchange invalidates a closed-total test. Charge is recorded as
a diagnostic but is not an acceptance criterion. Rate-based acceptance is
explicitly rejected when non-null because the runtime reaction-rate quantity
has not been verified as a stable before/after controller measure.

## Outputs, failure behaviour, and compatibility

`solver_history.csv` has one deterministic schema for every accepted and
rejected attempt, including the attempted `dt`, unchanged accepted timestamp
on rejection, next proposed `dt`, failure/acceptance reason, and measured
criteria. `diagnostics.json` records attempt counts, solver-failure counts,
reason counts, accepted final time, restoration status, termination reason,
and partial-run status. Incomplete runs retain accepted timeseries rows and
attempt history but omit scientific summaries and plots.

Checkpoints are written only after acceptance and before any possible future
restart operation. They contain a readable Reaktoro state and JSONL index.
Automatic restart remains disabled: `restart.enabled: true` fails validation
because the current checkpoint does not yet contain a demonstrated,
round-trippable controller and workflow reconstruction package.

Existing valid fixed YAML remains valid. Adaptive modes require explicit step
bounds, growth/shrink factors, retry limit, acceptance block, output schedule,
and maximum attempt count. Year units still require an explicit
`year_definition_days`.

## Verification

Commands executed on 30 July 2026:

```powershell
conda run -n fypr-reaktoro python -m pytest -q tests/test_adaptive_timestep_controller.py
# 11 passed in 1.18s

conda run -n fypr-reaktoro python -m pytest -q tests/test_adaptive_timestep_controller.py tests/test_fixed_timestep_controller.py tests/test_fixed_fugacity_workflows.py tests/test_first_version.py
# 63 passed in 5.16s

conda run -n fypr-reaktoro python -m pytest -q
# 63 passed in 5.00s

conda run -n fypr-reaktoro python .agents/skills/objective1-output-auditor/scripts/audit_output_package.py --self-test
# self_test: passed

conda run -n fypr-reaktoro python -m compileall -q batch_runner runner.py
git diff --check
# passed with no errors
```

The tests cover forced rejection and rollback, unchanged accepted time and
state, exact output/checkpoint/final landing, retry/minimum/maximum-attempt
termination, long-horizon schema constraints, every implemented acceptance
criterion, rejected restart and rate criteria, real Reaktoro state copy and
reconstruction, one-step versus substeps, and a controlled Forward Euler
refinement trend. The refinement errors for 1, 2, and 4 steps decrease from
`0.367879` to `0.117879` to `0.051473`.

## Remaining limitations and scientific interpretation

- Growth after an accepted trial is geometric, not an embedded error estimate,
  PI controller, or calibrated stiffness detector. Acceptance thresholds need
  case-specific scientific justification and convergence studies.
- No automatic restart, rollback across process failure, timeout interruption,
  adaptive rate criterion, mineral-depletion action, or state interpolation is
  implemented.
- Readable checkpoints support audit and manual recovery investigation but are
  not claimed restart-ready.
- Passing software and controlled numerical tests does not establish Kinec
  parameter validity, kinetic timestep convergence, calibration, conservation
  under every workflow, or scientific accuracy of a long-horizon batch result.
  Batch trajectories must not be interpreted as reactive transport or fracture
  sealing.

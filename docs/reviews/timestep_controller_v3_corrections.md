# Timestep Controller V3 Corrections

> Historical review: this records the former v3 output contract. The active
> contract is `objective1_audit_v4`.

## Implemented decisions

Seven controller/output defects were corrected without changing Reaktoro,
kinetic parameters, thermodynamic inputs, fixed-grid construction, or the
disabled restart/rate-acceptance policies.

1. Species and mineral change checks now have separate YAML blocks containing
   `absolute_tolerance_mol`, `relative_tolerance`, and
   `reference_floor_mol`. A trial passes each amount check when
   `|trial - accepted| <= absolute + relative * max(|accepted|, floor)`.
   Referencing the accepted state plus an explicit positive floor permits
   physically meaningful zero-to-positive appearance while retaining a finite,
   user-controlled limit near zero.
2. Adaptive preflight partitions the duration at every forced output and
   checkpoint target and sums `ceil(interval / dt_max)`. A configuration is
   rejected when this minimum possible accepted-step count exceeds
   `max_internal_steps`. Retries still consume the runtime attempt limit, so
   passing preflight is necessary but not sufficient for completion.
3. Rollback continues to use `ChemicalState(state)` and
   `state.assign(snapshot)`. A Reaktoro 2.13.0 constant-rate Calcite test first
   executed and rejected a real 1 s solve, restored the state, and retried 0.5
   s. The reused solver and a newly constructed solver produced exactly equal
   species amounts and element totals with equal retry iteration counts. Solver
   reconstruction was therefore not added.
4. `every_internal_step` now means every actual accepted solver step for fixed
   and adaptive modes, including steps introduced by checkpoint/target
   splitting. The row estimate and manifest description use the same meaning.
5. Negative-amount acceptance now uses optional
   `negative_amount_tolerance_mol`. Values in `[-tolerance, 0)` are retained
   unchanged and recorded by count and most-negative value; values below
   `-tolerance` reject the trial. No amount is clamped.
6. Structured failure results now cover `database_loading`, `kinetics_loading`,
   `mapping`, `system_construction`, `state_construction`, `solver_execution`,
   and `output_writing`. Diagnostics preserve exception type/message, last
   accepted time, attempt counts, and an `output_completeness` list. The CLI
   still exits unsuccessfully after writing the available partial package.
7. The output contract at the time was `objective1_audit_v3`. Solver-history columns,
   manifest time metadata, diagnostics completeness, tests, and the package
   auditor were updated together. The auditor rejects version 2 packages
   rather than silently interpreting their older fields.

## Compatibility

Fixed simulations without checkpoint splits retain the same deterministic
timestamps and chemistry. A fixed `every_internal_step` case with a checkpoint
split now intentionally emits the additional accepted state. Adaptive YAML
must replace the removed boolean/fraction fields with
`negative_amount_tolerance_mol`, `selected_species_change`, and
`mineral_change` as applicable. No scientific tolerance is supplied by
default; values must be justified for each case or benchmark.

Version 2 output readers must explicitly migrate to the version 3 manifest,
diagnostics, and solver-history fields. Existing version 2 output directories
remain untouched but fail the current auditor compatibility check.

## Verification evidence

The focused tests cover zero-to-positive species/mineral appearance, rejection
above the combined tolerance, tolerated and rejected negative noise without
clamping, impossible `dt_max` preflights with and without forced intervals,
real Reaktoro rollback/retry solver equivalence, checkpoint-split every-step
output, every named setup/solver/output failure stage, accepted-time retention,
output completeness, and version 2 rejection.

```powershell
conda run -n fypr-reaktoro python -m pytest -q
# 76 passed in 7.27s

conda run -n fypr-reaktoro python -m compileall -q batch_runner runner.py tests
# passed

conda run -n fypr-reaktoro python .agents/skills/objective1-output-auditor/scripts/audit_output_package.py --self-test
# self_test: passed

git diff --check
git diff --exit-code -- data/thermo/Kinec_v3_4.dat data/kinetics/kinec_rates_minimal.yaml batch_runner/Kinect_Custom_Rates.py
# passed; protected scientific files unchanged
```

## Remaining limitations

- The amount tolerances and reference floors are numerical/scientific inputs,
  not calibrated defaults. Convergence and sensitivity evidence is still
  required for each production case.
- The rollback equivalence test is deliberately small and closed-system. It
  supports the exact Reaktoro API path used here but does not prove identical
  hidden-solver behaviour for every constrained or highly stiff kinetic model.
- Lifecycle diagnostics cannot be persisted if the output location itself is
  unwritable or the storage device fails while writing the fallback diagnostic.
- Automatic restart, rate-based acceptance, adaptive error estimation,
  transport, and fracture evolution remain outside scope.
- Passing software tests does not establish kinetic calibration, timestep
  convergence for a scientific case, conservation under every workflow, or
  long-horizon geochemical validity.

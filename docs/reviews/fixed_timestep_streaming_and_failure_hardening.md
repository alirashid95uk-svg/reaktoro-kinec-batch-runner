# Fixed-Timestep Streaming and Failure Hardening

## Outcome

The fixed-step V1 runner now bounds requested work before Reaktoro construction,
generates timesteps lazily, streams full result rows and solver records to disk,
and restores the last accepted `ChemicalState` after a failed trial. Existing
scientific settings, Reaktoro solver calls, CSV column order, and successful
workflow outputs are unchanged.

## Design decisions

- `solver.timestep.max_internal_steps` is a positive, user-editable preflight
  limit with a documented default of `100000`. The resolved config and
  diagnostics report the requested step count and estimated rows/solver calls.
- Duration, `dt`, year length, and their resolved seconds must be finite.
  Oversized or non-finite cases fail before database/system construction.
- `fixed_steps_s()` is an iterator. Absolute target timestamps retain the
  corrected final-time behaviour without allocating a schedule container.
- Accepted rows and solver records are written one at a time to temporary JSONL
  spools. Final CSV and audit products consume repeatable iterators over those
  files; no complete trajectory of Python dictionaries is retained.
- Before each kinetic trial, `ChemicalState(state)` snapshots the accepted
  state. If Reaktoro fails or raises, `state.assign(snapshot)` restores it,
  accepted time remains unchanged, and a solver-history row is emitted with
  `accepted: false` and `time_end_s` equal to the accepted start time.
- Failed runs return normally to the output layer so configured partial
  timeseries, solver history, diagnostics, accepted final state, and manifest
  can be written. Scientific summaries, plots, validation ledgers, and
  surrogate exports are suppressed; the CLI then exits nonzero.

## Compatibility

- Existing YAML remains valid because `max_internal_steps` has a default.
  Cases requesting more than `100000` fixed steps must explicitly raise the
  limit after reviewing runtime and storage cost.
- Successful CSV schemas and column ordering are unchanged. Diagnostics gain
  work-estimate, partial-run, failed-attempt, and restoration fields.
- Runtime `SimulationResult` objects no longer expose complete in-memory
  `rows` or `solver_history` lists. Internal consumers use `iter_rows()` and
  `iter_solver_history()` before the temporary spools are removed by
  `write_outputs()`.
- Incomplete output packages intentionally omit completion-dependent products
  and must not be treated as valid surrogate or scientific-result packages.

## Verification

Environment: Reaktoro `2.13.0` in `fypr-reaktoro`.

| Check | Result |
|---|---|
| Focused fixed-step controller suite | `16 passed in 1.43s` |
| Large-schedule, preflight, real-state restoration, and partial-output subset | `4 passed, 12 deselected in 1.05s` |
| General Reaktoro reaction-rate contract | `1 passed, 20 deselected in 1.13s` |
| Complete project suite | `42 passed in 3.70s` |
| Objective 1 output-auditor self-test | `passed` |

The tests include a lazily sampled one-billion-step schedule, default preflight
rejection, NaN/infinity and conversion-overflow rejection, failed-state
restoration, unchanged accepted time, streamed partial rows, failure
diagnostics, and omission of completion-dependent summaries.

## Remaining risks

- The preflight limit bounds step count, not Reaktoro wall time or disk usage.
- Plotting still allocates the selected numeric series required by Matplotlib,
  although it no longer retains full chemistry-row dictionaries.
- Temporary spools scale with output size and may remain after process
  termination or an output-writing exception; a fresh output directory remains
  required for reruns.
- Failure diagnostics are written only when the configured diagnostics output
  is enabled. Setup failures before stream initialization still raise directly.
- Fixed-step failures stop immediately. Adaptive retry, timestep reduction,
  restart-ready recovery, and hard interruption of blocking C++ calls remain
  out of V1 scope. Diagnostic fixed-step checkpoints were added in the later
  output/checkpoint scheduling correction.
- Software tests do not establish timestep convergence, mass conservation,
  calibration, reactive-transport behaviour, or fracture sealing.

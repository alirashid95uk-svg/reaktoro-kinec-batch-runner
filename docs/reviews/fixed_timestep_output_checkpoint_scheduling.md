# Fixed-Timestep Output and Checkpoint Scheduling

## Outcome

The fixed-step controller now treats three timelines independently:

- the absolute fixed solver grid defined by `step_size.dt`;
- requested timeseries timestamps defined by `output_schedule`;
- diagnostic accepted-state timestamps defined by `checkpoint_schedule`.

Output or checkpoint targets split a fixed-grid interval when necessary. The
next solve uses the exact difference between the accepted time and the next
absolute target. This does not reset the fixed grid, does not interpolate a
chemical state, and does not introduce adaptive acceptance or timestep growth.

## Configuration and numerical rules

`solver.timestep.output_schedule` supports `explicit`, `logarithmic`, and
`hybrid`. `include_initial` and `include_final` add the two boundary outputs.
The documented `every_internal_step` default preserves existing YAML and is
generated lazily rather than expanded in memory.

All configured values use the existing Decimal-based conversion and the same
`year_definition_days`. Explicit and logarithmic endpoints must lie within the
configured duration. Logarithmic points are generated from
`start * 10^(k / points_per_decade)` through `end`, with the configured end
forced into the schedule. Runtime timestamps are then converted to the public
floating-point seconds boundary, sorted, and de-duplicated. The final solver
target remains exactly `duration_s`, independently of whether a final output
row is requested.

The preflight `max_internal_steps` limit applies to the fixed-grid steps plus
any additional schedule splits. Oversized logarithmic schedules are rejected
from their point-count estimate before timestamp generation. Diagnostics distinguish base internal steps,
resolved internal steps, requested output rows, actual output rows, requested
checkpoints, and written checkpoints.

Enabled checkpoints write `checkpoints/index.jsonl` and one readable Reaktoro
state file per accepted checkpoint. Checkpoint times do not create timeseries
rows unless the same timestamp is also requested by the output schedule.

## Manifest and compatibility

`manifest.json` now contains `time_semantics`, including canonical seconds,
duration, configured fixed `dt`, base and resolved internal-step counts, the
accepted-state/no-interpolation rule, and resolved output and checkpoint
schedules. Explicit/logarithmic/hybrid timestamps are listed. The compatibility
schedule is represented by its lazy fixed-grid rule and resolved count so a
large default schedule is not materialised solely for the manifest.

CSV column order and existing case YAML remain unchanged. Existing cases retain
their previous initial-plus-every-fixed-step rows unless they opt into a sparse
schedule. Downstream trajectory reports and plots consume only scheduled rows;
initial and final states are retained separately for endpoint summaries even
when their timeseries rows are disabled.

## Verification

Verified with Reaktoro 2.13.0 in `fypr-reaktoro`:

```text
conda run -n fypr-reaktoro python -m pytest -q tests/test_fixed_timestep_controller.py
26 passed in 2.18s

conda run -n fypr-reaktoro python -m pytest -q tests/test_first_version.py -k general_reaction_rate_contract
1 passed, 20 deselected in 1.17s

conda run -n fypr-reaktoro python .agents/skills/objective1-output-auditor/scripts/audit_output_package.py --self-test
self_test passed

conda run -n fypr-reaktoro python -m pytest -q
52 passed in 5.06s

conda run -n fypr-reaktoro python -m compileall -q batch_runner tests
passed

git diff --check
passed; line-ending conversion warnings only

git diff --exit-code -- data/thermo/Kinec_v3_4.dat data/kinetics/kinec_rates_minimal.yaml batch_runner/Kinect_Custom_Rates.py
passed; protected scientific files unchanged
```

## Remaining limits

- Timestepping remains fixed; there is no adaptive controller, rejected-step
  retry, or timestep-convergence evidence.
- Checkpoints are readable diagnostic snapshots, not restart-ready packages;
  automatic restart and controller-state restoration remain unimplemented.
- Logarithmic intermediate timestamps cross the public float boundary after
  deterministic Decimal generation. Exact landing means equality with those
  resolved floating-point seconds.
- Passing tests establish software timing and package behaviour only. They do
  not establish kinetic calibration, conservation, geochemical accuracy, or
  long-horizon scientific validity.

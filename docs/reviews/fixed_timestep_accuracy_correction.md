# Fixed-Timestep Accuracy Correction

## Outcome

The V1 fixed-timestep controller now converts configured time values once,
uses the same explicit year definition for duration and timestep, derives the
final remainder with decimal arithmetic, and records absolute target times only
after Reaktoro reports solver success. No scientific case values, kinetic
parameters, thermodynamic inputs, Reaktoro calls, or output-table schemas were
changed.

Follow-up memory, failure-diagnostic, and accepted-state restoration changes
are documented in `fixed_timestep_streaming_and_failure_hardening.md`.

## Corrected defects

| Defect | Correction |
|---|---|
| A year-based `dt` was reconverted without the configured `year_definition_days`, silently reverting to 365.25 days. | Duration and `dt` now pass through one Decimal-based conversion during case resolution and the resolved `dt_s` is reused. |
| `year_definition_days` was tied only to the duration unit. | It is required when either duration or `dt` uses `year(s)`, including mixed-unit cases, and rejected when neither does. |
| Repeated `time_s += dt_s` accumulated binary floating-point error. | Each step carries an absolute target computed from its integer index; the final target is set directly to the resolved duration. |
| Simulation time advanced before solver success was checked. | The solve result is checked first; only an accepted step updates time and emits history/timeseries records. |
| Final-step behaviour depended on floating-point division and accumulation. | Decimal `divmod` produces the full-step count and shortened remainder, including `dt > duration`. |

## Numerical reasoning

YAML numeric values are parsed as Python floats, then converted with
`Decimal(str(value))`. Unit factors and `year_definition_days` are applied in
Decimal arithmetic. Reaktoro still receives seconds as `float`, matching its
Python interface, but schedule construction does not repeatedly add those
binary floats.

For full steps, the target time is `step_index * resolved_dt`; for a
non-divisible duration, the final solve uses the Decimal-derived remainder.
For both divisible and non-divisible cases, the last recorded target is the
resolved `duration_s` itself. Therefore exact arrival means equality with that
resolved floating-point runtime value, which is also the value exposed through
solver history and timeseries output.

## Verification

Environment: Reaktoro `2.13.0` in `fypr-reaktoro`.

| Check | Result |
|---|---|
| `conda run -n fypr-reaktoro python -m pytest -q tests/test_fixed_timestep_controller.py` | `8 passed in 0.70s` |
| `conda run -n fypr-reaktoro python -m pytest -q tests/test_first_version.py -k general_reaction_rate_contract` | `1 passed, 20 deselected in 1.29s` |
| `conda run -n fypr-reaktoro python -m pytest -q` | `34 passed in 3.48s` |
| Protected scientific-file diff | No changes to `Kinec_v3_4.dat`, `kinec_rates_minimal.yaml`, or `Kinect_Custom_Rates.py` |

The focused tests cover divisible and non-divisible durations, `dt` greater
than duration, fractional steps, 360-day years, mixed year/day units,
monotonic contiguous timestamps, failed solves, and exact final-time arrival.

## Remaining limitations

- V1 still accepts a step using only `result.succeeded()`; no scientific
  acceptance thresholds or timestep-convergence criterion were added.
- A failed solve now restores the last accepted state and publishes no attempted
  timestamp; retry with a smaller timestep remains unimplemented.
- Adaptive stepping, rejected-step recovery, and restart remain outside V1
  scope. Diagnostic fixed-step checkpoints were added in the later
  output/checkpoint scheduling correction.
- Decimal input values that only approximate a rational fraction can produce a
  very small legitimate final remainder. No hidden tolerance discards that
  configured simulation time.
- Passing tests establish controller behaviour and the existing Reaktoro call
  contract; they do not establish kinetic timestep convergence, conservation,
  experimental agreement, reactive-transport behaviour, or fracture sealing.

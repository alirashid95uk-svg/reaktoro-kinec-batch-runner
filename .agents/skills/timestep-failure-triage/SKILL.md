---
name: timestep-failure-triage
description: Use as first-response triage for adaptive-timestep failures. Classify setup errors, Reaktoro nonlinear-solve failures, temporal-error rejections, event corrections, minimum-step exhaustion, and output/checkpoint failures before changing parameters.
metadata:
  inspiration: HeshamFS/materials-simulation-skills simulation-failure-triage
---

# Timestep Failure Triage

## First rule

Preserve the first failing evidence before retrying: resolved config, solver-history entry, accepted state/time, first exception/result failure, trial `h`, retry count, and git commit.

## Classification

### Setup/configuration failure

Examples: database loading, mineral mapping, invalid tolerance, invalid units, missing kinetic record.

Action: fix the configuration/source error. **Do not shrink dt** as a workaround.

### Reaktoro nonlinear-solve failure

The chemical solve itself did not succeed.

Action:

```text
rollback -> emergency shrink -> retry
```

If the same state fails at `dt_min`, stop and investigate chemistry/solver semantics.

### Temporal-error rejection

All required solves succeeded, but `E > 1`.

Action: rollback and compute a smaller error-based retry timestep. Do not label this as a Reaktoro solver failure.

### Hard-event correction

The trial crossed a hard geochemical event.

Action: rollback/localize the event, accept only a valid event-resolved state, reset controller history.

### Soft-event cap

Not a failure. The next timestep was conservatively limited near an event indicator.

### Numerical-integrity failure

Non-finite or materially invalid state after a nominally successful solve.

Action: reject, preserve evidence, determine whether it is timestep-related before retrying.

### Output/checkpoint failure

Chemical state may be valid but output writing failed. Preserve accepted time/state and mark output completeness honestly.

## Retry discipline

Change only one control at a time while diagnosing. Do not simultaneously modify `dt_min`, tolerances, kinetics, database, and controller factors.

## Stop conditions

Stop rather than stabilizing arbitrarily when:

- failure persists at `dt_min`;
- required scientific input is missing/invalid;
- success depends on changing unverified kinetic/thermodynamic values;
- rollback equivalence cannot be established;
- the controller enters repeated event/rejection chatter without progress.

A run that eventually completes is not automatically scientifically valid.

---
name: controller-property-testing
description: Use when testing pure adaptive-timestep controller logic over broad input domains. Focus on invariants for bounds, target landing, rollback, error scaling, and event caps. Inspired by property-based testing practice; do not use randomized real Reaktoro simulations as the primary oracle.
metadata:
  inspiration: trailofbits/skills property-based-testing
---

# Controller Property Testing

## Principle

Example tests protect known cases. Property tests protect controller laws over many generated inputs.

Use the strongest available property; avoid tests that merely reimplement the same formula as the production code.

## High-value properties

### Bounds

For every valid finite input:

```text
dt_min <= h_trial <= dt_max
```

unless the remaining exact target interval is smaller than `dt_min`, in which case the documented final-target rule must determine behaviour.

### Exact target cap

A proposed step must never cross the nearest output/checkpoint/final target.

### Monotonic accepted time

Accepted physical time must increase strictly after an accepted positive step and must not change after rejection.

### Rollback invariant

For every rejected trial:

```text
accepted_state_after_rejection == accepted_state_before_trial
accepted_time_after_rejection  == accepted_time_before_trial
```

Use project-supported state comparison, not object identity.

### Error monotonicity

For fixed tolerances and state scale, increasing the full-vs-half disagreement must not reduce normalized `E`.

### Tolerance monotonicity

Increasing `atol` or `rtol` must not increase normalized `E` for the same trial data.

### Zero-to-positive safety

A controlled mineral amount starting at zero must yield finite scaling when `floor > 0` or `atol > 0`.

### Accepted-state rule

When a trial is accepted, the retained chemical state must be the two-half-step state, not the full-step state or algebraic extrapolation.

### Event cap

A valid predicted future event earlier than the controller step must not be crossed.

### Determinism

For identical deterministic inputs and Reaktoro behaviour, pure controller decisions must be reproducible.

## Tool choice

If Hypothesis is already present, use it. If not, adding a new dependency is a project/user decision. Deterministic parameterized tests and bounded random loops with a fixed seed are acceptable alternatives.

## Do not

- generate arbitrary invalid ChemicalStates;
- fuzz thermodynamic/kinetic scientific values to make a controller test pass;
- replace focused real Reaktoro contract tests with mocks;
- assert only `does not crash` when a stronger invariant exists.

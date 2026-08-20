---
name: adaptive-timestep-error-control
description: Use when implementing, reviewing, or validating the Richardson error-controlled, geochemistry-aware adaptive timestep controller in this Reaktoro batch runner. Governs full-vs-two-half-step trials, error scaling, acceptance, rollback, I-controller updates, and hard/soft geochemical events. Do not use for unrelated fixed-step or documentation-only work.
---

# Adaptive Timestep Error Control

## Authority

For this feature use, in order:

1. the user's explicit implementation instruction;
2. `docs/design/adaptive_timestep_error_control.md`;
3. active runtime code/tests and `docs/dev/solver_workflow_and_long_horizon_timestep.md`;
4. this skill.

If these disagree, stop and report the conflict rather than silently choosing new scientific behaviour.

## Goal

Upgrade the outer timestep controller from primarily **solver-feasibility-driven** behaviour to explicit temporal-error control while retaining exact target landing, rollback, diagnostics, and Reaktoro's scientific semantics.

The accepted-step decision must conceptually answer:

```text
Can Reaktoro solve the trial?
AND
Is the estimated temporal error acceptable?
AND
Is the resulting state scientifically admissible?
```

## Core numerical contract

From the same last accepted state `y_n`, perform independent trial trajectories:

```text
full:      y_F = Phi_h(y_n)
half 1:    y_H1 = Phi_(h/2)(y_n)
half 2:    y_H = Phi_(h/2)(y_H1)
```

Do not let the full-step branch mutate the starting state used by the half-step branch.

### Controlled quantity

Prefer verified kinetic reaction extents if they can be obtained cleanly and consistently from the active Reaktoro interface. Otherwise use kinetic mineral amounts.

For controlled quantity `q_j`:

```text
e_j = abs(q_H,j - q_F,j) / (2**p - 1)
T_j = atol_j + rtol_j * max(abs(q_H,j), floor_j)
E_j = e_j / T_j
E   = max_j(E_j)
```

Accept the temporal-error criterion only when:

```text
E <= 1
```

`atol_j` must have the same physical unit as `q_j`. `rtol_j` is dimensionless. `floor_j` exists to make zero-to-positive precipitation numerically meaningful; it must be explicit/configured or scientifically justified, not invented ad hoc.

### Temporal order

Do **not** silently assume `p = 1` merely because first-order behaviour is plausible.

- The implementation may require an explicit `temporal_order` input while validation is pending.
- Determine observed order using `h`, `h/2`, `h/4` refinement on representative cases.
- If a stable asymptotic order cannot be demonstrated, do not claim Richardson control is validated.

### Accepted state

On an accepted interval retain the actual two-half-step Reaktoro state:

```text
y_(n+1) = y_H
```

Do not inject an algebraically Richardson-extrapolated ChemicalState unless a later explicit design proves that the extrapolated state satisfies all Reaktoro equilibrium/kinetic constraints.

## First controller: I-controller

Use a simple error-based controller as the initial production candidate:

```text
k = p + 1
h_proposed = safety * h * E**(-1/k)
```

Then clip by configured bounds and growth/shrink limits.

Required handling:

- `E == 0`: use the configured maximum growth factor; never divide by zero.
- non-finite `E`: reject the trial.
- `E > 1`: reject and calculate a smaller retry timestep, clipped so rejection cannot grow the step.
- `E <= 1`: accept and calculate the next candidate timestep.

Do not introduce PI/digital-filter history as the default implementation until the Richardson error stream has been validated. PI is a later smoothing/efficiency upgrade.

## Separate nonlinear-solver failure from LTE rejection

A failed Reaktoro solve gives no valid Richardson error estimate.

Use a separate path:

```text
trial solve failure
-> restore last accepted state
-> emergency shrink/restart factor
-> retry
```

Log this reason distinctly from `temporal_error_rejection`.

If repeated shrinking reaches `dt_min`, stop with a clear failure. Do not keep changing chemistry, tolerances, kinetics, or equilibrium constraints to force the run through.

## Reaktoro first-step semantics

Reaktoro has native first-step/preconditioning behaviour. Do not duplicate or bypass it blindly.

Before finalizing the startup path, use `reaktoro-runtime-validation` to establish whether Richardson branches at `t=0` receive equivalent native preconditioning. Preferred outcome is a common, scientifically valid starting state from which both branches are independent. If exact equivalence is not demonstrated, treat startup as a special verified path rather than assuming ordinary branch semantics.

## Solver reuse and rollback

Restoring `ChemicalState` is not automatically proof that all solver-side history has been reset.

For rejected real Reaktoro trials:

- compare retry behaviour with a reused solver and a newly constructed solver when material;
- reconstruct/reset the solver after rejection if state-only rollback is not proven equivalent;
- preserve existing focused rollback tests.

## Geochemical events

### Hard events

Use hard events for transitions that should be localized or explicitly landed on, such as:

- complete kinetic mineral exhaustion (`n_m -> 0`);
- actual phase disappearance where the implemented model exposes it;
- externally imposed condition changes.

A hard event may cause rollback/correction. Reset controller history after a hard event.

### Soft events

Use soft events only to cap the next trial timestep initially:

- saturation-index crossing (`SI_m -> 0`);
- first indication of secondary-mineral appearance;
- rapid pH movement;
- rapidly changing reaction rate.

`SI = 0` is a thermodynamic transition indicator, not universally proof that precipitation physically starts at that exact instant. Do not treat every SI crossing as an exact hard event without model justification.

### Event prediction

A linear zero-crossing predictor may be used only with two valid accepted states and a finite nonzero slope:

```text
t_event = t_n - g_n * (t_n - t_(n-1)) / (g_n - g_(n-1))
h_event = t_event - t_n
```

Ignore invalid/past predictions. Protect against repeated near-zero event caps that create infinite retry loops.

## Target landing

The trial timestep must continue to land exactly on:

- requested output times;
- checkpoint times;
- final duration.

Use the minimum valid cap:

```text
h_trial = min(h_controller, h_event_if_valid, h_output, h_checkpoint, h_final, h_max)
```

Do not interpolate chemistry to output times when an exact shortened solve can land there.

## Scientific/admissibility checks

Keep these independent from the Richardson LTE norm:

- finite state values;
- material negative amounts beyond configured numerical tolerance;
- appropriate element/component conservation diagnostics;
- pH and SI diagnostics/events;
- exact Reaktoro result success.

Do not use conserved totals as evidence that temporal trajectory error is small.

## Configuration discipline

If public YAML/schema changes:

- route through `case-config-discipline`;
- keep units explicit;
- reject NaN/Inf and invalid bounds;
- no scientifically important hidden defaults;
- update schema template, runtime validation, documentation, and positive/negative tests together.

Do not expose PI controls until PI behaviour is actually implemented and tested.

## Required solver-history diagnostics

Record enough information to reconstruct controller decisions. At minimum:

- accepted time before/after trial;
- requested and effective `h`;
- full-step/half-step solve statuses;
- actual number of Reaktoro solve calls;
- `E`, worst controlled variable, and its scaled error;
- accepted/rejected status and rejection reason;
- event cap type/target when used;
- controller-produced next `h`;
- retry count;
- whether solver reconstruction/reset occurred.

Do not mislabel two-half-step subsolves as accepted physical timesteps; only the completed accepted interval advances accepted time.

## Focused test requirements

Software tests must include at least:

1. identical starting-state independence of full and half branches;
2. exact Richardson formula for a deterministic toy map;
3. `E <= 1` acceptance and `E > 1` rollback;
4. `E == 0`, non-finite error, `dt_min`, `dt_max`, and growth/shrink clipping;
5. zero-to-positive mineral appearance under absolute+relative tolerance;
6. output/checkpoint/final-time exact landing;
7. real Reaktoro failure/rollback/retry probe;
8. startup/preconditioning branch equivalence probe;
9. hard-event correction and soft-event capping;
10. controller-history reset after hard event/repeated rejection;
11. deterministic fixed-step regression remains unchanged.

Property tests should cover pure controller logic; do not use randomized real Reaktoro runs as a substitute for focused scientific runtime tests.

## Scientific validation gate

Before claiming the new controller is scientifically validated:

- demonstrate observed temporal order on representative cases;
- compare against progressively refined fixed-step references;
- show tighter tolerances reduce discrepancy;
- test dissolution, precipitation from zero, exhaustion, near-equilibrium, and rapid initial CO2 acidification;
- verify conservation/integrity behaviour;
- compare adaptive and fixed runtime at matched measured accuracy.

Passing unit tests alone is not timestep convergence or scientific validation.

## Completion report

Report separately:

1. implementation changes;
2. focused software tests;
3. real Reaktoro runtime probes;
4. temporal-convergence evidence;
5. matched-accuracy performance evidence;
6. scientific inputs explicitly confirmed unchanged;
7. remaining limitations.

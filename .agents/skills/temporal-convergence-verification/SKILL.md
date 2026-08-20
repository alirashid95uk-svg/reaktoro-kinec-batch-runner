---
name: temporal-convergence-verification
description: Use to determine observed temporal order, test whether Richardson step-doubling is in the asymptotic regime, and verify adaptive results against refined fixed-step Reaktoro trajectories. Use before assigning or validating temporal_order p.
metadata:
  inspiration: HeshamFS/materials-simulation-skills convergence-study
---

# Temporal Convergence Verification

## Goal

Establish whether the Reaktoro outer kinetic stepping exhibits a stable temporal order that justifies Richardson error control.

## Required refinement

Use at least three systematically refined fixed timesteps:

```text
h, h/2, h/4
```

Prefer four levels for high-confidence claims.

Compare quantities at the **same physical output times**, not by internal-step index.

For refinement ratio `r = 2` and a scalar or vector quantity of interest `Q`, estimate:

```text
p_obs = log( ||Q_h - Q_h2|| / ||Q_h2 - Q_h4|| ) / log(2)
```

Use an explicitly stated norm. For multiple minerals/species, prefer a scaled vector norm or report per-quantity orders rather than hiding disagreement in one aggregate.

## Validity checks

Do not report a reliable order when:

- either difference is zero/near roundoff;
- convergence is oscillatory/non-monotone without explanation;
- successive triplets give materially different `p_obs`;
- one refinement level contains solver failures or different physical/event semantics;
- compared outputs are not aligned to common physical times.

A visually similar trajectory is not sufficient evidence of asymptotic convergence.

## Representative regimes

Run the study across at least:

- rapid initial CO2 acidification;
- fast carbonate dissolution;
- slower silicate/clay kinetics;
- secondary-mineral precipitation from zero;
- mineral exhaustion;
- near-equilibrium behaviour.

If the observed order differs by regime, do not hide this behind one global `p`. Either justify a conservative order or redesign the estimator/controller assumption.

## Reference trajectory

For validation of the adaptive controller, generate a progressively refined fixed-step reference until the chosen QoIs change less than a documented reference threshold.

Then compare adaptive outputs at the same requested output times.

## Evidence to record

- case/config hash or git commit;
- `h`, `h/2`, `h/4` values and units;
- controlled quantities/QoIs;
- norm/scaling;
- observed order per triplet;
- whether the sequence appears asymptotic;
- reference trajectory choice;
- discrepancies at each tolerance level.

## Pass criterion

Do not hard-code a universal numerical threshold for `p_obs` without a project decision. A defensible pass requires stable order across successive refinement triplets and representative chemical regimes.

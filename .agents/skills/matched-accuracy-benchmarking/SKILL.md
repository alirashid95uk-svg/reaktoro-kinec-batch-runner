---
name: matched-accuracy-benchmarking
description: Use only after correctness/convergence work to compare adaptive and fixed timestep performance at approximately equal measured numerical accuracy. Prevents misleading speedup claims based on unequal error.
metadata:
  inspiration: HeshamFS/materials-simulation-skills verification/benchmark guidance
---

# Matched-Accuracy Benchmarking

## Core rule

Never claim speedup from:

```text
adaptive runtime vs arbitrary fixed-step runtime
```

Compare:

```text
adaptive runtime at measured error epsilon
vs
fixed-step runtime at approximately the same measured error epsilon
```

## Required benchmark setup

1. Establish a refined fixed-step reference trajectory.
2. Choose common physical output times.
3. Define scientific quantities of interest and error norms before timing.
4. Warm up/import dependencies consistently.
5. Record wall time and actual Reaktoro solve-call count.
6. Repeat enough times to distinguish timing noise from real improvement.

## Metrics

Record at minimum:

- wall-clock runtime;
- accepted outer intervals;
- rejected trials;
- full/half Reaktoro solve calls;
- solver-failure retries;
- event-limited steps;
- maximum/aggregate error vs reference at common output times;
- tolerance settings;
- fixed-step size achieving comparable error.

## Richardson overhead

One trial may require up to three Reaktoro solves. Report this cost explicitly. A reduction in accepted outer-step count does not imply runtime speedup.

## Cases

Benchmark at least one fast-transient case and one slow long-horizon case. If the controller helps only one regime, report that rather than averaging the distinction away.

## Claim language

Valid:

> At comparable trajectory error, the adaptive controller reduced median wall time by X% for case Y.

Invalid:

> Adaptive stepping is X times faster.

unless the accuracy, hardware, environment, repetitions, and workload are all documented.

---
name: scientific-change-verification
description: Verify source-code, test, configuration, and documentation changes in this Reaktoro batch runner without silently changing scientific settings. Use after implementing or reviewing repository changes and before claiming completion.
---

# Scientific Change Verification

## Workflow

1. Read `AGENTS.md` and every triggered project skill.
2. Inspect `git status --short` and the affected execution path before editing.
3. Preserve scientific input values and user-supplied files unless the request
   explicitly changes them.
4. If solver, timestep, output, postprocessing, or schema behavior changes,
   read the three coordinated design files in `docs/dev/` together.
5. Run the smallest targeted test in `fypr-reaktoro`, then run the full suite
   when a shared runtime module or config model changed.
6. Run `git diff --check` and inspect `git diff --stat` plus the relevant diff.
7. Confirm protected scientific files did not change unexpectedly:

```powershell
git diff --exit-code -- data/thermo/Kinec_v3_4.dat data/kinetics/kinec_rates_minimal.yaml batch_runner/Kinect_Custom_Rates.py
```

Include the adapter in that command only when it was not intentionally edited.

## Completion Standard

Report separately:

- static checks performed;
- tests performed and exact result;
- real Reaktoro runtime probes performed;
- scientific validation performed;
- checks not performed and why.

Passing tests prove software behavior only. Do not claim calibrated kinetics,
timestep convergence, conservation, experimental agreement, transport
behavior, or fracture sealing unless those checks were actually run.

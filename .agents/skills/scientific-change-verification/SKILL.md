---
name: scientific-change-verification
description: Use before claiming completion when a change can alter scientific/runtime behaviour, configuration semantics, solver behaviour, output interpretation, or Reaktoro integration. Do not use for documentation-only, formatting, UI-only, or other changes that cannot alter the scientific/runtime contract.
---

# Scientific Change Verification

## Scope

This skill verifies behaviour-changing work. It is not a mandatory wrapper for
every source-code edit.

Use it for changes to:

- chemistry/system/state construction;
- thermodynamic or kinetic integration;
- solver calls, workflow staging, timestep control, rollback, or acceptance;
- scientific configuration semantics or defaults;
- scientific observations, diagnostics, conservation checks, or output meaning;
- exact Reaktoro API assumptions.

Do not trigger it solely because a file has a `.py` extension.

## Verification

1. Inspect the affected execution path and identify the scientific/runtime claim
   that could be wrong.
2. Confirm no scientific value, unit, database, kinetic parameter, surface area,
   boundary condition, or timestep control changed unintentionally.
3. Run the smallest focused test that can falsify the changed behaviour.
4. If exact Reaktoro 2.13 semantics are material, run a minimal real runtime
   probe in `fypr-reaktoro` or the existing focused contract test.
5. Run the broader suite once at final integration only when shared runtime,
   configuration, or output infrastructure changed enough to justify it.
6. Inspect the relevant diff for unintended scope expansion.

Read a design contract only when the change affects that contract:

- config schema -> `docs/dev/config_schema_feature_options.md`;
- solver/timestep -> `docs/dev/solver_workflow_and_long_horizon_timestep.md`;
- output package -> `docs/dev/output_package_design.md`.

Read all three only for a genuinely cross-cutting change.

When scientific source files are within the write scope, explicitly confirm they
were not changed unexpectedly:

```powershell
git diff --exit-code -- data/thermo/Kinec_v3_4.dat data/kinetics/PalandriKharaka_local.yaml data/kinetics/kinec_rates_minimal.yaml
```

Do not require this command for documentation-only work or changes that cannot
touch those files.

## Completion Report

Report separately:

- focused static/tests performed;
- real Reaktoro runtime probes performed, if any;
- scientific validation performed, if any;
- checks not performed because they were outside the change's risk boundary.

Passing tests prove software behaviour only. Do not promote them to evidence of
calibration, timestep convergence, conservation, experimental agreement,
transport behaviour, or fracture sealing.

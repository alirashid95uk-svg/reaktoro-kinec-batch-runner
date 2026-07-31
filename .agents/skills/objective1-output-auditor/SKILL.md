---
name: objective1-output-auditor
description: Audit an Objective 1 Reaktoro batch output directory for package completeness, schema consistency, input provenance, time and solver coherence, enabled-file behavior, and scientific interpretation limits. Use after a run, before exporting datasets, or when outputs may be stale, partial, or mixed.
---

# Objective 1 Output Auditor

Read these files together before changing output behavior:

```text
docs/dev/output_package_design.md
docs/dev/solver_workflow_and_long_horizon_timestep.md
docs/dev/config_schema_feature_options.md
```

Run the deterministic package audit from the repository root:

```powershell
python .agents/skills/objective1-output-auditor/scripts/audit_output_package.py <output_dir>
```

The command exits nonzero for package-contract failures and prints JSON. It
does not invent scientific tolerances: carbon and element balance maxima are
reported as metrics, while acceptance thresholds must come from an explicit
project or benchmark decision.

## Interpretation

- Treat hash mismatches, undeclared files, schema disagreement, incomplete
  runs, time mismatches, failed accepted steps, and reaction-rate sign failures as
  blocking findings.
- Treat validation targets outside uncertainty as failed scientific checks,
  not reasons to tune inputs automatically.
- Treat `not_evaluated` porosity/permeability/capillary fields honestly.
- Do not promote a clean package audit to proof of calibration, timestep
  convergence, mass conservation, reactive transport, or fracture sealing.

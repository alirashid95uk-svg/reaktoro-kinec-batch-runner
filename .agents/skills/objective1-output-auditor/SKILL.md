---
name: objective1-output-auditor
description: Audit an existing Objective 1 output directory for package completeness, schema consistency, provenance, time/solver coherence, enabled-file behaviour, and interpretation limits. Use after a run or before consuming outputs; do not use for ordinary case-YAML editing.
---

# Objective 1 Output Auditor

For an existing output package, run:

```powershell
python .agents/skills/objective1-output-auditor/scripts/audit_output_package.py <output_dir>
```

The command exits nonzero for package-contract failures and prints JSON.

When changing output-package behaviour, read
`docs/dev/output_package_design.md`. Read the solver or schema contract only if
the output change also alters solver/timestep behaviour or case configuration.
Do not require all three documents for an output-local change.

## Interpretation

Treat hash mismatches, undeclared files, schema disagreement, incomplete runs,
time mismatches, failed accepted steps, and reaction-rate sign failures as
blocking package findings.

Carbon and element balance maxima are metrics unless an explicit project or
benchmark tolerance defines pass/fail behaviour. Do not invent tolerances.

Validation targets outside their stated uncertainty are failed scientific
checks, not reasons to tune inputs automatically.

Treat `not_evaluated` porosity/permeability/capillary fields honestly.

A clean package audit establishes package coherence only. It does not prove
calibration, timestep convergence, mass conservation, reactive transport, or
fracture sealing.

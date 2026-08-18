# Configuration Rules

This document records configuration-specific rules only. Project-wide
scientific, architecture, routing, and verification rules live in `AGENTS.md`.

## Configuration Layers

```text
schema_template.yaml
-> case input
-> validated/resolved configuration
```

### Schema template

- Documents the accepted YAML shape.
- May contain explicit placeholder sentinels.
- Is not a runnable scientific case until placeholders are replaced.

### Case input

- Contains real source-supported values.
- Uses explicit units and feature choices.
- Contains no unresolved placeholder sentinels.

### Resolved configuration

- Is generated deterministically.
- Contains normalized paths, canonical units, approved defaults, derived values,
  schedules, and hashes as implemented by the active resolver.
- Is an output/provenance artifact, not a source case to edit manually.

## Runtime Contract

`batch_runner/config/` and focused tests define active accepted behaviour.
`docs/dev/config_schema_feature_options.md` defines approved schema intent.
`cases/schema_template.yaml` and runnable cases must stay synchronized with the
active runtime model.

Unknown fields and incompatible combinations must fail validation.

Do not expose unsupported runtime features merely as disabled schema blocks.
Cation exchange and automatic restart are not active runtime features.
Configured validation targets are reporting inputs, not an automatic experiment
calibration workflow.

## Scientific Values and Defaults

Do not invent scientific numeric values. New values must come from supplied
files/data, explicit instruction, cited project sources, or deterministic
preprocessing.

Do not hide scientific-behaviour defaults in Python. Approved defaults must be
implemented deliberately, documented, tested where needed, and visible in the
resolved configuration.

## Units and Ownership

Use fixed-unit field names when one canonical unit is enforced, and `{value,
unit}` structures when multiple units are supported. Reject ambiguous unitless
scientific quantities.

Duration and timestep control belong under `solver.timestep`, not `kinetics`.
Database selection is explicit. Missing required kinetic records, thermodynamic
minerals, local paths, or kinetic-mineral surface areas are hard failures.

For case/schema editing workflow and focused verification, use the
`case-config-discipline` skill rather than duplicating that process here.

# Configuration Rules

This document records configuration-specific rules only. Project-wide
scientific, architecture, routing, and verification rules live in `AGENTS.md`.

## Configuration Layers

```text
CaseConfig models
-> case input
-> validated/resolved configuration
```

### Authoritative model

- `batch_runner.config.case.CaseConfig` and its nested Pydantic models define
  accepted fields, types, defaults, constraints, and descriptions.
- Named model validators define conditional requirements and cross-feature
  rules that cannot be expressed by individual fields.
- `python runner.py config --help` and the generated configuration reference
  are projections of those definitions, not independent registries.

### Schema template

- Demonstrates the accepted YAML shape for case authors.
- May contain explicit placeholder sentinels.
- Is not a runnable scientific case until placeholders are replaced.
- Is an authoring aid, not the schema authority.

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
`docs/dev/config_schema_feature_options.md` records the durable configuration
contract without duplicating the field catalogue. `cases/schema_template.yaml`
and runnable cases must stay synchronized with the active runtime model.

Unknown fields and incompatible combinations must fail validation.

Do not expose unsupported runtime features merely as disabled schema blocks.
Cation exchange and automatic restart are not active runtime features.
Configured post-simulation validation scripts are trusted downstream analysis,
not an automatic experiment-calibration workflow.

## Scientific Values and Defaults

Do not invent scientific numeric values. New values must come from supplied
files/data, explicit instruction, cited project sources, or deterministic
preprocessing.

Approved defaults must be implemented deliberately in the Pydantic models,
documented in field metadata, tested where needed, and visible in the resolved
configuration. Conditional defaults must remain explicit in validator metadata
and the generated reference.

## Generated Reference

Run `python tools/build_docs.py` to regenerate the configuration and CLI pages
and build the documentation site strictly. Do not edit files under
`docs/generated/`; they are ignored build products.

## Units and Ownership

Use fixed-unit field names when one canonical unit is enforced, and `{value,
unit}` structures when multiple units are supported. Reject ambiguous unitless
scientific quantities.

Duration and timestep control belong under `solver.timestep`, not `kinetics`.
Database selection is explicit. Missing required kinetic records, thermodynamic
minerals, local paths, or kinetic-mineral surface areas are hard failures.

For case/schema editing workflow and focused verification, use the
`case-config-discipline` skill rather than duplicating that process here.

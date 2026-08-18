---
name: case-config-discipline
description: Use for case YAML, CaseConfig/schema, configuration preprocessing, schema templates, or configuration documentation. Keep runtime schema, user-facing YAML, and scientific provenance aligned without inventing values.
---

# Case Config Discipline

## Authority

Use these sources in order:

1. `batch_runner/config/` and focused tests define active runtime behaviour.
2. `docs/dev/config_schema_feature_options.md` defines approved schema intent.
3. `cases/schema_template.yaml`, runnable cases, README, and config documentation
   must describe the active contract accurately.

Read the solver or output design document only when the configuration change
also alters that runtime/output contract. Do not read all three coordinated
design documents for a schema-local change.

## Scientific Values

Do not invent, tune, or copy unrelated scientific values.

Scientific values may come only from supplied files/data, explicit user
instruction, cited project sources, or deterministic preprocessing.

Templates may contain placeholders. Runnable cases may not contain unresolved
placeholder sentinels such as `REQUIRED`, `OPTIONAL`, `TBD_SOURCE_REQUIRED`, or
`REQUIRED_IF_*`.

Preserve provenance fields when they exist. Add new provenance structure only
when the new scientific input genuinely requires it.

## Change Types

### Case-data change

- Edit only the relevant runnable case.
- Do not change the schema to accommodate malformed data.
- Validate through `load_case`.
- Confirm paths and units resolve as intended.
- Run a focused test only when runtime behaviour needs protection.

### Schema or preprocessing change

- Update the strict Pydantic model and relevant cross-field validation.
- Keep unknown fields and invalid combinations as hard failures.
- Update the schema template when the accepted YAML shape changes.
- Update `docs/dev/config_schema_feature_options.md` when the public contract
  changes.
- Update runnable cases only when source-supported values are available.
- Add focused positive/negative tests for the changed rule.

Do not require unrelated README, output, solver, Workbench, or skill-routing
changes unless their contract actually changed.

## Stable Rules

- Duration and timestep control belong under `solver.timestep`.
- Optional scientific behaviour must be explicit.
- Database selection is explicit; no fallback database.
- Missing kinetic records, thermodynamic minerals, or required surface areas are
  hard failures.
- Do not add case-level mineral aliases.
- Do not expose unsupported runtime features merely as disabled schema blocks.
- Defaults that affect scientific behaviour must be approved, documented, and
  visible in resolved configuration.
- Units must be explicit and validated.

## Verification

Use the smallest check that covers the edit:

```powershell
conda run -n fypr-reaktoro python -m pytest -q tests/test_first_version.py -k "schema_template or config or timestep or redox or kinetics"
```

Narrow the `-k` expression further when possible. Validate every modified
runnable case with `load_case`.

Run broader tests only when the configuration change also alters shared runtime
execution or outputs.

Report scientific values changed and their provenance separately from schema,
default, documentation, and test changes.

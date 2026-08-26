# Configuration Schema and Feature Options — Active Contract

## Authority

The strict Pydantic models under `batch_runner/config/` define accepted source
YAML. Their field annotations, defaults, constraints, descriptions, and named
validators are the authoritative configuration interface.

The generated Configuration Reference is a browsable projection of those
models. Build it with:

```text
python tools/build_docs.py
```

Terminal discovery uses the same projection:

```text
python runner.py config --help
python runner.py config --help timestep
python runner.py config --help solver.timestep.step_size
```

Do not manually add a field catalogue to this document. The generated
reference must change by editing the runtime model that validates the field.

## Runtime Scope

The active schema supports:

- explicitly selected embedded or local PHREEQC-style databases;
- batch equilibrium and batch kinetic workflows;
- native Palandri-Kharaka or explicitly selected custom Kinec kinetics;
- disabled, finite-amount, and fixed-fugacity CO2 modes;
- optional pE-based redox at implemented workflow stages;
- fixed, solver-feasibility adaptive, and Richardson error-controlled
  timesteps;
- accepted-state output schedules and checkpoints;
- config-controlled diagnostics, summaries, plots, monitoring, and downstream
  validation.

An accepted configuration field represents implemented runtime behaviour. Do
not expose speculative features as disabled blocks, placeholders, or status
records.

## Configuration Layers

```text
source YAML
-> CaseConfig validation
-> ResolvedCase paths, units, schedules, hashes, and derived bounds
-> chemistry and solver execution
```

`CaseConfig` fields are user-editable source options. `ResolvedCase` adds
deterministic operational values such as absolute paths, canonical seconds,
schedule targets, step counts, and source hashes; those derived values are not
additional YAML fields.

`cases/schema_template.yaml` is a non-runnable authoring aid containing explicit
placeholder sentinels. It is not the schema authority and need not enumerate
every mutually exclusive mode on one YAML tree.

## Stable Scientific Rules

- Unknown fields fail validation.
- Invalid local and cross-section combinations fail validation.
- Database selection is explicit; there is no database fallback.
- Scientific behaviour is explicit, including kinetic model, mineral roles,
  surface areas, CO2 boundary conditions, redox staging, and timestep mode.
- Duration and timestep controls belong under `solver.timestep`.
- Time values use supported explicit units and resolve to canonical seconds.
- Any configured use of `year` or `years` requires an explicit positive
  `year_definition_days`; no year length is assumed.
- Kinetic minerals require initial amount and surface area; enabled kinetics
  requires at least one kinetic mineral.
- Missing thermodynamic minerals, kinetic records, required parameter files, or
  local database files are hard failures.
- Output and monitor selections may observe only configured/requested species,
  minerals, schedules, and diagnostics.
- Checkpoints contain accepted states only and do not provide restart.
- Post-simulation validation is trusted downstream analysis. It does not alter
  simulation results or constitute automatic calibration.

Mode-specific requirements and conflicts are implemented by discriminated
models or named Pydantic validators and appear in generated configuration help.

## Defaults and Examples

Defaults that affect behaviour must be deliberate, visible in the model, and
included in resolved configuration. Conditional defaults that cannot be
expressed as an unconditional Pydantic field default must be stated in the
owning model or validator description.

Do not invent scientific numerical examples. Runnable case values require
project-supported provenance. Schema examples may illustrate non-scientific
structure, enum selection, or unit syntax only.

## Unsupported Source Fields

The following concepts are not active source-case options and must remain
unknown-field failures:

```text
solver.backend
solver.restart
solver.safety
solver.conservation
solver.geochemical_controls
solver.workflow.precondition_kinetics
solver.timestep.acceptance
solver.timestep.mode: adaptive_long_horizon
```

The repository does not currently provide authoritative reactive transport,
automatic restart, cation exchange, or automatic experimental calibration.

## Maintenance Rule

When the accepted YAML contract changes in one implementation:

1. update the owning Pydantic annotation, `Field` metadata, and named validator;
2. preserve or explicitly approve every scientific default and unit;
3. update the schema template only when its illustrated YAML shape changes;
4. add focused positive and negative tests for changed validation behaviour;
5. build the generated documentation strictly.

Generated configuration and CLI pages are disposable build products. Never edit
them as source documentation.

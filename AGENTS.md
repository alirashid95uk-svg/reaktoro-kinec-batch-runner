# Project Guidance

## Purpose

This repository is a research-grade Reaktoro batch geochemical simulator for
CO2-brine-mineral systems. Keep the scientific execution path explicit,
reproducible, and maintainable without Codex.

Scientific correctness takes precedence over software abstraction.

## Supported Scope

Supported runtime scope:

- PHREEQC-style thermodynamic databases selected explicitly;
- batch equilibrium simulations;
- batch kinetic simulations;
- Reaktoro native Palandri-Kharaka kinetics by default;
- optional custom Kinec kinetics when explicitly selected;
- optional fixed-fugacity CO2 or finite-amount CO2;
- optional pE-based redox;
- fixed and adaptive timestep execution as implemented by the active schema.

Not currently supported as authoritative runtime features:

- reactive transport;
- automatic restart;
- cation exchange;
- automatic experimental calibration or experiment-fitting workflows.

The existing validation target/ledger machinery is a reporting capability; it
must not be described as automated experimental validation or calibration.

## Scientific Non-Negotiables

Never silently change:

- thermodynamic database selection or content;
- activity models;
- mineral identities or mappings;
- kinetic parameters;
- reactive surface areas;
- CO2 boundary conditions;
- redox conditions;
- timestep controls;
- experimental or literature-derived values.

Scientific numerical values must come from supplied files/data, explicit user
instruction, cited project sources, or deterministic preprocessing. Do not
invent values to make examples, tests, or simulations pass.

Keep these user-supplied scientific files protected unless the user explicitly
authorizes a scientific-content change:

```text
data/thermo/Kinec_v3_4.dat
data/kinetics/kinec_rates_minimal.yaml
data/kinetics/PalandriKharaka_local.yaml
batch_runner/simulator/kinetics/kinec.py
```

`Kinec_v3_4.dat` is thermodynamic input. Runtime kinetic-rate parameters come
from the selected kinetics YAML. Do not parse PHREEQC `RATES` blocks at runtime.

## Reaktoro Runtime

Target Reaktoro 2.13.0 with Python 3.11 in the `fypr-reaktoro` Conda
environment.

Use the installed 2.13.0 runtime as the authority when exact Python binding,
solver, reaction-rate, state-mutation, unit, sign, or surface-area semantics
matter.

Do not run a Reaktoro runtime probe for ordinary documentation, UI, refactoring,
or unrelated test changes.

## Architecture

Preserve the execution chain:

```text
YAML
-> validation/resolution
-> database + kinetics
-> chemical system
-> chemical state
-> solver
-> observations
-> outputs/diagnostics
```

`runner.py` is orchestration only. Scientific logic belongs under the focused
`batch_runner` modules.

Do not introduce plugin managers, generic backend factories, abstract simulator
engines, dependency-injection containers, dynamic imports for core execution,
silent fallbacks, or broad exception swallowing.

The project models batch geochemistry. Do not infer reactive-transport,
permeability, capillary-entry-pressure, or fracture-sealing behaviour from batch
mineral changes unless an explicit model for those quantities is implemented.

## Guidance Routing

Use one primary project skill by default. Add a second skill only when the task
actually crosses that boundary.

- Case YAML, schema, preprocessing, or config documentation:
  `case-config-discipline`.
- PHREEQC database loading or database configuration:
  `phreeqc-database-only`.
- Custom Kinec parameters or attachment:
  `kinec-yaml-kinetics`.
- Exact Reaktoro 2.13 runtime/API semantics:
  `reaktoro-runtime-validation`.
- Direct Reaktoro construction style:
  `reaktoro-simple-syntax`.
- Existing output-package audit:
  `objective1-output-auditor`.
- Cross-module feature/architecture design:
  `user-editable-project-design`.
- Changes that can alter scientific/runtime behaviour, configuration semantics,
  solver behaviour, output interpretation, or Reaktoro integration:
  `scientific-change-verification` before claiming completion.

Do not load `scientific-change-verification` for documentation-only, formatting,
UI-only, or other changes that cannot alter the scientific/runtime contract.

## Design Documents

Read only the contract directly affected by the change:

- `docs/dev/config_schema_feature_options.md` for case-schema/validation changes;
- `docs/dev/solver_workflow_and_long_horizon_timestep.md` for solver, workflow,
  timestep, rollback, or checkpoint behaviour;
- `docs/dev/output_package_design.md` for output-package contracts.

Read all three only when the change crosses those boundaries. Do not require all
three for a local change confined to one contract.

Treat large migration/historical design documents as task-specific references,
not mandatory background reading.

## Verification

Use the smallest verification that can falsify the change.

- Guidance or documentation only: inspect the changed text; no scientific
  runtime probe or full test suite is required.
- Case-data/config-only change: validate the affected case/config and run the
  focused configuration test when needed.
- Local runtime change: run the focused test covering the affected path.
- Exact Reaktoro API/solver semantics: run a minimal real Reaktoro 2.13 probe or
  focused runtime test.
- Shared runtime/config/output changes: run focused tests during development;
  run the broader suite once at final integration when warranted.

Do not use the multi-minute three-mineral case when a unit test or minimal
runtime probe answers the question.

Passing tests establish software behaviour only. Do not claim calibration,
timestep convergence, conservation, experimental agreement, or scientific
validity unless those checks were actually performed.

## Feature Status

Use only:

- `IDEA` - discussed;
- `READY` - behaviour agreed and compactly specified;
- `DONE` - implemented and verified.

Do not leave unimplemented roadmap items presented as active V1 functionality.

# Project Guidance

## Purpose

This repository is a simple Reaktoro batch simulation runner. It must remain
understandable, reproducible, and modifiable without Codex. Physics and
geochemistry take precedence over software abstraction.

## Supported Scope

- PHREEQC-style thermodynamic databases only.
- Explicitly selected embedded PHREEQC databases, such as `phreeqc.dat`.
- Explicitly selected local PHREEQC-style `.dat` databases.
- Batch equilibrium simulations.
- Batch kinetic simulations using the native local Palandri-Kharaka model by default.
- Optional custom Kinec kinetics when explicitly selected.
- Optional fixed-fugacity CO2 or finite-amount CO2 setup.
- Optional redox using pE.

Cation exchange is planned but not implemented in V1.
Experiment validation is planned but not implemented in V1.

## Active V1 Baseline

- Standard Reaktoro equilibrium and kinetics solvers.
- Fixed, adaptive, and adaptive-long-horizon timestep modes.
- Base outputs and YAML-controlled Objective 1 audit outputs.
- Rejected-step recovery and checkpoint writing.
- No automatic restart, cation exchange, reactive transport, or
  experiment-validation workflow.

Use the verified environment:

```powershell
conda run -n fypr-reaktoro python -m pytest -q
```

Use focused tests for routine smoke checks; the three-mineral development case
executes the real staged chemistry workflow.

## Scientific Workbench Scope

The approved PySide6 workbench expands only the user-facing orchestration,
editing, process-control, artifact-reading, comparison, study, dataset, and
reporting layers defined in `docs/dev/GUI_Upgrade_Fixed.md`.

- `batch_runner` remains the authoritative Qt-free scientific execution path.
- `workbench_core` remains Qt-free and operates on case and saved artifacts.
- `workbench` contains presentation, accessibility, and `QProcess` ownership.
- The command-line runner and legacy launcher remain operational until golden
  replacement-equivalence and Windows acceptance checks pass.
- Scientific values, databases, kinetic parameters, Reaktoro equations,
  defaults, timestep acceptance, and output definitions do not change during
  the GUI migration.
- Saved or derived workbench artifacts must be reproducible headlessly.
- Completed result packages remain immutable; legacy packages are indexed or
  adapted read-only and are never migrated in place.

## Skill Routing

- YAML case changes: `case-config-discipline`.
- PHREEQC database loading: `phreeqc-database-only`.
- Custom Kinec parameters or attachment: `kinec-yaml-kinetics`.
- Reaktoro construction or solver code: `reaktoro-simple-syntax` and
  `reaktoro-runtime-validation`.
- Any source-code change: `scientific-change-verification`.
- Output generation or package review: `objective1-output-auditor`; also read
  the three coordinated design files named below.
- Workbench UX design or screenshot review: `design-critique` and
  `qt-ui-design`.
- Native Windows GUI interaction, accessibility, or scale acceptance:
  `windows-desktop-e2e`.
- Workbench plots and data-visualisation accessibility: `charts-graphs`.

## Scientific Interpretation Boundary

- Batch results are not reactive-transport behaviour.
- Mineral-volume change is not porosity, permeability, or capillary-entry-
  pressure change without an explicit update law and its provenance.
- Matrix mineral precipitation is not fracture sealing unless fracture-surface
  reactions and aperture evolution are represented.

## User-Supplied Scientific Files

The user supplied these files. They have been moved to their active project
locations. Do not delete them or modify their scientific content:

```text
data/thermo/Kinec_v3_4.dat
= local PHREEQC-style thermodynamic database

data/kinetics/kinec_rates_minimal.yaml
= cleaned runtime kinetic-rate parameter file

data/kinetics/PalandriKharaka_local.yaml
= corrected local parameters for Reaktoro's native Palandri-Kharaka model

batch_runner/simulator/kinec.py
= Kinec YAML -> Reaktoro kinetic-rate adapter
```

## Thermodynamic Database Rule

PHREEQC-style databases only. Use `PhreeqcDatabase`. Database selection must
be explicit:

- No generic database backend.
- No automatic database selection.
- No fallback database.
- If a local database path fails, stop and report the exact path.

```yaml
database:
  source: embedded
  name: phreeqc.dat
```

or:

```yaml
database:
  source: local
  path: data/thermo/Kinec_v3_4.dat
```

`data/thermo/Kinec_v3_4.dat` is the user-supplied local PHREEQC-style
thermodynamic database. It is not the runtime kinetic-rate input.

## Kinetic-Rate Rule

The default kinetic model is Reaktoro's native
`ReactionRateModelPalandriKharaka` loaded from:

```text
data/kinetics/PalandriKharaka_local.yaml
```

Reaktoro matches each exact thermodynamic mineral species name through the
parameter record's `Mineral` and `OtherNames` fields. Missing matches are hard
failures. Case configuration must not add a separate mineral alias.

The custom Kinec model is available only with explicit `model: kinec` and uses:

```text
data/kinetics/kinec_rates_minimal.yaml
```

Do not parse PHREEQC `RATES` blocks at runtime. Do not use
`Kinec_v3_4.dat` as the runtime kinetic-rate input.

- Missing kinetic record = hard failure.
- Missing surface area for a kinetic mineral = hard failure.
- Missing thermodynamic mineral = hard failure.
- No silent skipping.
- No invented kinetic parameters.

`ReactionRateModelKinec` is provided by the user-supplied adapter, not by
Reaktoro. Its units and dissolution/precipitation sign must be validated
against the exact Reaktoro reaction-rate interface used before scientific
runs. Standard diagnostics use Reaktoro's runtime `ChemicalProps.reactionRate`
for either selected model.

## No-Random-Values Rule

Do not invent scientific numeric values. Case values must come from supplied
files, supplied data, explicit user instruction, or deterministic
preprocessing. Templates must use placeholders, not fake numbers.

## Simple Reaktoro Syntax Rule

Use simple visible Reaktoro calls. Prefer direct use of
`PhreeqcDatabase`, `AqueousPhase`, optional `GaseousPhase`,
`MineralPhases`, `ChemicalSystem`, `ChemicalState`, solver classes,
`ChemicalProps`, and `AqueousProps`. Use `ActivityModelPhreeqc` for PHREEQC
aqueous systems unless an explicit, documented project decision changes it.
Do not hide basic Reaktoro setup behind unnecessary classes. Use pseudocode
labels where syntax has not been locally tested. Exact Reaktoro Python syntax
must be verified during implementation.

## User-Editable Design Rule

- Prefer short modules and simple Python functions.
- Give each feature one clear config location, one clear execution module,
  and one clear output effect.
- Make optional features explicitly disableable.
- Use only minimal tests that directly protect the feature being added.
- Do not create large testing loops, broad test harnesses, or excessive test
  infrastructure.
- Add comments only for scientific, unit, or non-obvious Reaktoro decisions.
- Preserve working code and make targeted changes.

## Required Execution Chain

Maintain this simple execution chain as a design guide:

```text
YAML config
→ validation/preprocessing
→ PHREEQC database loading
→ chemical system construction
→ chemical state construction
→ selected kinetic model attachment
→ solver execution
→ diagnostics/postprocessing
```

This chain is not a reason to build complicated architecture. `runner.py`
must remain orchestration only. `runner.py` must not contain scientific logic.

## Forbidden Complexity

- No generic backend system.
- No plugin manager.
- No hidden registry.
- No abstract simulator architecture.
- No dynamic imports for core execution.
- No dependency injection container.
- No broad exception swallowing.
- No silent fallback.
- No random example configs.

## Before Adding Any Feature

1. Confirm the feature belongs to supported batch scope.
2. Identify the source-supported scientific inputs and units.
3. Add one clear YAML config field or block.
4. Add explicit validation and any deterministic preprocessing.
5. Add execution logic in the correct focused module, not `runner.py`.
6. Add diagnostics or output behavior if the feature changes results.
7. Add only minimal tests that directly protect the feature.
8. Update developer documentation.
9. Confirm the feature introduces no hidden default or silent fallback.

## Solver/output/config feature design files

Before modifying solver execution, timestep control, output writing, postprocessing, or the case-config schema, read these three design files together:

```text
docs/dev/output_package_design.md
docs/dev/solver_workflow_and_long_horizon_timestep.md
docs/dev/config_schema_feature_options.md
```

Use them as a coordinated implementation contract:

```text
output_package_design.md
→ defines what files are produced and what each file contains.

solver_workflow_and_long_horizon_timestep.md
→ defines how Reaktoro is executed, including workflow modes, CO₂/redox constraint staging, timestep control, rollback, checkpointing, and solver diagnostics.

config_schema_feature_options.md
→ defines the YAML schema, allowed values, validation rules, defaults, and feature flags.
```

Do not implement one of these areas in isolation if the change affects the others.

Hard rules:

```text
All optional features must be controlled from YAML.
Unknown config fields must fail validation.
Invalid config combinations must fail validation.
Do not modify Reaktoro internals.
Do not introduce plugin managers, backend factories, abstract simulator engines, dynamic imports, or silent fallbacks.
Do not let kinetics own duration or timestep control; solver.timestep owns time integration.
Checkpointing and restart are separate features.
CSV column order must be deterministic.
Output tables should report runtime results, not repeated copies of the input YAML.
```

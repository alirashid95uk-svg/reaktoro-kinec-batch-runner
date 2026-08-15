# Definitive UX and Application Architecture for the Reaktoro Batch Runner

## Document status

This document is the **final-target product, scientific-safety, data, runtime, and implementation contract** for upgrading the current Reaktoro simulation launcher into a complete scientific workbench.

It defines the final application. It is not a minimal viable product, temporary prototype, or reduced first release. The implementation must nevertheless follow the dependency order stated near the end of this document because later capabilities depend on stable contracts, schemas, and headless services.

The document does not authorise changes to thermodynamic data, kinetic parameters, geochemical equations, solver physics, scientific defaults, or Reaktoro behaviour unless a separate scientifically reviewed requirement explicitly approves them.

---

## 0. Authority, scope, and governing rules

### 0.1 Relationship to repository guidance

This workbench is an approved expansion of the **user-facing, orchestration, analysis, and reproducibility layer**. It does not relax the repository's scientific-simplicity rules.

The existing `batch_runner` remains a focused Reaktoro execution package with the visible chain:

```text
YAML case
→ strict validation and deterministic preprocessing
→ database and kinetic-parameter loading
→ mineral mapping
→ chemical-system and state construction
→ solver execution
→ diagnostics and output writing
```

The workbench may add rich editing, queueing, process control, result reading, comparison, parameter-study generation, dataset assembly, and reporting, but those capabilities must remain outside the scientific execution modules.

Before implementation begins, `AGENTS.md` must be updated with a short workbench-specific scope section so that repository guidance and this contract do not conflict. The update must preserve all existing prohibitions against:

- invented scientific values;
- silent fallback;
- automatic model switching;
- hidden scientific defaults;
- plugin managers;
- generic simulator backends;
- dependency-injection containers;
- unnecessary abstraction inside the scientific runner.

### 0.2 Sources of truth

The following precedence applies:

1. `batch_runner/config.py` and its focused tests define active runtime configuration behaviour.
2. `batch_runner` scientific modules and their focused tests define active execution behaviour.
3. The coordinated developer contracts define approved intended behaviour:
   - `docs/dev/config_schema_feature_options.md`;
   - `docs/dev/solver_workflow_and_long_horizon_timestep.md`;
   - `docs/dev/output_package_design.md`.
4. This document defines the approved workbench target and operational contracts.
5. `cases/schema_template.yaml`, runnable cases, README, workbench documentation, and screenshots must remain consistent with the active code.

When these sources disagree, Codex must identify and resolve the contradiction deliberately. It must not silently choose one source, rewrite scientific behaviour, or broaden the scope.

### 0.3 Core architectural rule

```text
The GUI is not the simulator.
The GUI is not the scientific analysis engine.
The GUI is a presentation and interaction layer over headless, testable services.
```

The runner must remain independently usable from the command line. Every simulation, comparison, study generation, dataset assembly, and report generation operation that changes a saved scientific artifact must also be reproducible through a headless command.

### 0.4 Primary safety invariants

```text
A simulation cannot execute without successful full preflight.

The scientific payload and dependencies that passed preflight are the
scientific payload and dependencies that execute.

Operational metadata may differ from the source case, but scientific fields
may not change after preflight.

Completed runs are never overwritten or edited in place.

Scientific inputs are never silently repaired.

The program never skips a configured mineral or missing kinetic record.

Partial trajectories are never represented as completed scientific results.

The runner remains usable without the workbench.

Failures retain raw technical evidence.

Derived artifacts remain traceable to their source runs and specifications.

Application convenience never overrides YAML scientific intent.
```

---

## 1. Executive decision

The project should become a **native Windows scientific workbench built with PySide6 and Qt Widgets**, while retaining the existing command-line runner as the authoritative simulation entry point.

The final system consists of three layers:

```text
PySide6 workbench
→ Qt-free workbench core
→ existing batch_runner and runner CLI
```

The workbench should provide:

- environment diagnosis;
- hybrid structured/YAML case editing;
- layered validation and preflight;
- immutable queue planning;
- isolated subprocess execution;
- cooperative cancellation and conservative recovery;
- artifact-derived run history;
- live numerical monitoring using already-produced information;
- interactive post-processing;
- scientifically controlled comparison;
- deterministic parameter studies;
- leakage-safe AI-dataset assembly;
- reproducible reports and exports.

It must not contain:

- automatic scientific parameter generation;
- automatic mineral substitution or skipping;
- automatic thermodynamic-database fallback;
- automatic kinetic-model switching;
- hidden scientific defaults;
- a general plugin framework;
- a generic simulator backend system;
- a general machine-learning training platform;
- reactive-transport, fracture-sealing, leakage, or geomechanical interpretation unsupported by the model;
- cloud dependence;
- scientific calculations duplicated in Qt-specific code.

The current Tkinter launcher is a successful proof of the execution boundary. It must remain available as a regression reference until the PySide6 workbench passes all replacement acceptance criteria.

---

## 2. Current launcher assessment

### 2.1 What the launcher already does correctly

| Existing capability | Required treatment |
|---|---|
| Full construction preflight | Preserve. The same preparation path used by runtime must remain authoritative. |
| Explicit `READY`, `BLOCKED`, and unchecked states | Preserve the meaning, but integrate them into the richer validation state machine. |
| Run gating | Preserve. No solver execution without full preflight. |
| Traceable run snapshot | Preserve and strengthen with explicit scientific and full-file fingerprints. |
| Source path and SHA-256 traceability | Preserve, while adding shareable-path privacy rules. |
| Fresh non-overwriting run directories | Preserve as a hard invariant. |
| Child-process execution | Preserve. Reaktoro execution must not occur in the GUI process. |
| Streamed process output and `launch_log.txt` | Preserve, but separate machine events from human logs. |
| Plain-language diagnosis | Preserve and move to a reusable Qt-free module. |
| Python/native crash capture and exit-code recording | Preserve and strengthen with controller-generated lifecycle records. |
| Sequential multi-case execution | Preserve as the default execution policy. |
| Direct access to outputs and diagnosis | Preserve and integrate into run history and result exploration. |

The preflight preparation sequence remains:

```text
configuration resolution
→ database loading
→ kinetic-parameter loading
→ exact mineral mapping
→ chemical-system construction
→ initial-state construction
```

The workbench must not replace this with a shallow GUI-only validator.

### 2.2 Current limitations

The existing launcher does not yet provide:

- case creation and structured editing;
- field-linked error navigation;
- systematic environment diagnosis;
- persistent run history;
- durable queue records;
- queue reordering and failure policy;
- cooperative cancellation;
- conservative recovery after interruption;
- structured live progress;
- interactive result exploration;
- multi-run compatibility checks and comparison;
- deterministic parameter-study generation;
- leakage-safe dataset assembly;
- reproducible comparison and dataset specifications;
- integrated reporting;
- complete accessibility coverage.

### 2.3 Current fragilities to correct

#### Blocking preflight

Preflight must never run synchronously on the GUI event thread. It must run through a worker process controlled by `QProcess`.

#### Hardcoded environment identity

The workbench must not depend on a fixed Conda environment name. It must store an explicitly selected solver environment path or solver launch command and verify that exact command.

#### Windows-specific behaviour

Windows remains the primary platform. OS-specific operations such as opening folders, process-tree termination, and bootstrap discovery must be isolated behind a focused platform service.

#### Validation staleness

A displayed validation state must become stale when the case, relevant dependency, code identity, schema, or solver environment changes.

#### Limited launcher tests

The current tests remain valuable but are insufficient for queue persistence, event protocols, cancellation, process-tree handling, output-schema compatibility, accessibility, or large result packages.

---

## 3. Product scope and non-goals

### 3.1 Supported final scope

The workbench supports the lifecycle of YAML-defined Reaktoro batch equilibrium and kinetic simulations:

```text
environment readiness
→ case authoring
→ scientific and runtime validation
→ immutable queue planning
→ isolated execution
→ diagnosis and recovery
→ run history
→ result exploration
→ comparison
→ study generation
→ dataset assembly
→ reproducible reporting
```

### 3.2 Scientific scope boundary

The workbench does not convert batch simulations into reactive-transport predictions.

Unless separately implemented and scientifically verified, it does not represent:

- advection or diffusion;
- fresh-brine renewal;
- multiphase flow;
- fracture transport;
- pressure evolution from a flow solver;
- porosity or permeability feedback;
- capillary-entry-pressure evolution;
- fracture aperture evolution;
- geomechanics;
- leakage flux;
- field-scale storage performance.

Any batch simulation reaching 10,000 years remains a configured batch kinetic trajectory, not a geological leakage forecast.

### 3.3 Explicit non-goals

The final application will not become:

- a thermodynamic database editor;
- a kinetic-parameter authoring system;
- a universal scientific workflow manager;
- a remote cluster scheduler;
- a cloud service;
- a notebook replacement;
- an ML training interface;
- a reactive-transport simulator;
- a general-purpose plotting application.

---

## 4. Users and core jobs

### 4.1 Primary researcher

A geochemical researcher who understands the scientific problem but should not need terminals, Conda syntax, Python traceback reading, or manual YAML editing for routine operation.

Core jobs:

- create source-supported cases;
- see all active assumptions and defaults;
- validate database, mineral, and kinetic compatibility;
- safely execute one or many cases;
- identify failures without changing scientific values blindly;
- inspect geochemical and numerical behaviour;
- compare cases under explicit compatibility rules;
- generate traceable studies and AI datasets.

### 4.2 Scientific reviewer or supervisor

Core jobs:

- inspect assumptions, sources, and exact inputs;
- understand run completion and limitations;
- review major geochemical changes;
- distinguish computed output from interpretation;
- compare cases;
- receive a self-contained report with provenance.

### 4.3 Parameter-study operator

Core jobs:

- define a constrained deterministic design;
- generate cases from a verified baseline;
- validate all samples;
- manage blocked, failed, partial, and completed samples;
- prevent duplicates;
- consolidate quality-controlled results;
- preserve method, seed, constraints, and validity domain.

### 4.4 Software maintainer and auditor

Core jobs:

- reproduce all operations from CLI;
- inspect process commands and event records;
- rebuild run history from artifacts;
- verify schema and output compatibility;
- test GUI/runner equivalence;
- confirm that Qt-specific code contains no scientific logic.

---

## 5. End-to-end workflow

```text
Application launch
    ↓
Workbench environment check
    ↓
Solver environment and command verification
    ├─ Python and Reaktoro import through the exact launch command
    ├─ package and environment identity
    ├─ databases and kinetic parameter files
    ├─ project and run-directory permissions
    ├─ disk availability
    └─ code and schema identity
    ↓
Case library
    ├─ existing project YAML
    ├─ external YAML import
    ├─ duplicate a verified case
    └─ create from the non-runnable schema template
    ↓
Hybrid case editor
    ├─ curated structured forms
    ├─ round-trip YAML source
    ├─ units and provenance
    ├─ conditional fields
    ├─ explicit defaults and derived values
    └─ atomic save and conflict handling
    ↓
Layered validation
    ├─ YAML syntax
    ├─ placeholder rejection
    ├─ strict Pydantic schema
    ├─ cross-field consistency
    ├─ path and dependency resolution
    ├─ database and kinetic loading
    ├─ mineral mapping
    ├─ system and initial-state construction
    ├─ workload and output checks
    └─ validation receipt and scientific fingerprint
    ↓
Run plan
    ├─ immutable final snapshots
    ├─ queue order
    ├─ duplicate warning
    ├─ execution capability and worker policy
    ├─ failure policy
    └─ exact scientific and operational review
    ↓
Execution
    ├─ isolated solver processes
    ├─ versioned machine events
    ├─ human-readable stderr logs
    ├─ cooperative cancellation
    ├─ force termination when necessary
    └─ partial-result preservation when possible
    ↓
Run history
    ├─ completed
    ├─ chemistry completed / output incomplete
    ├─ partial numerical failure
    ├─ cancelled cleanly
    ├─ force terminated
    ├─ blocked before execution
    ├─ native crash
    └─ interrupted or indeterminate
    ↓
Scientific review
    ├─ completion and provenance
    ├─ accepted trajectories
    ├─ scientific summaries
    ├─ numerical behaviour
    ├─ target audit
    └─ raw evidence
    ↓
Comparison or study assembly
    ├─ schema and quantity compatibility
    ├─ native-grid comparison
    ├─ final-state comparison
    ├─ explicit derived alignment where allowed
    ├─ QC and exclusions
    └─ saved comparison or study specification
    ↓
Export
    ├─ YAML / JSON / CSV / Parquet
    ├─ PNG / SVG
    ├─ Markdown / HTML / PDF
    ├─ notebook-compatible scripts
    └─ leakage-safe AI dataset package
```

---

## 6. Information architecture

The application uses **seven permanent workspaces**.

| Workspace | Responsibility | Explicit exclusion |
|---|---|---|
| **Home and Environment** | Workbench and solver-environment readiness, project status, recent activity, unresolved operational problems | Scientific parameter editing |
| **Cases** | Case library, creation, editing, provenance, validation | Solver execution logic |
| **Queue** | Immutable run planning, order, process policy, execution control | Editing queued snapshots |
| **Runs** | Artifact-derived run history, status, diagnosis, provenance, completeness | Modifying completed artifacts |
| **Explore** | Single-run scientific and numerical review | Editing source cases |
| **Compare** | Compatible multi-run analysis and saved comparison specifications | Parameter generation and ML training |
| **Studies** | Deterministic study definition, generated cases, QC, dataset assembly | General ML modelling |

### 6.1 Navigation

```text
Home
Cases
Queue
Runs
Explore
Compare
Studies
```

The Cases workspace uses contextual navigation:

```text
Overview
Physical and Brine
CO₂ and Redox
Minerals and Kinetics
Solver
Post-processing
Validation Targets
Outputs
YAML
Validation
```

A long wizard must not be the primary interaction model because scientific cases are edited non-linearly.

### 6.2 Progressive disclosure

Each case section presents:

```text
core fields
→ conditional fields
→ advanced numerical controls
→ source and provenance details
→ resolved-value preview
→ raw YAML
```

Advanced controls remain discoverable and keyboard accessible, but they do not occupy the primary view permanently.

### 6.3 State vocabularies

Case, validation, queue, and run states are distinct. They must not be collapsed into one generic status field.

#### Case document state

```text
clean
modified
invalid_yaml
schema_invalid
external_conflict
archived
```

#### Validation state

```text
not_checked
checking
ready
blocked
stale
validation_process_failed
```

#### Queue-entry state

```text
planned
queued
starting
running
pause_after_current_requested
cancel_after_current_requested
cancelled_before_start
finished
```

#### Run termination state

```text
blocked_preflight
completed
chemistry_completed_output_incomplete
partial_numerical_failure
solver_failure_at_start
cancelled_cleanly
cancel_requested_solver_unresponsive
force_terminated
native_crash
controller_failure
interrupted_by_host
indeterminate
```

Every state must have text, an icon, and accessible metadata. Colour alone is prohibited.

---

## 7. Case document and hybrid-editor contract

### 7.1 Document authority

The **round-trip YAML document** is the editable case-document authority.

The Pydantic model is the validation and semantic authority. Qt forms are interaction views over the round-trip document. No widget tree becomes an independent source of scientific truth.

```text
round-trip YAML document
↕ transactional path-level edits
curated Qt forms

round-trip YAML document
→ parse and validate
→ Pydantic semantic model and resolved preview
```

### 7.2 Synchronisation rules

1. Form changes patch specific YAML paths transactionally.
2. Raw YAML changes are parsed only when the user explicitly applies or saves them, or after a debounced non-destructive syntax check.
3. A YAML parse or schema failure preserves the last valid form state but marks it stale and read-only until the text is valid again.
4. Switching from raw YAML to structured forms requires successful parsing.
5. Form changes must not rewrite the complete document unless the user explicitly requests canonical formatting.
6. Pydantic-injected defaults appear as resolved values. They are not silently written into source YAML.
7. Conditional fields disabled by another setting remain preserved in the unsaved document only when their presence is schema-valid. Otherwise, changing the controlling option requires an explicit review of fields that will be removed.
8. List identity must not rely only on row position. Mineral rows use their exact configured mineral name as the document identity while duplicates remain invalid.
9. Undo and redo operate at document level across both form and YAML changes.
10. Save uses an atomic temporary-file replacement.
11. External modifications trigger conflict handling. The application must not overwrite an externally changed source file silently.
12. The original file is never modified merely by importing or previewing it.

### 7.3 Comment and formatting preservation

Use `ruamel.yaml` or another proven round-trip parser to preserve, where technically possible:

- comments;
- mapping order;
- list order;
- quoting style;
- multiline scalars;
- user-written provenance text.

Comments are not a replacement for structured provenance fields when runtime traceability requires them.

### 7.4 Form-generation boundary

Pydantic JSON Schema may provide:

- data types;
- required fields;
- allowed literals;
- basic ranges;
- descriptions;
- path identifiers.

The complete GUI must not be blindly generated from JSON Schema. Complex sections require curated widgets because scientific meaning, conditional legality, unit presentation, provenance, and table editing cannot be expressed safely through a generic form generator alone.

### 7.5 Save and revision behaviour

A successful save produces:

- exact saved bytes;
- source-file SHA-256;
- document status `clean`;
- validation status `stale` or `not_checked` unless the saved content exactly matches an existing valid receipt;
- optional local revision metadata in the workbench cache.

A “committed case revision” means a saved file identified by its path and SHA-256. The workbench does not need a hidden document-version database. A queued run obtains its own immutable snapshot.

### 7.6 Scientific field assistance

The editor may provide:

- field definitions;
- accepted units;
- allowed categorical values;
- dependency explanations;
- resolved-path previews;
- database species search based on the selected database;
- kinetic-record availability display;
- source/provenance entry fields;
- links to project documentation.

It must not provide unsupported numerical recommendations or silently create values.

---

## 8. Scientific configuration and unit integrity

### 8.1 Single schema authority

`CaseConfig`, `load_case`, and deterministic preprocessing remain authoritative. GUI convenience checks may identify obvious errors early, but successful GUI checks never replace full authoritative validation.

Unknown fields and invalid combinations must continue to fail.

### 8.2 No automatic scientific repair

The application must never automatically:

- change a mineral role;
- skip a mineral;
- substitute a mineral or species;
- edit a kinetic parameter file;
- switch kinetic models;
- choose another database;
- invent a surface area;
- change a numerical acceptance tolerance;
- insert a brine composition;
- add stoichiometric mappings;
- change a CO₂ mode;
- infer a provenance source;
- modify solver workflow to make a case execute.

A suggested corrective action may navigate to the relevant field or evidence. It may not apply a scientific change without explicit user editing.

### 8.3 Value-origin classification

Every displayed scientific or operational value is classified as one of:

```text
explicit source input
approved software default
deterministically derived value
resolved file lookup
runtime result
operational metadata
```

Defaults must be visible. A default path or model may be displayed as resolved, but the interface must not present it as explicitly entered by the user.

### 8.4 Units

Every numerical scientific field requires:

- a visible unit or explicit dimensionless designation;
- a validated accepted-unit set;
- a canonical runtime unit;
- a conversion preview where multiple units are accepted;
- retention of the entered value and unit in the source document;
- retention of the resolved canonical value in resolved configuration or provenance.

Axis-unit conversion and display conversion must not change stored scientific output.

### 8.5 Provenance fields

The workbench exposes existing structured provenance fields. New source-dependent values require a schema change if no suitable provenance field exists.

The interface must distinguish:

- bibliographic source;
- deterministic derivation;
- user decision;
- software default;
- unsupported or missing provenance.

---

## 9. Validation, snapshots, and fingerprint contracts

### 9.1 Validation layers

Validation is reported by layer:

| Layer | Examples |
|---|---|
| Document syntax | YAML parsing, duplicate-key policy, unresolved merge conflict |
| Template sentinel | `REQUIRED`, `TBD_SOURCE_REQUIRED`, or related placeholders in runnable cases |
| Schema | unknown field, type error, mode-specific field, range error |
| Cross-field consistency | CO₂/workflow mismatch, adaptive mode without kinetics, invalid redox staging |
| File and environment | missing database, missing kinetic file, unreadable path, incompatible environment |
| Scientific compatibility | missing thermodynamic mineral, kinetic record, or surface area |
| Construction | database loading, system construction, initial-state construction |
| Operational readiness | fresh output path, writable directory, queue policy, disk availability |
| Scale advisory | estimated internal steps, result rows, checkpoint count, approximate output volume |

Scale advisories are not scientific validity judgements. Estimates must be labelled approximate.

### 9.2 Validation receipt

A successful full preflight writes an immutable, versioned `validation_receipt.json` containing:

```text
receipt_schema_version
receipt_id
created_at_utc
case_name
snapshot_sha256
scientific_fingerprint
operational_fingerprint
configuration_schema_version
runner_version
worker_protocol_version
solver_environment_identity
code_identity
dependency_hashes
preflight_stage_results
kinetic_mapping_summary
ready: true
```

A failed preflight may also write a receipt with `ready: false`, exact failed stage, errors, warnings, mapping evidence, and process outcome.

### 9.3 Exact execution semantics

The specification does **not** require the source case bytes to be identical to the final run snapshot because the run snapshot contains a fresh output path and operational metadata.

The correct invariant is:

```text
The exact scientific payload and dependency fingerprint that passed
preflight must execute.
```

Two hashes are mandatory:

| Identifier | Definition |
|---|---|
| `snapshot_sha256` | SHA-256 of the exact final `run_case.yaml` bytes, including the run output directory |
| `scientific_fingerprint` | Hash of canonical resolved scientific and numerical configuration, excluding approved operational fields, plus scientific dependency and code identity |

An optional `operational_fingerprint` covers output path, run ID, queue ID, logging configuration, and process-control metadata.

Immediately before launch, the controller verifies:

- the final snapshot SHA-256;
- the scientific fingerprint;
- database and kinetic hashes;
- schema and code identity;
- solver-environment identity.

Any mismatch invalidates the receipt and blocks execution.

### 9.4 Canonical scientific fingerprint

The fingerprint input must be documented and versioned. It includes:

- the fully resolved scientific and numerical configuration;
- approved defaults after resolution;
- canonical units;
- thermodynamic database identity;
- kinetic parameter identity;
- relevant scientific auxiliary-file hashes;
- runner scientific code identity;
- configuration schema version.

It excludes:

- output directory;
- run directory;
- timestamps;
- run, queue, or study IDs;
- log paths;
- display preferences;
- report preferences that do not change simulation output.

### 9.5 Staleness detection

A validation receipt becomes stale if any fingerprint input changes, including external changes to the source case, database, kinetic file, runner code, configuration schema, or solver environment.

File watchers may provide immediate notification, but authoritative staleness is determined by recomputing the fingerprints before queueing and execution.

---

## 10. Environment and distribution architecture

### 10.1 Separate environments

The workbench environment and solver environment must be separated.

#### Workbench environment

```text
Python compatible with the selected PySide6 release
PySide6
PyQtGraph
ruamel.yaml
pandas
PyArrow
reporting dependencies
workbench application and headless core
```

#### Solver environment

```text
Python 3.11
Reaktoro 2.13
Pydantic 2
PyYAML
Matplotlib
pytest
existing scientific dependencies
```

The verified solver environment must not be burdened with the entire GUI stack unless a later controlled test demonstrates that one environment is equally reproducible and stable.

### 10.2 Solver launch command

The workbench stores an explicit solver launch specification rather than only an environment name.

Preferred form:

```text
conda run --no-capture-output -p <solver_environment_path> python runner.py ...
```

Directly launching `<environment>\python.exe` is permitted only after Windows DLL loading and Reaktoro import are verified using that exact command.

The Environment Doctor must test the command actually used for simulations.

### 10.3 Environment identity

Record:

- selected solver environment path as private operational metadata;
- Python version;
- Reaktoro version;
- package inventory;
- explicit environment export or lock file when available;
- hashes of the inventory and environment specification;
- launch-command form;
- successful import and smoke-check outcome.

A package-list hash alone is not a reproducible environment description.

### 10.4 Environment Doctor

The Environment Doctor verifies:

- workbench dependencies;
- selected solver launch command;
- Reaktoro import through that command;
- Python and Reaktoro versions;
- project root;
- runner availability;
- database and kinetic files;
- run-directory permissions;
- file-system write and atomic-replace support;
- available disk space;
- code and schema identity;
- optional Git status;
- platform-specific process-control capability.

It diagnoses but does not silently install, update, repair, or switch environments.

### 10.5 Bootstrap

The user-facing launch remains one-click:

```text
desktop shortcut or launch script
→ start the workbench environment
→ open the workbench
→ verify the configured solver environment
```

The bootstrap may offer an explicit environment-creation or repair command, but it must show what will change and require confirmation.

### 10.6 Packaging decision

A single-file executable is not the primary distribution target because Reaktoro and its native dependencies are better controlled through Conda.

The deliverable is:

- a pinned workbench environment;
- a pinned solver environment;
- a lightweight Windows bootstrap;
- explicit environment verification;
- documented recovery commands.

---

## 11. Technology decision

### 11.1 Selected stack

```text
PySide6
Qt Widgets
QProcess
Qt model/view
PyQtGraph for interactive display
Matplotlib for runner-generated canonical static plots
Pydantic as authoritative runtime schema
ruamel.yaml for round-trip YAML editing
pandas for tabular analysis
PyArrow for Parquet
SQLite as a rebuildable run-index cache
versioned JSONL worker protocol
```

Use pandas rather than introducing a pandas/Polars choice. One tabular stack is sufficient and easier for a single researcher to maintain.

### 11.2 Why PySide6 and Qt Widgets

The application is dominated by:

- scientific forms;
- trees and tables;
- queue and run-state models;
- logs and inspectors;
- keyboard navigation;
- local process control;
- interactive plots;
- high-DPI Windows use.

PySide6 provides mature widgets, process signals, model/view separation, native desktop behaviour, accessibility integration, and one primary programming language.

Qt Widgets is preferred over QML because the interface is a dense scientific desktop application rather than an animated consumer interface.

### 11.3 Plotting split

PyQtGraph is used for responsive interactive viewing. Matplotlib remains the runner's canonical static-output system.

A PyQtGraph view or SVG generated from a user-selected comparison is a **derived workbench artifact**, not a replacement for the original Matplotlib output.

### 11.4 Rejected primary frameworks

Tkinter remains useful as a regression launcher but is insufficient for the final workbench's model/view, accessibility, table, and plotting requirements.

Streamlit, Dash, Panel, and NiceGUI introduce a local web-server lifecycle and weaker desktop process ownership.

Tauri introduces Rust, JavaScript, and Python sidecars without solving a requirement that Qt cannot solve directly.

Jupyter remains a follow-on analysis and export target, not the authoritative queue or run-control application.

---

## 12. Application and package architecture

### 12.1 Layered architecture

```text
┌──────────────────────────────────────────────┐
│ PySide6 workbench                            │
│                                              │
│ Views, Qt models, widgets, accessibility     │
└───────────────────┬──────────────────────────┘
                    │ calls
                    ▼
┌──────────────────────────────────────────────┐
│ Qt-free workbench core                       │
│                                              │
│ document operations                         │
│ queue and run records                        │
│ protocol parsing                             │
│ result readers and schema adapters           │
│ comparisons                                  │
│ studies and case generation                  │
│ dataset assembly                             │
│ reports                                      │
└──────────────┬────────────────┬──────────────┘
               │ CLI            │ process control
               ▼                ▼
       workbench_cli.py       runner.py
                                  │
                                  ▼
┌──────────────────────────────────────────────┐
│ Existing batch_runner scientific execution  │
│                                              │
│ config → database → system/state → solver    │
│ → diagnostics → outputs                      │
└──────────────────────────────────────────────┘
```

### 12.2 Package boundary

```text
workbench_core/
├── schemas/
│   ├── protocol.py
│   ├── validation_receipt.py
│   ├── queue_record.py
│   ├── run_record.py
│   ├── comparison_spec.py
│   ├── study_spec.py
│   └── dataset_manifest.py
├── documents.py
├── fingerprints.py
├── run_records.py
├── queue_records.py
├── protocol_reader.py
├── result_readers.py
├── schema_adapters.py
├── comparison.py
├── studies.py
├── datasets.py
└── reports.py

workbench/
├── app.py
├── models/
├── views/
├── widgets/
├── controllers/
├── services/
│   ├── environment.py
│   ├── processes.py
│   ├── platform_windows.py
│   └── run_index.py
└── resources/

batch_runner/
├── existing scientific execution
└── protocol_events.py

runner.py
workbench_cli.py
```

### 12.3 Import rules

- No Qt import may appear inside `batch_runner`.
- No Qt import may appear inside `workbench_core`.
- `workbench` may depend on `workbench_core`.
- `workbench_core` may read runner artifacts but must not duplicate Reaktoro calculations.
- `batch_runner` must not depend on `workbench` or `workbench_core` except for a narrowly shared schema module only if circular dependency is avoided; otherwise protocol schemas remain in a separate neutral package.

### 12.4 Scientific logic boundary

The workbench may:

- edit case documents;
- invoke authoritative validation;
- display resolved values;
- schedule solver processes;
- parse output artifacts;
- perform explicitly defined post-run comparisons;
- generate constrained case studies;
- assemble traceable datasets;
- create derived reports.

It must not:

- construct Reaktoro systems in the GUI process;
- attach kinetic models;
- calculate reaction rates independently of saved runner outputs;
- decide accepted timesteps;
- generate scientific result rows independently;
- reinterpret failed outputs as successful;
- alter thermodynamic or kinetic source files.

---

## 13. Worker protocol and live-progress contract

### 13.1 Channel separation

The process channels are unambiguous:

```text
stdout = machine-readable JSONL protocol events only
stderr = human-readable logs and technical diagnostics
files  = durable scientific results, records, and logs
```

The controller mirrors stderr to `launch_log.txt`. It records stdout machine events to `worker_events.jsonl` after validating or safely retaining each line.

Human-readable and machine-readable content must not share stdout.

### 13.2 Event ownership

The worker emits scientific and execution-stage events. The controller emits process-lifecycle events that a crashed worker cannot reliably emit.

#### Worker-owned events

```text
worker_ready
environment_verified
stage_started
stage_completed
validation_issue
mapping_result
simulation_started
progress_summary
checkpoint_written
warning
output_written
simulation_finished
worker_failure_reported
```

#### Controller-owned events

```text
process_created
process_started
cancel_signal_sent
terminate_sent
kill_sent
process_exited
protocol_error
controller_error
```

A worker must not be expected to emit `worker_exited` after a native crash.

### 13.3 Event envelope

Every event contains:

```text
protocol_version
event_type
timestamp_utc
run_id
case_id
sequence_number
producer
payload
```

Rules:

- `sequence_number` begins at 1 separately for each producer.
- Each JSON event occupies one line and is flushed immediately.
- Unknown event types or newer protocol versions are retained as raw records and reported as unsupported, not discarded.
- A malformed or truncated final line creates a protocol error but does not invalidate prior events.
- Protocol events are operational observations, not authoritative scientific outputs.

### 13.4 Progress throttling

The full solver-attempt history remains in `solver_history.csv` or its durable staging stream.

Live events must be throttled or aggregated so that the GUI does not materially affect performance. A progress event may include:

- accepted simulation time;
- requested duration;
- current or last attempted timestep;
- accepted and rejected attempt counts;
- latest acceptance or rejection reason;
- solver iterations where available;
- current stage;
- configured low-cost values already extracted for output.

The workbench must not request arbitrary extra Reaktoro property calculations for decorative monitoring.

### 13.5 CLI compatibility

The existing invocation remains valid:

```text
python runner.py <case.yaml>
python runner.py --preflight <case.yaml>
```

Structured events are introduced through backward-compatible options such as:

```text
python runner.py <case.yaml> --events-jsonl
python runner.py --preflight <case.yaml> --events-jsonl
```

Exact argument names may differ after repository inspection, but existing commands and exit-code semantics must remain operational.

---

## 14. Queue, run records, and persistence

### 14.1 Operational artifacts

Each run directory contains records outside the scientific result package:

```text
runs/<case_slug>/<run_id>/
├── run_case.yaml
├── run_record.json
├── validation_receipt.json
├── worker_events.jsonl
├── launch_log.txt
├── diagnosis.txt
└── results/
```

Workbench operational state is stored under:

```text
.workbench/
├── settings.json
├── queues/
│   └── <queue_id>.json
└── run_index.sqlite
```

The result directory remains controlled by the runner's output contract.

### 14.2 Run identity

Use a UUID or ULID for `run_id`. Timestamps may appear in folder names for readability but cannot be the sole identity.

Every run records:

- `run_id`;
- source case identity;
- final snapshot hash;
- scientific fingerprint;
- queue and study identity where applicable;
- current lifecycle state;
- controller process metadata;
- child process metadata;
- validation receipt reference;
- result-package location;
- termination category;
- output completeness;
- timestamps.

### 14.3 Run record state transitions

The permitted run lifecycle is explicit. Example transitions:

```text
created
→ validating
→ blocked_preflight

created
→ validating
→ ready
→ starting
→ running
→ completed

running
→ partial_numerical_failure
running
→ cancelled_cleanly
running
→ cancel_requested_solver_unresponsive
running
→ force_terminated
running
→ native_crash
running
→ chemistry_completed_output_incomplete
running
→ interrupted_by_host
```

Invalid transitions fail and generate a controller error. A completed run cannot return to running.

### 14.4 Queue record

A versioned queue record contains:

```text
queue_schema_version
queue_id
created_at_utc
updated_at_utc
failure_policy
worker_policy
queue_state
entries
```

Each entry contains:

```text
entry_id
order
run_id
snapshot_path
snapshot_sha256
scientific_fingerprint
validation_receipt_id
entry_state
status_reason
```

Queue entries reference final immutable snapshots. Editing the source case does not mutate the queue.

### 14.5 Queue failure policy

Supported policies:

```text
stop_after_failure
continue_after_failure
pause_for_decision
```

The policy applies only after the current worker has terminated and its run record has been classified.

### 14.6 Persistence and atomicity

- Mutable JSON records are written to a temporary file and replaced atomically.
- JSONL event files are append-only.
- Queue records have one controlling writer.
- Run records use controlled state transitions.
- A file lock or equivalent single-instance rule prevents two workbench instances from controlling the same queue.
- The SQLite index is a rebuildable cache and never the source of truth.
- Deleting or corrupting the index must not lose run evidence.

### 14.7 Run index

SQLite is the required internal search cache for the final workbench, not an alternative source of truth.

It stores searchable projections of:

- run ID and path;
- case name;
- scientific fingerprint;
- status and completeness;
- times;
- kinetic model and workflow;
- output schema;
- warnings;
- study ID;
- available artifact groups.

The application must provide a rebuild operation that scans run records and result manifests.

---

## 15. Queue execution and concurrency

### 15.1 Default execution policy

Sequential simulation-level execution remains the default.

The workbench must not imply that one Reaktoro simulation is accelerated by assigning multiple workers to it. Each worker process owns one simulation case.

### 15.2 Parallel execution capability

Process-level concurrency may be enabled only when the repository has focused Windows tests demonstrating:

- independent output directories;
- read-only shared scientific inputs;
- stable Reaktoro process behaviour;
- acceptable memory use;
- safe logging and event streams;
- safe use of the selected kinetic model.

Custom Kinec cases remain sequential until concurrent process execution is specifically verified, including the existing Python callback finalisation behaviour.

### 15.3 Worker limits

The user explicitly configures the maximum number of workers. The software may display CPU and memory information but must not silently choose a concurrency level.

The configured value is capped by a documented hard limit. Resource estimates remain advisory.

### 15.4 Duplicate-run detection

The queue compares scientific fingerprints against:

- queued entries;
- active runs;
- completed runs;
- study samples.

A duplicate generates a warning, not an automatic deletion. The user may execute a replicate deliberately. The run record then records the replicate relationship.

---

## 16. Cancellation, termination, and application closure

### 16.1 Cancellation is cooperative

Graceful cancellation is not guaranteed while Reaktoro is blocked inside a native solver call.

The cancellation token is checked:

- before each solver attempt;
- after each solver attempt;
- before expensive output extraction;
- before starting the next queued case.

If control returns to Python and output writing succeeds, accepted-state evidence and diagnostics are finalised. If the native solver does not return, force termination is the only available control.

### 16.2 Queue pause

```text
Pause queue
= do not start another queued run after the current process terminates.
```

It does not pause an active solver call.

### 16.3 Cancel after current

```text
Cancel after current
= allow the active run to terminate normally, then mark remaining
pending entries cancelled before start.
```

### 16.4 Graceful cancellation

```text
Graceful cancel current
= send a cooperative cancellation request and wait for a verified
safe boundary.
```

Possible outcomes:

```text
cancelled_cleanly
cancel_requested_solver_unresponsive
interrupted_during_output
```

### 16.5 Force termination

Force termination:

1. records controller intent;
2. sends terminate where appropriate;
3. waits a bounded operational interval;
4. escalates to kill;
5. terminates child descendants when required by Windows process behaviour;
6. preserves flushed files;
7. writes controller-derived interruption evidence;
8. never labels the result scientifically complete.

### 16.6 Application closure

The final workbench does **not** promise that workers continue after the GUI closes.

While active workers exist, closing offers:

```text
Return to application
Cancel after current and close when idle
Request graceful cancellation
Force terminate and close
```

The option “keep workers running and close UI” is excluded because it would require a separate persistent supervisor or Windows service, adding unjustified operational complexity.

### 16.7 Custom Kinec process finalisation

The current custom Kinec Windows workaround using immediate process exit after outputs are closed must be preserved until a verified replacement exists.

The controller must therefore determine process completion from:

- durable result diagnostics;
- output completeness;
- child exit code;
- process events.

It must not depend on Python interpreter shutdown hooks from the worker.

---

## 17. Recovery after interruption

### 17.1 Conservative recovery rule

The workbench never resumes chemical solver state automatically unless restart reconstruction is separately implemented, scientifically validated, and enabled by the active schema.

Checkpoints are evidence, not automatic restart points.

### 17.2 Startup recovery scan

At startup, the workbench scans queue and run records.

For records previously marked active, it:

1. checks durable diagnostics and output files;
2. examines the controller and worker event sequence;
3. checks whether a workbench-owned process handle still exists in the current session;
4. classifies the run as completed, partial, interrupted, force terminated, or indeterminate;
5. never assumes a PID alone identifies the original worker;
6. never automatically resumes the solver.

A process from a previous workbench session is not treated as safely controllable merely because the same PID exists.

### 17.3 Disk and output failures

Before launch, verify:

- output parent exists or can be created;
- final output directory does not exist;
- write and atomic-replace tests pass;
- approximate free-space check is not obviously inadequate;
- path length and Windows naming constraints are satisfied.

If chemistry completes but output writing fails, preserve:

```text
simulation_completed = true
output_completeness = partial
termination = chemistry_completed_output_incomplete
```

Do not collapse this into a generic failure.

---

## 18. Provenance and privacy

### 18.1 Expanded provenance

Each run records:

```text
run_id
source case logical identity
source case SHA-256
final snapshot SHA-256
scientific fingerprint
operational fingerprint
database identity and hash where available
kinetic model and parameter-file hash
runner code identity
Git commit and dirty-state evidence
configuration-schema version
output-schema version
worker-protocol version
workbench version
Python and Reaktoro versions
environment specification and package inventory
queue ID
study and sample ID where applicable
termination category
cancellation mode
exact output inventory
```

### 18.2 Dirty-code identity

A Git commit plus `dirty: true` is insufficient. When the repository is dirty, record one of:

- a deterministic relevant-source-tree hash;
- hashes of the relevant Python and configuration files;
- or the Git diff plus an untracked-file manifest.

The chosen method must be documented and stable.

### 18.3 Embedded databases

For embedded databases, record:

- database name;
- Reaktoro version and package/build identity;
- resource hash when technically accessible;
- a statement when a direct file hash is unavailable.

Do not invent a filesystem path.

### 18.4 Path privacy

| Artifact | Path policy |
|---|---|
| Private run record | Full resolved paths allowed |
| Shareable report | Prefer project-relative or redacted paths |
| Dataset manifest | Use logical identifiers and hashes; avoid personal absolute paths |
| Debug artifacts | Full paths allowed when explicitly included |

A host identifier is not collected unless a concrete reproducibility requirement is approved. Usernames and machine-specific paths must not appear in shareable exports by default.

---

## 19. Run history and diagnosis

### 19.1 Artifact-derived history

Run history is rebuilt from:

- `run_record.json`;
- `validation_receipt.json`;
- `results/manifest.json` when present;
- `results/diagnostics.json` when present;
- controller and worker events;
- output inventory.

A blocked or crashed run may have no result manifest. `run_record.json` is therefore the operational authority for run existence and termination classification.

### 19.2 Diagnosis presentation

The diagnosis view presents:

1. exact outcome category;
2. whether the solver started;
3. failed lifecycle stage;
4. requested and last accepted simulation time;
5. accepted and rejected attempts;
6. output completeness;
7. plain-language issue;
8. mineral mapping failures;
9. safe evidence-based actions;
10. expandable technical evidence.

Technical evidence includes:

- stderr log;
- exit code;
- controller events;
- worker events;
- traceback;
- diagnostics;
- solver history;
- mapping records.

No corrective action changes scientific inputs automatically.

### 19.3 Target-audit terminology

Until a verified experimental validation workflow exists, use:

```text
validation target
configured reference value
target audit
model–target difference
```

Do not use:

```text
experiment passed
model validated
calibration successful
scientifically accepted
```

A future validation result requires an explicit matching rule, metric, tolerance, units, uncertainty treatment, source, and test coverage.

---

## 20. Single-run scientific exploration

### 20.1 Overview questions

The first run view must answer:

```text
Did the simulation and output package complete?
What exact system and workflow were executed?
What changed scientifically?
Were numerical warnings or rejected steps present?
Which target, conservation, and audit outputs are available?
Where is the raw evidence?
```

Overview groups include:

- run and output completeness;
- requested and reached duration;
- accepted and rejected attempts;
- kinetic model and solver workflow;
- database and parameter identities;
- major warnings;
- target-audit availability;
- available result groups;
- scientific-scope warning.

### 20.2 Result groups

| Group | Supported quantities |
|---|---|
| Aqueous state | pH, ionic strength, requested species amounts and molalities |
| Minerals | amount, absolute change, percentage change where defined |
| Saturation | configured mineral saturation indices |
| Kinetics | saved reaction rates and rate-validation measures |
| Inventories | carbon inventory and element budgets when explicitly configured |
| Numerical | timestep, iterations, accepted/rejected attempts, wall time, acceptance reasons |
| Target audit | saved model values, configured reference values, units, uncertainty, and difference |
| Derived audits | volume change, surface area, regime classifications, and other explicitly implemented outputs |

The workbench reads saved outputs. It does not recalculate Reaktoro properties independently.

### 20.3 Interactive controls

- variable search and selection;
- show/hide series;
- zoom and pan;
- exact cursor values;
- accessible tabular equivalent;
- time-unit conversion;
- native accepted-state display;
- rejected-attempt markers on numerical plots;
- copy selected numerical values;
- export visible data;
- save a derived PNG or SVG;
- return to canonical view.

### 20.4 Axis safety

Axis options depend on variable class.

Logarithmic y-axis is disabled for:

- pH;
- saturation index;
- signed mineral change;
- reaction rates containing zero or negative values;
- any selected series containing non-positive values.

A logarithmic time axis is permitted only when all plotted times are positive. Time zero is omitted or handled by an explicit supported transformation; it must not be silently shifted.

### 20.5 Zero-initial-value handling

```text
initial amount > 0
→ absolute and percentage change are available

initial amount = 0 and final amount > 0
→ precipitation from zero
→ percentage change is undefined

initial amount = 0 and final amount = 0
→ unchanged zero
→ percentage change is undefined
```

The interface must not display zero, infinity, or an arbitrary percentage for undefined change.

### 20.6 Partial-run handling

A partial trajectory shows:

- a persistent incomplete-state banner;
- the requested duration;
- the last accepted time;
- accepted states only;
- the failure event in numerical views;
- separate rejected attempts;
- no implied continuation;
- no final scientific summary unless labelled “last accepted state.”

Incomplete runs are excluded from valid scientific dataset assembly by default and may enter only a separate failure ledger.

---

## 21. Output-schema compatibility and result readers

### 21.1 Immutable source packages

Old result packages are never modified in place by the workbench.

### 21.2 Version adapters

Each supported output-schema version has a read-only adapter that maps known quantities into a documented internal representation.

Rules:

- unsupported versions remain visible as raw artifacts;
- unknown columns are retained for inspection but not interpreted automatically;
- quantity identity and units must be explicit;
- migration creates a new derived package and preserves the original;
- dataset manifests record the source schema version for every run;
- comparisons are allowed only after explicit compatibility checks.

### 21.3 Internal quantity descriptor

A comparable quantity requires:

```text
quantity_id
human label
scientific meaning
unit
value type
sign domain
extensive or intensive classification
time semantics
source file and column
source output-schema version
```

Column-name similarity alone is not proof of compatibility.

---

## 22. Multi-run comparison contract

### 22.1 Compatibility gate

Before comparison, evaluate:

- output-schema support;
- quantity identity;
- units;
- completion status;
- time semantics;
- native time-domain overlap;
- scientific fingerprint differences;
- available source artifacts.

The application shows input and provenance differences before inviting interpretation of output differences.

### 22.2 Default comparison modes

The safe defaults are:

```text
native accepted grids
exact common timestamps within an explicit tolerance
initial-state comparison
final-state comparison for runs that reached the required endpoint
```

### 22.3 Interpolation rules

Interpolation is:

- disabled by default;
- display-only unless explicitly exported as a derived comparison artifact;
- prohibited outside overlapping domains;
- configured per variable class;
- recorded in a versioned comparison specification;
- labelled as derived;
- prohibited silently in AI dataset assembly.

General-purpose linear interpolation across every column is forbidden.

A variable-class policy may later approve specific methods for:

- smooth positive extensive quantities;
- signed rates;
- logarithmic quantities;
- threshold or onset events.

pH and saturation index must not be treated as ordinary linear concentration variables without a deliberate documented policy.

### 22.4 Comparison views

- native-grid trajectory overlays;
- small multiples;
- initial and final scalar tables;
- absolute differences;
- relative differences where the denominator is valid and non-zero;
- solver-cost comparison;
- completion and QC matrix;
- scientific input differences;
- provenance differences;
- excluded-run ledger.

### 22.5 Saved comparison specification

Every exported or reproducible comparison writes `comparison_spec.json` containing:

```text
comparison_schema_version
comparison_id
source run IDs
source schema versions
selected quantities
unit conversions
completion filters
time-alignment mode
common-time tolerance
interpolation policy if any
extrapolation policy: forbidden
excluded runs and reasons
created artifacts
software identity
```

The same comparison must be reproducible through `workbench_cli.py`.

---

## 23. Parameter-study contract

### 23.1 Headless study definition

A study is defined by a versioned `study_spec.yaml`. The GUI edits the specification; a Qt-free generator creates cases deterministically.

Required fields:

```text
study_schema_version
study_id
study_name
baseline_case_path
baseline_case_sha256
baseline_scientific_fingerprint
sampling_method
seed
sample_count
parameters
constraint_groups
cross_parameter_constraints
generated_case_directory
execution_policy
required_outputs
validity_domain
provenance
```

### 23.2 Parameter definition

Each varied parameter contains:

```text
parameter_id
YAML path
data type
scientific meaning
entered unit
canonical unit
range, categories, or imported values
sampling distribution
transform
provenance requirement
constraint-group membership
```

Only explicitly approved YAML paths may be varied. Arbitrary string-path assignment is prohibited.

### 23.3 Supported sources of samples

- deterministic design generated from an approved study specification;
- imported sampling matrix with explicit column mapping and units;
- explicit list of cases already created and validated.

An imported matrix is validated before any case is generated.

### 23.4 Scientific constraints

The study engine must support explicit constraints, including:

- non-negativity;
- upper and lower bounds;
- categorical legality;
- dependent-field legality;
- unit conversion;
- temperature–pressure domain constraints;
- CO₂ mode and workflow consistency;
- kinetic-role and surface-area requirements;
- parameter correlations;
- compositional closure;
- group-total constraints;
- conditional inclusion of fields.

### 23.5 Mineral composition sampling

Mineral or major-group fractions must not be sampled independently and then accepted without closure.

The study specification must state:

- whether values are fractions, mass percentages, volume percentages, or moles;
- the closure total;
- fixed and variable components;
- major-group constraints;
- within-group allocation rules;
- transformations used for compositional sampling;
- rejection or repair policy.

Any repair must be deterministic, mathematically specified, and scientifically approved. Silent renormalisation is prohibited unless the study specification explicitly defines it.

### 23.6 Generated-case record

Every generated case records:

- study ID;
- sample ID;
- baseline case hash;
- input parameter vector;
- canonical parameter vector;
- constraints checked;
- generation outcome;
- case SHA-256;
- scientific fingerprint;
- duplicate relationship;
- validation and run status.

### 23.7 Batch validation

All generated cases pass through the same authoritative full preflight as manually created cases.

Rejected cases remain in the study ledger with exact reasons. The program must not alter their scientific values merely to make them runnable.

### 23.8 Study manifest

The append/finalise study manifest records:

- specification hash;
- generator version;
- sampling method and seed;
- all samples;
- generated case paths and hashes;
- constraint outcomes;
- validation outcomes;
- run IDs;
- completion and QC states;
- excluded and duplicate samples;
- dataset exports.

---

## 24. AI-dataset assembly contract

### 24.1 Workbench boundary

The workbench prepares, validates, documents, and exports simulation datasets. It does not train, tune, select, or evaluate ML models.

It does not perform scaling, imputation, feature selection, dimensionality reduction, or hyperparameter optimisation.

### 24.2 Dataset types

| Dataset type | One sample represents |
|---|---|
| Final-state surrogate | One completed simulation run |
| Fixed-time surrogate | One completed run evaluated at an explicitly supported saved timestamp |
| Time-dependent tabular surrogate | `(run_id, time)` rows, grouped by run for splitting |
| Trajectory dataset | One grouped trajectory per run |
| Failure dataset | One run-level record in a separate failure ledger |

The dataset type is mandatory in the manifest.

### 24.3 Leakage prevention

```text
Rows from one run must never cross train, validation, and test splits.

Replicates, time points, and derived outputs from the same run belong to
one split group.

Scenario families may require higher-level grouping by study, baseline,
geological scenario, experimental source, or composition family.

Split generation operates on group IDs, never independent rows.
```

The manifest records:

- group column;
- split algorithm;
- seed;
- group-level counts;
- run IDs in each split;
- excluded groups;
- leakage checks.

### 24.4 Valid-run gate

A run can enter the valid scientific dataset only when:

- simulation completion is true;
- required output package is complete;
- required source output-schema version is supported;
- required features and targets are available;
- validity-domain metadata is present when required;
- duplicate policy is resolved;
- required conservation or target-audit flags meet the explicit dataset policy;
- no prohibited missing or non-finite values occur.

Blocked, failed, partial, cancelled, force-terminated, crashed, or output-incomplete runs enter only the separate failure/exclusion ledger.

### 24.5 Time handling

The dataset assembler must not silently interpolate trajectories.

A fixed-time dataset may use only:

- timestamps explicitly saved by the runner; or
- a separately approved and recorded derived-alignment policy.

Derived interpolated values are marked and cannot be mixed silently with native saved states.

### 24.6 Dataset manifest

Every dataset export writes a versioned manifest containing:

```text
dataset_schema_version
dataset_id
dataset_type
source study or explicit run set
source run IDs
source output-schema versions
source scientific fingerprints
features and targets
quantity definitions
units
time semantics
validity domain
completion and QC filters
missing-value policy
duplicate policy
split groups and split rule
seed
excluded runs and reasons
failure-ledger path
CSV and Parquet hashes
software and code identity
```

### 24.7 Output formats

- CSV for transparent, moderate-sized tables;
- Parquet for large typed datasets;
- JSON manifest for machine-readable provenance;
- separate CSV or Parquet failure ledger;
- optional Markdown summary.

Every exported row must trace to a `run_id`, and every run must trace to its case, scientific fingerprint, and source study or manual selection.

---

## 25. Reporting and export

### 25.1 Reports

Supported derived reports:

- run report;
- diagnosis report;
- comparison report;
- study report;
- dataset report.

Each report records its source artifacts and generation specification.

### 25.2 Formats

#### Authoritative source and data formats

- YAML for exact case and study specifications;
- JSON for records, manifests, diagnostics, and comparison specifications;
- CSV for transparent tables;
- Parquet for large study and dataset tables.

#### Figures

- runner-generated PNG as canonical static simulation figures where configured;
- workbench-derived PNG and SVG for selected interactive or comparison views.

#### Reports

- Markdown as transparent source;
- self-contained HTML as the primary readable report;
- PDF as a derivative export.

### 25.3 Notebook integration

The workbench may generate a notebook or Python script that reads saved artifacts. It must not embed hidden copies of scientific results or require the GUI to reproduce the analysis.

### 25.4 Shareable export privacy

Reports and manifests intended for sharing use project-relative paths or logical identifiers by default. Full local paths appear only in explicitly private debug or run records.

---

## 26. Major interaction specifications

### 26.1 Create, duplicate, or import a case

1. User selects New, Duplicate, or Import.
2. The workbench creates an unsaved round-trip document.
3. Structured forms and YAML show the same document.
4. Required placeholders remain visibly unresolved.
5. Each section reports completeness.
6. Illegal conditional fields are explained.
7. Save performs syntax, placeholder, and schema checks.
8. A source-to-save diff is available.
9. Atomic save succeeds or the original file remains unchanged.
10. Validation becomes required or stale.
11. Run actions remain disabled.

For imported YAML, failure to parse never modifies the source file.

### 26.2 Validate a case

1. Create an immutable validation snapshot.
2. Resolve approved operational validation paths.
3. Compute snapshot and dependency identities.
4. Run the authoritative full preflight in the solver environment.
5. Stream versioned events without blocking the GUI.
6. Group errors by lifecycle layer.
7. Navigate field-addressable errors to the form or YAML path.
8. Show all configured mineral mappings.
9. Write a validation receipt.
10. Mark the case Ready only when the current scientific fingerprint matches the receipt.

### 26.3 Add cases to a queue

1. Recompute validation freshness.
2. Create final run snapshots with fresh output paths and run IDs.
3. Compute full snapshot and scientific fingerprints.
4. Verify that the scientific fingerprint matches the validation receipt.
5. Show duplicate warnings.
6. Add immutable entries to the queue record.
7. Allow order and failure-policy changes.
8. Keep source cases editable independently.
9. Prevent editing queued snapshots.

### 26.4 Launch a queue

1. Review worker capability and count.
2. Recheck fingerprints and environment identity.
3. Atomically mark the entry Starting.
4. Launch the exact solver command through `QProcess`.
5. Record controller and worker streams separately.
6. Update live status from throttled events.
7. Classify the run from durable evidence and exit status.
8. Apply queue failure policy.

### 26.5 Monitor a run

Display:

- queue position;
- lifecycle stage;
- accepted simulation time and requested duration;
- current or last attempted timestep;
- accepted/rejected counts;
- elapsed wall time;
- latest warning;
- process state;
- current known output completeness.

Do not display a precise completion-time promise.

### 26.6 Diagnose a failure

1. Show exact termination category.
2. State whether the solver began.
3. Show failed stage and last accepted state.
4. Show output completeness separately.
5. Show mapping failures where relevant.
6. Present safe evidence-based actions.
7. Retain raw evidence.
8. Never alter inputs automatically.
9. Rerun only through a new snapshot and run ID.

### 26.7 Review a run

1. Show completion and provenance before plots.
2. Show scientific and numerical views.
3. Preserve batch-scope warnings.
4. Label unsupported derived outputs `not_evaluated`.
5. Allow derived report generation.
6. Keep raw artifacts immutable.

### 26.8 Compare runs

1. Select runs.
2. Check schema, quantity, unit, completion, and time compatibility.
3. Show input and provenance differences.
4. Use native grids by default.
5. Require explicit alignment for derived interpolation.
6. Mark incomplete runs distinctly.
7. Save a comparison specification for any export.

### 26.9 Generate a study

1. Select a verified baseline case.
2. Define approved parameters, units, distributions, and constraints.
3. Review compositional and cross-parameter rules.
4. Generate deterministically through the headless core.
5. Detect duplicates.
6. Save study manifest and generated-case records.
7. Full-preflight every case.
8. Preserve rejected samples and reasons.

### 26.10 Export an AI dataset

1. Select a completed study or explicit run set.
2. Choose a dataset type.
3. Evaluate completion, schema support, features, targets, validity, duplicates, and QC.
4. Define group-level splitting.
5. Verify no run or scenario group crosses splits.
6. Export valid data and a separate failure ledger.
7. Write the complete dataset manifest.
8. Stop at dataset production.

---

## 27. Definitive feature matrix

Criticality expresses final-product importance and dependency, not a reduced release plan.

| Domain | Feature | Criticality | Contract |
|---|---|---:|---|
| Environment | Workbench and solver Environment Doctor | Essential | Verify the exact workbench and solver commands, dependencies, scientific files, code identity, permissions, disk, and platform capability. |
| Environment | Explicit solver launch specification | Essential | Store an environment path or verified command; never silently switch environments. |
| Cases | Case library | Essential | Browse project and imported cases, search, filter, archive, and show document and validation states. |
| Cases | Hybrid YAML/form editor | Essential | One round-trip document, transactional path edits, atomic save, conflict handling, and visible defaults. |
| Cases | Provenance editing | Essential | Structured source and derivation fields without invented values. |
| Cases | External import and case duplication | High | Preserve source files; record lineage; create a new identity. |
| Validation | Layered validation navigator | Essential | Syntax, schema, files, mapping, construction, operational readiness, and scale advisories remain distinguishable. |
| Validation | Field-linked errors | Essential | Navigate to the exact form field or YAML path when available. |
| Validation | Versioned receipt and fingerprints | Essential | Bind preflight to the scientific payload, dependencies, code, environment, and final snapshot. |
| Queue | Immutable queue snapshots | Essential | Entries reference final snapshots and valid receipts, not editable source cases. |
| Queue | Reordering and failure policy | High | Persist order and explicit stop, continue, or pause-for-decision behaviour. |
| Queue | Duplicate warning | High | Compare scientific fingerprints while allowing deliberate replicates. |
| Queue | Capability-gated process concurrency | High | Sequential default; isolated process workers only after focused verification. |
| Execution | Versioned JSONL protocol | Essential | Machine events on stdout, human logs on stderr, durable result artifacts in files. |
| Execution | Live progress | Essential | Throttled accepted time, timestep, attempts, stages, and configured low-cost values. |
| Execution | Cooperative cancellation | Essential | Stop at a safe Python boundary when possible; never promise interruption of an active native call. |
| Execution | Force termination | Essential | Terminate the process tree, preserve flushed evidence, and classify conservatively. |
| Runs | Artifact-derived history | Essential | Run records and results are authoritative; SQLite is rebuildable. |
| Runs | Search and filtering | High | Search status, case, model, study, warnings, output schema, and time. |
| Runs | Diagnosis and provenance | Essential | Plain-language outcome plus complete technical evidence and hashes. |
| Explore | Interactive single-run review | Essential | Accessible plots and tables based on saved outputs, with safe axis controls. |
| Explore | Partial-run boundary | Essential | Last accepted time, requested duration, and omitted summaries remain explicit. |
| Compare | Compatibility gate | Essential | Quantity, unit, schema, time, and completion compatibility before comparison. |
| Compare | Native-grid and final-state comparison | Essential | No implicit interpolation. |
| Compare | Saved comparison specification | Essential | Reproducible headless comparison and export. |
| Studies | Versioned study specification | Essential | Approved parameter registry, units, transforms, provenance, constraints, and seed. |
| Studies | Compositional and dependency constraints | Essential | Prevent scientifically invalid independent sampling and silent repair. |
| Studies | Deterministic generation and full preflight | Essential | Every generated case is traceable and validated identically to manual cases. |
| Datasets | Leakage-safe assembly | Essential | Run/group-level splitting, valid-run gate, separate failure ledger, and full manifest. |
| Reporting | Headless reproducible reports | High | Markdown/HTML/PDF generated from saved specifications and artifacts. |
| Accessibility | Keyboard-complete workflow | Essential | All core operations accessible without pointing-device dependence. |
| Accessibility | Scalable and non-colour communication | Essential | High-DPI support, accessible roles, text status, and tabular plot alternatives. |
| Maintainability | Qt-free core | Essential | Comparisons, studies, datasets, records, and reports remain usable through CLI. |
| Migration | Existing launcher regression reference | Essential until replacement | Do not remove until final acceptance equivalence is demonstrated. |

---

## 28. Accessibility and cognitive-load requirements

### 28.1 Keyboard and focus

- All primary workflows must be keyboard complete.
- Focus order must follow visual and scientific order.
- Custom widgets require accessible names, roles, values, and descriptions.
- Tables expose headers, row identities, cell values, and status text.
- Context menus must have keyboard alternatives.
- Validation errors must be reachable through a dedicated error navigator.

### 28.2 Visual communication

- State is never communicated by colour alone.
- Text respects Windows display scaling.
- Layouts remain usable at common laptop resolutions and high-DPI desktop monitors.
- Scientific notation is rendered consistently and is copyable as text.
- Dense tables support adjustable columns and saved view preferences.
- Warnings distinguish blockers, scientific cautions, operational advisories, and informational notices.

### 28.3 Plot accessibility

Every plot provides:

- an equivalent table;
- series names and units;
- exact values through keyboard-accessible inspection or table selection;
- non-colour series distinction where practical;
- export of the displayed data subset;
- a written summary that does not claim unsupported interpretation.

### 28.4 Progressive disclosure

The interface hides complexity only visually. It does not hide active scientific values, defaults, dependencies, or warnings.

Core fields appear first. Advanced numerical controls remain one explicit level deeper and are never buried behind an undocumented “expert mode.”

---

## 29. Performance and scale requirements

### 29.1 General rules

- GUI responsiveness is protected through processes, worker threads, lazy models, and throttled events.
- Scientific execution is never moved onto the GUI thread.
- Large files are read incrementally or lazily where practical.
- Display downsampling does not alter exported source data.
- The workbench does not retain unnecessary complete copies of large tables in every view.

### 29.2 Benchmark fixtures

The final acceptance suite includes representative fixtures for:

```text
10,000 run records in the index
1,000,000 timeseries rows
500 study samples
large solver-history output
mixed completed, partial, blocked, and crashed runs
multiple supported output-schema versions
```

Performance thresholds must be measured and documented on the target workstation after profiling. Codex must not invent arbitrary timing claims.

### 29.3 Plot limits

The interface must prevent unreadable or unstable rendering of excessive series. Large selections require:

- explicit down-selection;
- small-multiple grouping;
- statistical envelopes only when the calculation is defined and reproducible;
- clear notice when display downsampling occurs.

Display aggregation is not written back into scientific source files.

---

## 30. Features explicitly rejected

| Rejected feature | Reason |
|---|---|
| Automatic scientific parameter suggestions | Unsupported values would undermine provenance and scientific control. |
| Automatic error repair | Many corrections require scientific judgement. |
| Automatic database fallback | Changes the scientific system. |
| Automatic kinetic-model fallback | Changes the kinetic formulation. |
| Automatic case-level mineral aliasing | Conflicts with exact thermodynamic and parameter-file matching. |
| Silent mineral skipping | Produces a different system than configured. |
| Generic plugin manager | Creates hidden execution paths and unnecessary maintenance. |
| Generic simulator-backend architecture | The application is specifically for this Reaktoro batch runner. |
| Built-in database or kinetic-parameter editor | High scientific risk and outside this workbench's scope. |
| General ML training environment | Duplicates specialised tools and weakens focus. |
| Cloud accounts and remote dependency | Unnecessary for the local reproducible workflow. |
| Tauri or multi-language frontend | Adds Rust and JavaScript without a required benefit. |
| Mandatory Jupyter workflow | Jupyter is a follow-on analysis target, not the process authority. |
| Precise completion-time promise | Solver cost is nonlinear and rejection dependent. |
| Pause active native solver call | Unsupported without a verified Reaktoro interruption mechanism. |
| Keep workers running after the workbench closes | Requires a persistent supervisor or service not justified by the project. |
| Editable completed outputs | Breaks provenance; annotations must be separate derived artifacts. |
| Automatic restart from checkpoint | Not implemented or scientifically validated. |
| Reactive-transport, fracture-sealing, or leakage dashboard | The batch model does not represent those processes. |
| Decorative monitoring of every species | Adds cost and clutter without a scientific decision benefit. |
| Independent GUI scientific schema | Would drift from `CaseConfig`. |
| Silent environment installation, repair, or upgrade | Can change numerical behaviour and reproducibility. |
| Silent interpolation for comparison or datasets | Creates undocumented derived values. |
| Random row-level train/test split | Produces simulation and trajectory leakage. |

---

## 31. Repository impact and migration contract

### 31.1 Existing areas

| Existing area | Final treatment |
|---|---|
| `runner.py` | Preserve current CLI; add backward-compatible machine-event and cancellation options. |
| `batch_runner/config.py` | Retain as authoritative runtime schema and preprocessing source. Add only justified metadata and tests. |
| `batch_runner/simulation.py` | Retain orchestration; expose safe progress and cooperative-cancellation checks without Qt imports. |
| `batch_runner/simulator/*` | Retain scientific implementation and direct Reaktoro usage. |
| `batch_runner/outputs.py` | Retain authoritative scientific output writing. |
| `batch_runner/output_tables.py` | Retain table definitions and expose stable descriptors where practical. |
| `batch_runner/output_plots.py` | Retain canonical Matplotlib outputs. |
| `batch_runner/scientific_reports.py` | Retain source calculations for existing configured audit outputs. |
| `batch_runner/manifest.py` | Expand provenance, code identity, and run references without duplicating operational records. |
| `Simulation launcher/simulation_launcher.py` | Preserve as a regression launcher until replacement acceptance passes; later move to an explicit legacy/reference location. |
| `Simulation launcher/launcher_diagnosis.py` | Refactor reusable diagnosis logic into a Qt-free operational module while preserving behaviour. |
| `Simulation launcher/Run Simulations.cmd` | Preserve during migration; later replace with the verified workbench bootstrap. |
| `Simulation launcher/tests/*` | Preserve and extend as golden regression tests. |
| `cases/schema_template.yaml` | Retain as a non-runnable human-facing template, synchronised with active schema. |
| `docs/dev/*` | Retain; add workbench contracts, record schemas, protocol, comparison, study, and dataset documentation. |
| `runs/` | Retain as the run artifact source of truth. |
| `environment.yml` | Retain or rename as the solver-environment specification; do not silently convert it into the GUI environment. |

### 31.2 New areas

```text
workbench_core/
workbench/
workbench_cli.py
docs/workbench/
.workbench/                 # generated local operational state; ignored by Git
```

### 31.3 Migration protection

The current launcher must not be deleted, overwritten, or made unusable until the new workbench proves:

- equivalent preflight results;
- equivalent scientific snapshot contents excluding approved operational fields;
- identical database and kinetic mappings;
- fresh non-overwriting outputs;
- consistent failure classification;
- CLI equivalence;
- full acceptance-test completion.

The transition is complete only after the new bootstrap becomes the documented default and the old launcher is explicitly archived as a reference.

### 31.4 No scientific changes during GUI migration

The GUI upgrade must not modify:

- thermodynamic databases;
- kinetic parameter values;
- mineral names or aliases;
- scientific case values;
- Reaktoro equations;
- solver defaults;
- timestep-acceptance thresholds;
- scientific output definitions;

unless a separate change request provides scientific justification, provenance, and focused verification.

---

## 32. Acceptance criteria

### 32.1 Scientific integrity

- Unknown YAML fields fail.
- Unsupported combinations fail before construction.
- Runnable cases reject unresolved placeholders.
- No GUI action silently changes scientific values.
- Every executed scientific fingerprint matches its successful preflight receipt.
- Final snapshot SHA-256 is verified before launch.
- Every kinetic mineral is mapped or the run is blocked.
- Units and value origins are visible.
- Partial and interrupted runs are never shown as complete.
- Unsupported transport, fracture, permeability, or leakage conclusions are not generated.
- GUI and CLI runs produce numerically equivalent scientific outputs for the same scientific fingerprint.

### 32.2 YAML and editor integrity

- Form changes preserve unrelated comments, ordering, and scalar styles where supported.
- YAML changes update forms only after successful parsing.
- Invalid YAML never overwrites the last valid saved file.
- Pydantic defaults are not silently inserted as user inputs.
- Undo/redo works across form and YAML changes.
- Atomic save prevents partial files.
- External file changes trigger conflict handling.
- Mineral and list ordering remains deterministic.

### 32.3 Process and protocol reliability

- Preflight and simulation never block the GUI event thread.
- Worker crash cannot crash the workbench.
- Malformed JSONL cannot crash the workbench.
- Machine events and human logs use separate channels.
- Controller records exit and kill events even when the worker crashes.
- Event throttling does not change scientific outputs.
- Custom Kinec finalisation behaviour remains correctly classified.
- Force termination handles the Windows process tree.

### 32.4 Queue and recovery

- Queue and run schemas are versioned.
- Every state transition is validated.
- Mutable records use atomic replacement.
- JSONL events are append-only.
- Queue state survives application restart.
- Orphaned active runs are classified conservatively.
- PID reuse cannot cause false worker ownership.
- SQLite can be deleted and rebuilt without losing run history.
- Output-writing failure remains distinct from solver failure.

### 32.5 Cancellation

- Queue pause never claims to pause an active solver call.
- Cooperative cancellation is tested at verified safe boundaries.
- An unresponsive native solver produces `cancel_requested_solver_unresponsive` before force termination.
- Clean cancellation finalises accepted-state evidence only when control returns and output writing succeeds.
- Force termination always creates interruption evidence.

### 32.6 Result and comparison integrity

- Original result packages are never modified by readers.
- Unsupported output schemas are clearly rejected for interpretation.
- Quantity and unit compatibility is checked before comparison.
- Native-grid comparison is the default.
- Interpolation is explicit, variable-aware, recorded, and never extrapolates.
- Invalid log-axis selections are disabled.
- Partial-run boundaries remain visible.
- Comparison exports reproduce from `comparison_spec.json` through CLI.

### 32.7 Study integrity

- Every varied path is approved and typed.
- Units convert deterministically.
- Bounds, dependencies, and categorical legality are checked.
- Composition closure and group constraints are verified.
- Silent renormalisation is prohibited.
- Generated samples reproduce from the study specification and seed.
- Duplicate fingerprints are detected.
- Every generated case receives authoritative full preflight.
- Rejected samples remain in the study ledger.

### 32.8 Dataset integrity

- Failed, partial, blocked, cancelled, crashed, and output-incomplete runs are excluded from valid data.
- Every row traces to a run ID.
- Runs and higher-level scenario groups never cross dataset splits.
- Time rows from one run remain in one split.
- Split rule, seed, and group IDs are recorded.
- Native and interpolated values cannot be mixed silently.
- Failure records are exported separately.
- CSV and Parquet content hashes are recorded.
- Dataset generation reproduces through CLI.

### 32.9 Reproducibility

Every completed run records:

- source and final snapshot hashes;
- scientific and operational fingerprints;
- database and kinetic identities;
- code identity including dirty-state evidence;
- environment specification and package inventory;
- Python and Reaktoro versions;
- configuration, output, protocol, and record schema versions;
- exact output inventory.

A simulation and every derived artifact can be reproduced without the GUI.

### 32.10 Accessibility

- All major workflows are keyboard accessible.
- Focus order is deterministic.
- Status is never colour-only.
- Tables expose accessible headers and values.
- Plot data is available in an accessible table.
- Text respects Windows scaling.
- Scientific notation is copyable and consistent.
- Error navigation is accessible.

### 32.11 Maintainability

- `batch_runner` contains no Qt imports.
- `workbench_core` contains no Qt imports.
- Scientific calculations are not duplicated in GUI code.
- Protocol, records, readers, studies, datasets, and reports have focused tests.
- Main workflows use fake-worker integration tests.
- Real Reaktoro tests remain focused and separate.
- No plugin manager, generic backend, hidden registry, or dependency-injection container is introduced.
- The full execution chain remains understandable to one researcher.

### 32.12 Performance

- The GUI remains responsive for the benchmark fixtures.
- Large tables use lazy or incremental loading where practical.
- Display downsampling never changes exported source data.
- Live monitoring has no material effect on solver output or accepted-step behaviour.
- Run-index rebuilding is deterministic.
- Measured performance results are documented rather than invented.

---

## 33. Verification strategy

### 33.1 Test layers

#### Pure unit tests

- fingerprint canonicalisation;
- record schema validation;
- state transitions;
- YAML path patching;
- comparison compatibility;
- study constraints;
- dataset grouping and leakage checks.

#### Fake-worker integration tests

- process events;
- malformed JSONL;
- cancellation;
- force termination;
- queue policies;
- recovery records;
- output-writing failures;
- controller crashes.

#### Real runner integration tests

- full preflight;
- one equilibrium case;
- one native Palandri–Kharaka kinetic case;
- one custom Kinec case;
- fixed and adaptive timestep cases;
- partial failure;
- output-writing failure where safely testable.

#### GUI tests

- keyboard navigation;
- focus order;
- field-linked errors;
- editor/YAML synchronisation;
- high-DPI layout;
- accessible names and roles;
- queue and run-state presentation.

### 33.2 Golden equivalence tests

For selected cases, compare old launcher, new workbench, and direct CLI:

```text
scientific fingerprint
resolved scientific configuration
mineral mapping
output schema
timeseries numerical values
summary tables
diagnostics classification
```

Operational paths, run IDs, and timestamps may differ.

### 33.3 Required test evidence

Codex must report:

- exact commands run;
- tests passed and failed;
- real Reaktoro cases executed;
- generated artifacts inspected;
- known platform limitations;
- remaining unsupported behaviour;
- any acceptance criterion not demonstrated.

---

## 34. Implementation dependency order

This dependency order does not reduce the final scope. It prevents later features from being built on unstable contracts.

```text
1. Reconcile AGENTS.md and workbench authority
2. Define versioned schemas and state machines
3. Implement fingerprints and immutable records
4. Implement Qt-free workbench core and CLI
5. Implement worker protocol and backward-compatible runner options
6. Implement process controller, queue persistence, and recovery
7. Implement PySide6 shell and environment management
8. Implement hybrid case editor and validation navigation
9. Implement execution monitoring and diagnosis
10. Implement artifact-derived run history
11. Implement result readers and output-schema adapters
12. Implement single-run exploration
13. Implement comparison and saved comparison specifications
14. Implement constrained studies and study manifests
15. Implement leakage-safe dataset assembly
16. Implement reproducible reports and exports
17. Complete accessibility, scale, regression, and acceptance audits
18. Make the new workbench the documented default only after equivalence passes
```

No step authorises a reduced product. Each step must leave the repository testable and scientifically unchanged.

---

## 35. Codex execution governance

Codex must treat this document as the complete final-target architecture, not permission for an uncontrolled rewrite.

Before changing production code, Codex must:

1. read `AGENTS.md` and the three coordinated developer contracts;
2. inspect the current launcher, runner, configuration, simulation, output, manifest, plotting, scientific-report, and test modules;
3. reconcile repository guidance with this workbench contract;
4. produce an implementation dependency graph and file-impact map;
5. define schemas for:
   - worker protocol;
   - validation receipt;
   - queue record;
   - run record;
   - comparison specification;
   - study specification;
   - study manifest;
   - dataset manifest;
6. preserve the current CLI and launcher until replacement acceptance tests pass;
7. make no changes to scientific parameters, databases, defaults, equations, mineral identity, or Reaktoro model behaviour;
8. keep all Qt imports outside `batch_runner` and `workbench_core`;
9. preserve headless reproducibility for simulations and derived artifacts;
10. use focused commits after coherent dependencies, with tests passing;
11. stop and report any contradiction requiring a scientific decision;
12. state honestly when a requested feature is not supported by Reaktoro or the current output contract;
13. avoid adding broad abstractions that are not required by this contract;
14. update documentation and tests with every schema or user-visible behaviour change.

Codex must not satisfy a GUI test by inventing scientific values or changing a runnable case without source support.

---

## 36. Final verdict

The definitive application is a **PySide6/Qt Widgets Reaktoro Scientific Workbench** with:

```text
separate workbench and solver environments
Qt-free operational and analysis core
QProcess-isolated solver execution
versioned JSONL protocol
transactional hybrid YAML/form editing
scientific and operational fingerprints
immutable queue and run records
artifact-derived history
cooperative cancellation with conservative classification
interactive but output-grounded exploration
explicitly compatible comparison
constraint-aware parameter studies
leakage-safe dataset assembly
headless reproducible reports
```

Its governing principle is:

```text
Make the scientific workflow easier to operate
without making the scientific model easier to alter invisibly.
```

The existing launcher remains the regression reference until the new workbench proves scientific and operational equivalence.

The final workbench differs from the present launcher as follows:

```text
Current launcher
= safe case selector, preflight tool, sequential process launcher,
  and diagnosis entry point

Definitive workbench
= complete, scientifically constrained operating environment for
  authoring, validating, executing, auditing, comparing, generating,
  and exporting reproducible Reaktoro batch experiments
```

The interface may be rich. The scientific execution path must remain explicit, inspectable, reproducible, and independent of the GUI.

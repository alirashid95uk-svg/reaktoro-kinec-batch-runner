# Workbench acceptance matrix

This matrix is the retained verification record for the implemented workbench.
It records the acceptance criteria and evidence that were used during the
workbench upgrade without retaining the former target-design document as an
active feature plan. “Pass” means implemented and covered by the named
automated or real-run evidence. “Constrained” means the safe current contract is
implemented but an explicitly prohibited or scientifically unapproved mode is
not enabled. “Partial evidence” identifies a measurement not claimed as fully
demonstrated.

## 32.1 Scientific integrity

| Criterion | Status | Evidence |
|---|---|---|
| Unknown YAML fields fail | Pass | Strict `CaseConfig`; solver and document tests. |
| Unsupported combinations fail before construction | Pass | Existing schema/config suite and authoritative preflight. |
| Runnable cases reject unresolved placeholders | Pass | Recursive sentinel gate and template/editor tests. |
| GUI actions do not silently change scientific values | Pass | Round-trip document tests; kinetic-model form refuses incomplete coupled edits. |
| Executed fingerprint matches successful receipt | Pass | `verify_prelaunch`; stale snapshot/dependency/code/environment tests and real runs. |
| Final snapshot SHA-256 is verified | Pass | Run authorisation and prelaunch tests. |
| Every kinetic mineral is mapped or blocked | Pass | Receipt schema, mapping events, full preflight, three-mineral real smoke. |
| Units and value origins are visible | Pass | Section-resolved case view, manifest traceability, Explore provenance, quantity descriptors. |
| Partial/interrupted runs are never shown complete | Pass | Run/result state gates and interrupted-package tests. |
| Unsupported transport/fracture/permeability/leakage conclusions are absent | Pass | Persistent batch-scope warnings; reports and dataset code do no such inference. |
| GUI and CLI outputs are numerically equivalent | Pass | Golden three-path CSV hashes and matching diagnostics/schema. |

## 32.2 YAML and editor integrity

| Criterion | Status | Evidence |
|---|---|---|
| Form changes preserve unrelated comments, ordering, and supported scalar styles | Pass | Transactional all-explicit-values editor, ruamel round trip, template/list regressions. |
| YAML updates forms only after successful parsing | Pass | Transactional apply test. |
| Invalid YAML never overwrites the last valid file | Pass | Invalid YAML and external-change GUI tests. |
| Pydantic defaults are not silently inserted as user inputs | Pass | Resolved values are read-only and separate; source round-trip tests. |
| Undo/redo spans form and YAML changes | Pass | Document history schema and tests. |
| Atomic save prevents partial files | Pass | Same-directory replacement; doctor and document tests. |
| External changes trigger conflict handling | Pass | File watcher plus pre-save byte identity; GUI test. |
| Mineral/list ordering is deterministic | Pass | Round-trip document and schema tests. |

## 32.3 Process and protocol reliability

| Criterion | Status | Evidence |
|---|---|---|
| Preflight and simulation do not block the GUI thread | Pass | Headless and direct-solver `QProcess` controllers; Qt tests and real smoke. |
| Worker crash cannot crash the workbench | Pass | Fake crash/controller classification tests. |
| Malformed JSONL cannot crash the workbench | Pass | Protocol-reader and controller tests. |
| Machine events and human logs use separate channels | Pass | JSONL stdout and human stderr runner contract. |
| Exit and actual kill actions are recorded | Pass | `kill_confirmed` and `kill_failed` are durable and distinct from a request. |
| Event throttling does not change scientific outputs | Pass | Throttling wraps event emission only; event-enabled workbench and direct/legacy hashes match. |
| Custom Kinec finalisation remains correctly classified | Pass | Managed Kinec real smoke and runner protocol tests. |
| Force termination handles the Windows process tree | Pass | PID-ownership guard plus real descendant-process `taskkill /T /F` test and failure simulation. |

## 32.4 Queue and recovery

| Criterion | Status | Evidence |
|---|---|---|
| Queue and run schemas are versioned | Pass | Strict v1 schemas. |
| Every state transition is validated | Pass | Explicit transition maps and invalid-transition tests. |
| Mutable records use atomic replacement | Pass | Shared persistence service and tests. |
| JSONL events are append-only | Pass | Controller/worker append protocol and integration tests. |
| Queue state survives restart | Pass | Active queue restore/recovery tests. |
| Orphaned active runs are conservative | Pass | Recovery tests and recovered real orphan. |
| PID reuse cannot create false ownership | Pass | Recovery never reattaches; live kill compares the owned QProcess PID. |
| SQLite is disposable and rebuildable | Pass | Artifact rebuild tests and 10,000-record deterministic fixture. |
| Output failure remains distinct from solver failure | Pass | Existing and added output-completeness/failure tests. |

## 32.5 Cancellation

| Criterion | Status | Evidence |
|---|---|---|
| Queue pause never claims to pause an active call | Pass | Queue status/action tests and explicit UI text. |
| Cooperative cancellation occurs only at verified boundaries | Pass | Fixed/adaptive solver rollback and output-boundary tests. |
| Unresponsive cancel state precedes force | Pass | Controller timer, durable run transition, and tests. |
| Clean cancel finalises accepted evidence only after control returns and writing succeeds | Pass | Solver/output cancellation tests. |
| Force termination always creates interruption evidence | Pass | Controller stream, forced terminal run record, and tests. |

## 32.6 Results and comparisons

| Criterion | Status | Evidence |
|---|---|---|
| Readers and derived writers never modify source packages | Pass | Read-only services plus equal/descendant output-path guards and artifact-preservation tests for comparisons, datasets, and reports. |
| Unsupported schemas are clearly rejected | Pass | Raw inventory remains visible; interpretation gate tests. |
| Quantity/unit compatibility is checked | Pass | Descriptor-based compatibility tests and GUI gate. |
| Native-grid comparison is default | Pass | GUI and CLI defaults. |
| Interpolation is explicit, variable-aware, recorded, and non-extrapolating | Constrained | Policy and domain checks exist; every current v4 descriptor forbids interpolation because none is scientifically approved. |
| Invalid log axes are disabled | Pass | Descriptor/sign/value checks and GUI tests. |
| Partial-run boundaries remain visible | Pass | Persistent status, requested/reached time, accepted-state/numerical views. |
| Comparison reproduces through CLI | Pass | `compare-reproduce` produced an identical recorded CSV hash. |

## 32.7 Studies

| Criterion | Status | Evidence |
|---|---|---|
| Varied paths are approved and typed | Pass | Explicit registry and path/type tests. |
| Units convert deterministically | Pass | Closed conversion table and tests. |
| Bounds, dependencies, and categories are checked | Pass | Study schema/generator tests. |
| Composition and group constraints are checked | Pass | Constraint engine tests. |
| Silent renormalisation is prohibited | Pass | Default/unapproved closure repair rejects without mutation; only an explicitly `scientifically_approved` deterministic policy can normalise and records its factor. |
| Generation reproduces from specification and seed | Pass | Deterministic grid/random/LHS tests and hashed specification. |
| Duplicate fingerprints are detected | Pass | Manifest and queue/dataset duplicate tests. |
| Every generated case receives full preflight | Pass | Generator requires callback; real one-sample CLI study produced ready baseline/sample receipts. |
| Rejected samples remain in the ledger | Pass | Manifest tests. |

## 32.8 Datasets

| Criterion | Status | Evidence |
|---|---|---|
| Non-complete runs are excluded from valid data | Pass | Completion/auditor gate and exclusion-ledger tests. |
| Every row traces to a run ID | Pass | Dataset schema/output tests. |
| Runs and higher groups do not cross splits | Pass | Run, replicate, study, and explicit scenario-group union/leakage tests. |
| All time rows for one run share one split | Pass | Group-level assignment tests. |
| Split rule, seed, and group IDs are recorded | Pass | Manifest schema and generated dataset inspection. |
| Native and interpolated values cannot mix silently | Pass | Native timestamp selection and interpolation-forbidden semantics. |
| Failure records are separate | Pass | Failure dataset and exclusion ledger tests. |
| CSV/Parquet hashes are recorded | Pass | Generated dataset manifest and hash tests. |
| Dataset generation is available through CLI | Pass | Real `dataset-assemble` artifact set plus focused service tests. |

## 32.9 Reproducibility

| Criterion | Status | Evidence |
|---|---|---|
| Completed runs record source/final hashes and both fingerprints | Pass | Real run records and schemas. |
| Database and kinetic identities are recorded | Pass | Load-time hashes, receipt, manifest, diagnostics, protected-file hash check. |
| Code identity includes dirty-state evidence | Pass | Git commit/status and relevant-tree hash in environment/receipts. |
| Environment spec, export, and package inventory are recorded | Pass | Doctor evidence and receipt hashes. |
| Python/Reaktoro and all contract versions are recorded | Pass | Receipts, protocol, records, manifest, environment evidence. |
| Exact output inventory is recorded | Pass | Diagnostics, manifest, run record, saved audit-table inventory, and authoritative auditor. |
| Simulations and derived artifacts work without GUI | Pass | Direct runner, comparison reproduction, report reproduction, and `workbench_cli.py` command matrix. |

## 32.10 Accessibility

| Criterion | Status | Evidence |
|---|---|---|
| Major workflows are keyboard accessible | Pass | Menus, shortcuts, tab order, F2 explicit-value editing, Qt workflow tests, and native UIA shortcut navigation across all seven pages plus sampled flows. |
| Focus order is deterministic | Pass | Explicit editor order and prerequisite state matrices; native UIA samples enabled-focus traversal without claiming every workflow. |
| Status is not colour-only | Pass | Text, icon, accessible-description component test. |
| Tables expose headers and values | Pass | Shared accessible table construction and tests. |
| Plot data has an accessible table | Pass | Exact displayed values, copy, and GUI test. |
| Windows scaling is respected | Partial evidence | The 84-record manifest covers seven pages, two states, two logical sizes, and native Qt factors 100%, 125%, and 150% with zero automated clipping/root-scroll failures. Native UIA passed, but no physical multi-monitor DPI measurement is claimed. |
| Scientific notation is copyable and consistent | Pass | Native table strings and selected-value clipboard action. |
| Error navigation is accessible | Pass | Named list, F6, field navigation, tests. |

## 32.11 Maintainability

| Criterion | Status | Evidence |
|---|---|---|
| `batch_runner` and `workbench_core` contain no Qt imports | Pass | Import scan test. |
| Scientific calculations are not duplicated in GUI | Pass | GUI reads saved descriptors/artifacts only. |
| Protocol/records/readers/studies/datasets/reports have focused tests | Pass | Workbench core suite. |
| Main workflows use fake-worker integration tests | Pass | Process-controller suite. |
| Real Reaktoro tests are separate and focused | Pass | Solver suite plus manual Palandri/Kinec smokes. |
| No forbidden generic architecture was added | Pass | Repository inspection and independent reviews. |
| Execution chain remains understandable | Pass | Direct YAML -> validation -> runner -> saved artifacts architecture. |

## 32.12 Performance

| Criterion | Status | Evidence |
|---|---|---|
| GUI remains responsive for benchmark fixtures | Partial evidence | Simulation/derived work are processes; Runs caps indexed view at 500; Explore loads 10,000 displayed rows. Core scale passed 3/3 in 266.86 s, but no arbitrary GUI latency threshold is claimed. |
| Large tables are incremental/lazy where practical | Pass | pandas chunk readers; first-window GUI rendering; SQLite search limits. |
| Display downsampling does not alter source export | Pass | Source artifacts are immutable; displayed-subset export is explicitly labelled. |
| Monitoring does not materially change solver output/steps | Pass | Event-enabled workbench and direct/legacy output hashes match. |
| Run-index rebuilding is deterministic | Pass | Repeated 10,000-record rebuild test. |
| Measurements are documented, not invented | Pass | Scale wall times and target workstation are recorded in `acceptance_evidence.md`. |

Current implementation boundaries: the supported scientific output schema is
v4 and older schemas are rejected. Parallel study workers are disabled.
Comparison interpolation remains disabled for current v4 descriptors because
none is scientifically approved. These are current-state constraints, not
roadmap commitments.

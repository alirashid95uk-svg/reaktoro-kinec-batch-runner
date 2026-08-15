# Reaktoro Scientific Workbench

The workbench is the native Windows interface for this repository's batch
equilibrium and kinetic runner. It adds editing, orchestration, inspection,
comparison, study generation, dataset assembly, and reporting without moving
scientific calculations into the GUI.

## Environment setup

Keep the two environments separate:

```powershell
conda env create -f environment-workbench.yml
conda run -n fypr-reaktoro python -c "import reaktoro; print(reaktoro.__version__)"
.\Run Workbench.cmd
```

`environment-workbench.yml` is pinned and contains PySide6, pyqtgraph,
round-trip YAML support, table/Parquet support, report generation, and GUI
tests. It intentionally does not contain Reaktoro. `environment.yml` remains
the solver specification. The workbench Environment Doctor verifies the exact
solver prefix, import command, package inventory, environment export, project
files, atomic replacement, code identity, and available disk space. It never
installs, updates, repairs, or silently switches an environment.

For non-default locations:

```powershell
$env:REAKTORO_WORKBENCH_PREFIX = 'D:\conda\envs\reaktoro-workbench'
$env:REAKTORO_SOLVER_PREFIX = 'D:\conda\envs\fypr-reaktoro'
.\Run Workbench.cmd
```

The selected solver prefix and launch form are persisted in
`.workbench/settings.json`; an explicit command-line argument or environment
variable takes precedence. Settings writes are atomic and never install or
change packages.

## Architecture and authority

```text
PySide6 views
  -> Qt-free workbench_core services and strict schemas
  -> immutable validation snapshot and receipt
  -> exact solver-environment command
  -> runner.py in a QProcess
  -> existing batch_runner scientific execution
  -> immutable result artifacts and append-only events
```

`batch_runner` and `runner.py` remain the scientific authority. `workbench`
contains presentation and process wiring. `workbench_core` contains no Qt and
owns documents, fingerprints, validation, records, comparisons, studies,
datasets, reports, and the rebuildable run index. `workbench_cli.py` exposes
the same artifact-changing services without the GUI.

The seven permanent workspaces are Home and Environment, Cases, Queue, Runs,
Explore, Compare, and Studies. Use Ctrl+1 through Ctrl+7 to navigate. Status
always has text and an icon; plot values are also available in a keyboard-
accessible table.

The interface uses a fixed scientific light theme and progressive disclosure.
The left sidebar states what each workspace does; secondary filters, validation
evidence, numerical monitor values, axis controls, and environment details stay
collapsed until they are useful. At 960 x 600 and above, dense content remains
inside page-level tabs, splitters, or scroll regions rather than creating a
root horizontal scrollbar. Full paths and durable IDs remain available in
tooltips and provenance views, while operational tables use case names and
short run IDs.

`Run Workbench.cmd` prints an immediate startup message. A splash window is
shown before the main workspaces are imported and constructed. If startup
fails, the command window retains the error and the GUI also shows an
actionable failure dialog.

## First run through the interface

1. On **Home**, run **Environment Doctor**. Expand its details only if a check
   is blocked or you need the exact recorded environment identity.
2. Open **Cases**, select a case, and choose **Open**. Core fields are shown
   first; **Advanced values** and **YAML** retain complete,
   transactional access to the source.
3. Use the ordered action bar: **Save**, **Validate saved case**, then
   **Prepare validated run for queue**. Blocking evidence opens the validation
   drawer automatically.
4. In **Queue**, confirm the failure policy and the verified worker count,
   then start the prepared run. Cancellation controls appear only during live
   execution; detailed numerical monitoring is expandable.
5. Use **Runs** to find the completed record and inspect its diagnosis and
   provenance. Activate the row to open **Explore**.
6. In **Explore**, choose a saved quantity and exact time display. The plot,
   accessible data table, numerical evidence, and saved audit tables all read
   the existing package without recalculation.

**Compare** guides package selection through alignment and compatibility before
export. **Studies** separates Definition, Parameters and Constraints,
Samples/QC, Dataset Export, and Reports so dataset safety controls never share
one overflowing toolbar.

## Safe case workflow

1. Open, import, duplicate, or create an unsaved case from
   `cases/schema_template.yaml`.
2. Replace every template sentinel. The workbench does not supply scientific
   numbers.
3. Apply YAML. Duplicate keys, unknown fields, invalid combinations, and
   unresolved placeholders block the form and cannot overwrite the last valid
   file.
4. Save atomically. An external modification creates a conflict instead of an
   overwrite.
5. Validate. Full solver-environment preflight writes an immutable snapshot,
   a versioned receipt, dependency hashes, environment evidence, stage results,
   and exact kinetic-mineral mapping.
6. Prepare a run. This creates a fresh final snapshot and run record; the source
   case remains unchanged.
7. Add only ready snapshots to the sequential queue.

Form edits preserve unrelated YAML comments, ordering, and scalar style where
round-trip YAML supports them. The **Advanced values** table exposes every
source scalar with its path, unit, and value origin; press F2 to edit a selected
value. Multi-value edits, list removals, and placeholder-key renames apply as
one transaction. This also provides a structured, progressive path through the
schema template without invented values. Resolved defaults are visible
separately and are not silently inserted into the source document.

## Queue, cancellation, and recovery

The verified queue uses one solver worker. Queue policy is explicit:
`stop_after_failure`, `continue_after_failure`, or `pause_for_decision`.
Duplicate scientific fingerprints are shown before execution.

Pause and cancel-after-current affect queue scheduling only; neither pauses an
active native Reaktoro call. Graceful cancellation creates a sentinel checked
at verified safe boundaries. If native control does not return within the
controller deadline, the run first becomes
`cancel_requested_solver_unresponsive`; force termination then uses Windows
process-tree termination and writes controller evidence. Accepted-state output
is finalised only if scientific control returns and output writing succeeds.

One controlling GUI or CLI instance owns `.workbench/control.lock`; delegated
child commands carry the lock token rather than bypassing it. Mutable JSON
records use same-directory atomic replacement. `events.jsonl` is append-only.
Startup recovery reconciles orphaned records and the active queue
conservatively; it does not infer successful completion from a vanished PID or
from PID reuse.

## Results, comparisons, studies, and datasets

Runs are rebuilt from `run_record.json`, `manifest.json`, and
`diagnostics.json`; `.workbench/run_index.sqlite` is disposable. Explore reads
saved artifacts only. It shows completion boundaries, hashes, solver attempts,
raw artifact inventory, exact plot data, safe log-axis controls, time-unit
conversion, saved Objective 1 audit tables with a disclosed 1,000-row display
cap, and PNG/SVG export. It does not recalculate Reaktoro properties.

Comparisons require explicit quantity identity, unit, schema, completion, time
semantics, and native-domain compatibility. Native accepted grids are the
default. No v4 quantity currently has an approved interpolation policy, so
interpolation is refused and extrapolation is always forbidden.

Study specifications are versioned YAML with approved typed paths, source
units, canonical units, constraints, provenance, seed, validity domain, output
requirements, and a sequential execution policy. Generation is deterministic;
every generated case receives the authoritative preflight; rejected and
duplicate samples remain in the manifest.

Dataset assembly stops at dataset production. It admits only complete,
supported, auditor-passing runs with explicit features and targets. It does no
training, imputation, scaling, feature selection, or silent interpolation.
Runs, trajectories, explicit scenario groups, and replicate fingerprints
cannot cross splits. CSV, Parquet, a separate failure ledger, hashes, source
identities, split rules, seed, validity-domain policy, and QC filters are
recorded.

## Headless examples

```powershell
conda run -n reaktoro-workbench python workbench_cli.py doctor --solver-prefix "$env:REAKTORO_SOLVER_PREFIX"
conda run -n reaktoro-workbench python workbench_cli.py validate cases\case.yaml --solver-prefix C:\path\to\fypr-reaktoro
conda run -n reaktoro-workbench python workbench_cli.py run cases\case.yaml --solver-prefix C:\path\to\fypr-reaktoro
conda run -n reaktoro-workbench python workbench_cli.py rebuild-index
conda run -n reaktoro-workbench python workbench_cli.py compare --help
conda run -n reaktoro-workbench python workbench_cli.py study-generate --help
conda run -n reaktoro-workbench python workbench_cli.py dataset-assemble --help
conda run -n reaktoro-workbench python workbench_cli.py report --help
conda run -n reaktoro-workbench python workbench_cli.py report-reproduce --help
```

Run the legacy scientific CLI directly when no workbench artifacts are needed:

```powershell
conda run -n fypr-reaktoro python runner.py cases\case.yaml
```

## Scientific interpretation boundary

This remains batch geochemistry. It is not reactive transport, a caprock
leakage forecast, or fracture-resolved HMC. Mineral-volume change is not
porosity, permeability, capillary-entry-pressure, or fracture-aperture change
without an explicit sourced update law. Matrix precipitation is not fracture
sealing. Long batch duration does not supply transport-limited behaviour.

The complete record, protocol, comparison, study, dataset, and migration
contracts are in [contracts.md](contracts.md). Measured verification evidence
and declared limitations are in [acceptance_evidence.md](acceptance_evidence.md).
The criterion-by-criterion audit is in
[acceptance_matrix.md](acceptance_matrix.md).

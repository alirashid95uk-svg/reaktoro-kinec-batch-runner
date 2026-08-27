# Reaktoro Batch Runner

A simple Reaktoro batch simulation runner for explicit YAML-defined
equilibrium and kinetic cases. Reaktoro's native Palandri-Kharaka model uses
the corrected local parameter file by default; the custom Kinec model is an
explicit option.

The scientific core is organised into stable `batch_runner.config`,
`batch_runner.simulator`, and `batch_runner.outputs` package APIs. See
[docs/dev/architecture.md](docs/dev/architecture.md) for the package map and
execution flow.

## Recommended: Scientific Workbench

Create the pinned GUI environment once, then double-click `Run Workbench.cmd`:

```powershell
conda env create -f environment-workbench.yml
.\Run Workbench.cmd
```

The PySide6 workbench keeps GUI-only packages outside the verified Reaktoro
environment. It diagnoses both environments without silently installing or
repairing either one. It provides the complete case editor, immutable
preflight snapshots and receipts, sequential queue, process control and
recovery, artifact-derived run history, result exploration, compatibility-
gated comparisons, deterministic studies, leakage-safe dataset assembly, and
reproducible reports. See [docs/workbench/README.md](docs/workbench/README.md).

Every artifact-changing operation is also available through the Qt-free CLI:

```powershell
conda run -n reaktoro-workbench python workbench_cli.py --help
```

Set `REAKTORO_WORKBENCH_PREFIX` and `REAKTORO_SOLVER_PREFIX` before starting
the bootstrap when either environment is stored outside the default Conda
location.

## Legacy Regression Launcher

`Simulation launcher/Run Simulations.cmd` remains available as a preserved
regression reference. It selects cases, performs the same scientific
construction preflight, and runs them sequentially. The launcher:

- starts the verified `fypr-reaktoro` environment;
- validates the configuration, database, kinetic records, mineral mapping,
  chemical system, and initial state before execution;
- runs selected cases sequentially;
- creates `runs/<case>/<timestamp>/run_case.yaml` as a traceable input snapshot;
- changes only the snapshot's `paths.output_dir`;
- writes results into a fresh `results/` folder and never overwrites an old run;
- keeps terminal output in `launch_log.txt`; and
- writes a plain-language `diagnosis.txt` with the true failure stage, last
  accepted time, output completeness, and safe next actions;
- captures Python/native crash stacks and the exact child-process exit code;
- opens the latest run folder or its report from **Open last run** and
  **Open diagnosis**.

The original YAML case and all scientific settings remain unchanged. The run
snapshot records the original case path and SHA-256 hash. The launcher improves
setup and queue handling; it does not change Reaktoro solver performance or
scientific settings.

## Run a Case

Use the locally verified environment:

```powershell
conda run -n fypr-reaktoro python runner.py path\to\case_input.yaml
```

Normal execution shows a compact live terminal monitor. On an interactive
terminal it redraws in place; redirected output is plain line-oriented text.
Use `--events-jsonl` for the separate machine-readable stdout protocol.

Or create a separate environment:

```powershell
conda env create -f environment.yml
conda run -n reaktoro-batch-runner python runner.py path\to\case_input.yaml
```

The output directory must not already exist. Every run writes the standard
manifest, diagnostics, accepted-state timeseries, solver history, and log:

```text
manifest.json
diagnostics.json
simulation.log
timeseries.csv
solver_history.csv
```

Requested species and minerals automatically add their standard timeseries
columns and `aqueous_summary.csv` or `mineral_summary.csv`. Top-level `plots`
contains only plot controls; top-level `monitor` and `debug` own presentation
and troubleshooting settings.

Optional Objective 1 audit outputs are disabled unless explicitly enabled in
`postprocessing`; each enabled analysis writes its corresponding table without
a second output toggle:

```text
reaction_rates.csv
reaction_rate_validation.csv
carbon_inventory.csv
element_budget.csv
mineral_volume_change.csv
regime_classification.csv
surface_area_audit.csv
workflow_comparison.csv
secondary_mineral_assemblage.csv
surrogate_dataset.csv
porosity_permeability.csv
```

## Inputs

- Thermodynamic database: `data/thermo/Kinec_v3_4.dat`
- Default native kinetic parameters: `data/kinetics/PalandriKharaka_local.yaml`
- Optional custom Kinec parameters: `data/kinetics/kinec_rates_minimal.yaml`
- Optional Kinec adapter:
  `batch_runner/simulator/kinetics/kinec.py`

Copy `cases/schema_template.yaml` to create a case input. The template is
intentionally not runnable: replace every required placeholder with a value
from supplied data, supplied files, explicit user instruction, or
deterministic preprocessing.

The Pydantic models under `batch_runner/config/` are the authoritative
configuration interface. Inspect the same field metadata used by validation:

```powershell
python runner.py config --help
python runner.py config --help timestep
python runner.py config --help kinetics.model
```

Build the browsable API, configuration, CLI, architecture, and limitations
documentation with `python tools/build_docs.py`. The generated configuration
and CLI pages are build products; do not edit them manually.

Relative paths in a case config are resolved from the project root. Absolute
paths also work. A missing path stops the run and reports the exact resolved
path; the runner does not search other locations.

Optional post-simulation validation is configured with `validation.enabled`
and a trusted script under `validation/`. After a complete simulation package,
the runner invokes that script with the actual timestamped `--results-dir` and
writes downstream analysis beside `results/` in the run's `validation/`
directory. Validation failure is reported separately and does not invalidate
the simulation package.

Cation exchange is not implemented.
Automatic experimental calibration or experiment-fitting is not implemented;
post-simulation validation remains downstream analysis only.
Transport is not implemented.

Runnable examples are the non-template YAML case files currently tracked under
`cases/`. The Jayasekara files exercise the supported long-horizon workflows; their
names do not establish experimental agreement. The generated Pokrovsky
Calcite cases have source and interpretation limits documented in
`cases/pokrovsky_2005/README.md`.

On Windows, completed custom-Kinec CLI runs exit immediately after all outputs
are closed to avoid a Reaktoro 2.13 Python rate-callback finalization crash.

## Case Rules

- `database.source` is `local` or `embedded`; no fallback is used.
- `co2.mode` is `disabled`, `finite`, or `fixed_fugacity`.
- `redox.apply_during` explicitly controls pE staging when redox is enabled.
- Mineral role `equilibrium` creates an equilibrium phase.
- Mineral `name` is the exact thermodynamic database species name.
- Mineral role `kinetic` requires an initial amount, surface area, and a
  matching selected-model parameter record.
- `kinetics.model` is `palandri_kharaka` by default or explicitly `kinec`.
- `kinetics.path` may override the selected model's project parameter file.
- Native Palandri-Kharaka matching uses the parameter file's `Mineral` and
  `OtherNames` fields; case-level mineral aliases are not supported.
- `solver.workflow` explicitly controls equilibrium and kinetic constraint
  staging.
- `solver.timestep` owns duration and timestep control.
- Fixed, legacy solver-feasibility `adaptive`, and explicit Richardson
  `adaptive_error_controlled` timesteps use standard solvers. Existing
  `mode: adaptive` cases retain their original controller. Checkpoint writing
  is implemented; automatic restart and smart solver backends remain unavailable.
- Objective 1 analyses are active `postprocessing` fields. Budgets and
  inventories use only explicitly configured species/mineral stoichiometry.
  Reaction-rate diagnostics use Reaktoro's attached runtime rates and live
  total surface areas for accepted states. Mineral volume, porosity,
  permeability, and capillary-entry-pressure
  outputs report `not_evaluated` unless their required source-supported inputs
  or update laws are explicitly configured.

## Add a Small Feature

Add one described config field, validate it, add direct execution logic in the
relevant small module, expose its output if needed, and add one focused test.
`runner.py` remains orchestration only.

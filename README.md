# Reaktoro Kinec Batch Runner

A simple Reaktoro batch simulation runner for explicit YAML-defined
equilibrium and Kinec kinetic cases.

## Run a Case

Use the locally verified environment:

```powershell
conda run -n fypr-reaktoro python runner.py path\to\case_input.yaml
```

Or create a separate environment:

```powershell
conda env create -f environment.yml
conda run -n reaktoro-kinec-batch-runner python runner.py path\to\case_input.yaml
```

The output directory must not already exist. Output files are controlled by
the case YAML. The base package supports:

```text
manifest.json
diagnostics.json
timeseries.csv
mineral_summary.csv
aqueous_summary.csv
solver_history.csv
debug/mineral_connection.csv
plots/
```

Optional debug outputs include `debug/resolved_config.yaml` and
`debug/final_state.txt`.

Optional Objective 1 audit outputs are disabled unless explicitly enabled in
YAML:

```text
reaction_rates.csv
kinec_rate_validation.csv
carbon_inventory.csv
element_budget.csv
mineral_volume_change.csv
regime_classification.csv
surface_area_audit.csv
workflow_comparison.csv
secondary_mineral_assemblage.csv
surrogate_dataset.csv
validation_ledger.csv
porosity_permeability.csv
```

## Inputs

- Thermodynamic database: `data/thermo/Kinec_v3_4.dat`
- Runtime kinetic parameters: `data/kinetics/kinec_rates_minimal.yaml`
- Kinec adapter:
  `batch_runner/Kinect_Custom_Rates.py`

Copy `cases/schema_template.yaml` to create a case input. The template is
intentionally not runnable: replace every required placeholder with a value
from supplied data, supplied files, explicit user instruction, or
deterministic preprocessing.

Relative paths in a case config are resolved from the project root. Absolute
paths also work. A missing path stops the run and reports the exact resolved
path; the runner does not search other locations.

Cation exchange is planned but not implemented in V1.
Experiment validation is planned but not implemented in V1.
Transport is not implemented.

`cases/source_supported_kinetic_case.yaml` is a runnable functional Calcite
and finite-CO2 case copied from the values in the older user-supplied Kinec
notebook. It is not a calibrated experiment.

`cases/jayasekara_kinec_only_software_test.yaml` explicitly aliases three
legacy mineral names to approved Kinec names. It intentionally excludes
Goethite and Pyrite to remain Kinec-YAML-only and is not a full legacy
experiment reproduction.

`cases/calcite_quartz_illite_development.yaml` is the small source-supported
development case for fixed-fugacity initial equilibrium followed by closed
kinetics. Its one-second kinetic step currently takes several minutes in the
locally verified Reaktoro 2.13 environment.

On Windows, completed kinetic CLI runs exit immediately after all outputs are
closed to avoid a Reaktoro 2.13 Python rate-callback finalization crash.

## Case Rules

- `database.source` is `local` or `embedded`; no fallback is used.
- `co2.mode` is `disabled`, `finite`, or `fixed_fugacity`.
- `redox.apply_during` explicitly controls pE staging when redox is enabled.
- Mineral role `equilibrium` creates an equilibrium phase.
- Mineral role `kinetic` requires an initial amount, surface area, and Kinec
  YAML record.
- `kinetics` owns only kinetic enablement and the Kinec YAML path.
- `solver.workflow` explicitly controls equilibrium and kinetic constraint
  staging.
- `solver.timestep` owns duration and timestep control.
- Fixed timestep execution is implemented. Adaptive timesteps, long-horizon
  checkpoints/restart and smart solver backends live only in `docs/dev` until
  implemented; they are not active case-YAML fields in V1.
- Objective 1 audit outputs are active case-YAML fields. Budgets and
  inventories use only explicitly configured species/mineral stoichiometry.
  Reaction-rate diagnostics recompute the Kinec rate formula for accepted
  states. Mineral volume, porosity, permeability, and capillary-entry-pressure
  outputs report `not_evaluated` unless their required source-supported inputs
  or update laws are explicitly configured.

## Add a Small Feature

Add one config field, validate it, add direct execution logic in the relevant
small module, expose its output if needed, and add one focused test.
`runner.py` remains orchestration only.

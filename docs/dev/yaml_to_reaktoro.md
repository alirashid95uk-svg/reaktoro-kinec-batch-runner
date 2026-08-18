# YAML → Reaktoro Equivalent

`yaml_to_reaktoro.py` is a standalone audit tool for converting a case YAML into a readable Python script containing the direct Reaktoro construction and solver calls represented by that case.

It is deliberately separate from `batch_runner.simulator`: the generated file does not call `build_chemical_system()`, `build_chemical_state()`, `build_conditions()`, or `execute_solver()`. This makes the generated Python useful for reviewing the physical and geochemical assumptions encoded by a case.

## Usage

```powershell
python yaml_to_reaktoro.py cases\calcite_quartz_illite_development.yaml
```

Default output:

```text
cases/calcite_quartz_illite_development_reaktoro.py
```

Choose another output path:

```powershell
python yaml_to_reaktoro.py cases\calcite_quartz_illite_development.yaml -o review_case.py
```

Print without writing a file:

```powershell
python yaml_to_reaktoro.py cases\calcite_quartz_illite_development.yaml --stdout
```

## Coverage

The generator covers the active Reaktoro-facing case features: local/embedded PHREEQC databases, PHREEQC aqueous activities, finite-CO2 Peng–Robinson gas phases, fixed CO2 fugacity, pE staging, equilibrium/kinetic minerals, native Palandri–Kharaka kinetics, the project Kinec callback, all four workflow modes, fixed timestep execution, adaptive/adaptive-long-horizon execution, output/checkpoint target splitting, rollback, and configured adaptive state-acceptance checks.

Reporting-only blocks (`outputs`, `validation`, and non-solver postprocessing) are not emitted as Reaktoro code. A postprocessing requested-species list is included only when it directly participates in adaptive step acceptance.

## Unsupported-field safety

The generator is generic for data inside a supported feature: adding/removing minerals, aqueous elements, species, amounts, surface areas, schedules, or numerical values does not require generator changes.

Any unsupported physics field, solver field, activity model, workflow mode, kinetic model, or timestep mode has no defined Reaktoro mapping and is therefore a hard failure. The generator must never silently emit an incomplete script that appears physically equivalent.

Successful code generation means every Reaktoro-facing option in the case is either translated or explicitly known to be non-Reaktoro output metadata.

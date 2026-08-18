# Project Scope

## Runtime Scope

This project is a Reaktoro batch geochemical simulator for YAML-defined batch
equilibrium and kinetic cases using explicitly selected PHREEQC-style
thermodynamic databases.

Supported scientific/runtime capabilities include the features implemented by
the active strict schema, including configured CO2 conditions, pE-based redox,
mineral kinetics, timestep control, diagnostics, outputs, studies, and
Workbench orchestration.

Not currently supported as authoritative runtime features:

- reactive transport;
- cation exchange;
- automatic restart;
- automatic experimental calibration or experiment-fitting workflows.

The existing validation target/ledger functionality compares configured runtime
quantities with explicit targets; it is not an automated calibration system.

## Execution Boundary

Preserve the explicit batch-simulation chain:

```text
YAML
-> validation/resolution
-> PHREEQC database + kinetic parameters
-> chemical system
-> chemical state
-> solver
-> observations
-> outputs/diagnostics
```

This is batch geochemistry, not reactive transport.

## Scientific Inputs

The protected user-supplied scientific files are:

```text
data/thermo/Kinec_v3_4.dat
data/kinetics/kinec_rates_minimal.yaml
data/kinetics/PalandriKharaka_local.yaml
batch_runner/simulator/kinetics/kinec.py
```

`Kinec_v3_4.dat` is thermodynamic input. Runtime kinetic-rate inputs come from
the explicitly selected kinetics parameter file/model.

Do not change scientific content, values, units, mappings, boundary conditions,
or kinetic parameters without explicit scientific justification and
authorization.

Project-wide development and verification rules live in `AGENTS.md`; do not
duplicate them here.

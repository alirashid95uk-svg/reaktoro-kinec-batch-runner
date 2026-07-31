# Project Scope

## What This Project Does

This project is a simple Reaktoro batch simulation runner. It supports
YAML-defined batch equilibrium and batch kinetic cases using PHREEQC-style
thermodynamic databases. Cases may explicitly enable fixed-fugacity CO2,
finite-amount CO2, and pE-based redox.

Cation exchange is planned but not implemented in V1.
Experiment validation is planned but not implemented in V1.

The simple execution chain must remain explicit:

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

This is a design guide, not a reason to build complicated architecture.

## Thermodynamics and Kinetics

The user supplied these scientific files. They are now in their active project
locations. Do not delete them or modify their scientific content:

```text
data/thermo/Kinec_v3_4.dat
= local PHREEQC-style thermodynamic database

data/kinetics/kinec_rates_minimal.yaml
= optional custom Kinec kinetic-rate parameter file

data/kinetics/PalandriKharaka_local.yaml
= default native Palandri-Kharaka parameter file

batch_runner/Kinect_Custom_Rates.py
= Kinec YAML -> Reaktoro kinetic-rate adapter
```

Their scientific content must remain unchanged.

## First Runner Milestone

Run one YAML-defined batch kinetic case using:

- local PHREEQC-style thermodynamic database;
- native local Palandri-Kharaka parameters by default, or explicit custom Kinec parameters;
- explicit mineral amounts;
- explicit surface areas;
- standard outputs;
- no random scientific defaults.

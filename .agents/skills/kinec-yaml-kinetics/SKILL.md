---
name: kinec-yaml-kinetics
description: Use when adding or reviewing Kinec mineral kinetics; enforce cleaned-YAML-only runtime parameters, explicit surfaces, and direct Reaktoro reaction attachment.
---

# Kinec YAML Kinetics

Runtime Kinec kinetics use only:

```text
data/kinetics/kinec_rates_minimal.yaml
```

`data/thermo/Kinec_v3_4.dat` is a PHREEQC-style thermodynamic database. It is
not the runtime kinetic-rate input.

## Validation Rules

- Do not parse PHREEQC `RATES` blocks at runtime.
- Missing kinetic record = hard failure.
- Missing surface area for a kinetic mineral = hard failure.
- Missing thermodynamic mineral = hard failure.
- No silent skipping.
- No invented kinetic parameters.
- Validate the adapter against the exact Reaktoro overload used here before
  scientific use.

## Reaktoro 2.13 Contract

`ReactionRateModelKinec` returns a general `ReactionRateModel` and attaches it
to `MineralReaction`; it does not use the mineral-specific
`MineralReactionRateModel` callback. In the verified general-model binding:

- the callback returns total reaction rate in `mol/s`;
- the generated mineral coefficient is `-1`;
- a positive callback rate dissolves mineral and a negative rate precipitates;
- `ChemicalProps.surfaceArea(mineral)` is the live total area in `m2`.

Do not transfer a sign convention from a different Reaktoro callback type.
Run the executable contract check after changing the adapter, reaction
attachment, Reaktoro version, or surface construction:

```powershell
conda run -n fypr-reaktoro python -m pytest -q tests/test_first_version.py -k general_reaction_rate_contract
```

This check validates interface units and sign only. It does not validate Kinec
parameters, calibration, timestep convergence, or experimental agreement.

## Conceptual Usage

Keep the adapter direct and visible:

```python
params = KinecParams.local(config.kinetics.path)

reaction = MineralReaction(mineral_name)
reaction.setRateModel(ReactionRateModelKinec(params, mineral_name))

surface = MineralSurface(mineral_name, value, unit)
```

`ReactionRateModelKinec` and `KinecParams` are provided by the user-supplied
adapter, not by Reaktoro. The adapter should return the official Reaktoro
rate-model type required by the chosen `MineralReaction.setRateModel`
overload. Do not over-abstract this workflow.

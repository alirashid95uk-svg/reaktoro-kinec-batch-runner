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
- Validate the adapter's units and dissolution/precipitation sign against the
  exact Reaktoro reaction-rate interface before scientific use.

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

"""Explicit adaptive-step acceptance checks on a Reaktoro trial state."""

from __future__ import annotations

from math import inf, isfinite
from typing import Any, Callable

import reaktoro as rkt

from batch_runner.config import AdaptiveTimestepConfig, AmountChangeToleranceConfig, ResolvedCase
from batch_runner.simulator.mapping import _thermo_name


def evaluate_trial(
    case: ResolvedCase,
    system: Any,
    accepted_state: Any,
    trial_state: Any,
) -> dict[str, Any]:
    timestep = case.config.solver.timestep
    if not isinstance(timestep, AdaptiveTimestepConfig):
        raise TypeError("adaptive acceptance requires an adaptive timestep config")
    config = timestep.acceptance
    reasons: list[str] = []
    metrics: dict[str, Any] = {
        "delta_pH": None,
        "max_delta_saturation_index": None,
        "max_selected_species_change_mol": None,
        "max_selected_species_tolerance_ratio": None,
        "worst_selected_species": None,
        "max_mineral_change_mol": None,
        "max_mineral_tolerance_ratio": None,
        "worst_mineral": None,
        "minimum_species_amount_mol": None,
        "tolerated_negative_species_count": 0,
        "most_negative_tolerated_amount_mol": None,
        "max_element_balance_error_mol": None,
        "max_element_balance_error_ratio": None,
        "worst_element": None,
        "trial_charge_mol": None,
    }

    trial_amounts = [float(value) for value in trial_state.speciesAmounts()]
    metrics["minimum_species_amount_mol"] = min(trial_amounts, default=None)
    if config.negative_amount_tolerance_mol is not None:
        tolerance = config.negative_amount_tolerance_mol
        tolerated_negatives = [value for value in trial_amounts if -tolerance <= value < 0.0]
        metrics["tolerated_negative_species_count"] = len(tolerated_negatives)
        metrics["most_negative_tolerated_amount_mol"] = min(
            tolerated_negatives, default=None
        )
        if any(value < -tolerance for value in trial_amounts):
            reasons.append("negative_species_amount_below_tolerance")

    accepted_aqueous = rkt.AqueousProps(accepted_state)
    trial_aqueous = rkt.AqueousProps(trial_state)
    values_to_check = trial_amounts + [
        float(trial_state.temperature()),
        float(trial_state.pressure()),
    ]

    if config.max_delta_pH is not None or config.fail_on_non_finite:
        accepted_pH = float(accepted_aqueous.pH())
        trial_pH = float(trial_aqueous.pH())
        metrics["delta_pH"] = abs(trial_pH - accepted_pH)
        values_to_check.append(trial_pH)
        if config.max_delta_pH is not None and metrics["delta_pH"] > config.max_delta_pH:
            reasons.append("max_delta_pH")

    if config.max_delta_saturation_index is not None or config.fail_on_non_finite:
        saturation_changes = []
        for mineral in case.config.minerals:
            thermo_name = _thermo_name(mineral)
            accepted_si = float(accepted_aqueous.saturationIndex(thermo_name))
            trial_si = float(trial_aqueous.saturationIndex(thermo_name))
            saturation_changes.append(abs(trial_si - accepted_si))
            values_to_check.append(trial_si)
        metrics["max_delta_saturation_index"] = max(saturation_changes, default=0.0)
        if (
            config.max_delta_saturation_index is not None
            and metrics["max_delta_saturation_index"] > config.max_delta_saturation_index
        ):
            reasons.append("max_delta_saturation_index")

    if config.selected_species_change is not None:
        change = _max_change_ratio(
            case.config.postprocessing.requested_species,
            lambda state, name: float(state.speciesAmount(name)),
            accepted_state,
            trial_state,
            config.selected_species_change,
        )
        metrics["max_selected_species_change_mol"] = change["max_change_mol"]
        metrics["max_selected_species_tolerance_ratio"] = change["max_ratio"]
        metrics["worst_selected_species"] = change["worst_name"]
        if change["max_ratio"] > 1.0:
            reasons.append("selected_species_change_tolerance")

    if config.mineral_change is not None:
        minerals = {mineral.name: _thermo_name(mineral) for mineral in case.config.minerals}
        change = _max_change_ratio(
            list(minerals),
            lambda state, name: float(state.speciesAmount(minerals[name])),
            accepted_state,
            trial_state,
            config.mineral_change,
        )
        metrics["max_mineral_change_mol"] = change["max_change_mol"]
        metrics["max_mineral_tolerance_ratio"] = change["max_ratio"]
        metrics["worst_mineral"] = change["worst_name"]
        if change["max_ratio"] > 1.0:
            reasons.append("mineral_change_tolerance")

    if config.element_conservation.enabled:
        before = [float(value) for value in accepted_state.elementAmounts()]
        after = [float(value) for value in trial_state.elementAmounts()]
        values_to_check.extend(after)
        relative = config.element_conservation.relative_tolerance or 0.0
        absolute = config.element_conservation.absolute_tolerance_mol or 0.0
        deltas = [abs(trial - accepted) for accepted, trial in zip(before, after)]
        allowed = [absolute + relative * abs(accepted) for accepted in before]
        ratios = [
            delta / limit if limit > 0.0 else (0.0 if delta == 0.0 else inf)
            for delta, limit in zip(deltas, allowed)
        ]
        worst_index = max(range(len(deltas)), key=deltas.__getitem__, default=None)
        metrics["max_element_balance_error_mol"] = max(deltas, default=0.0)
        metrics["max_element_balance_error_ratio"] = max(ratios, default=0.0)
        if worst_index is not None:
            metrics["worst_element"] = str(list(system.elements())[worst_index].name())
        if any(delta > limit for delta, limit in zip(deltas, allowed)):
            reasons.append("element_conservation")

    metrics["trial_charge_mol"] = float(trial_state.charge())
    values_to_check.append(metrics["trial_charge_mol"])
    if config.fail_on_non_finite and any(not isfinite(value) for value in values_to_check):
        reasons.append("non_finite_state_value")

    return {
        "accepted": not reasons,
        "acceptance_reason": "accepted" if not reasons else ";".join(dict.fromkeys(reasons)),
        **metrics,
    }


def _max_change_ratio(
    names: list[str],
    amount: Callable[[Any, str], float],
    accepted_state: Any,
    trial_state: Any,
    tolerance: AmountChangeToleranceConfig,
) -> dict[str, Any]:
    changes: list[tuple[float, float, str]] = []
    for name in names:
        before = amount(accepted_state, name)
        after = amount(trial_state, name)
        delta = abs(after - before)
        allowed = tolerance.absolute_tolerance_mol + tolerance.relative_tolerance * max(
            abs(before), tolerance.reference_floor_mol
        )
        ratio = delta / allowed if allowed > 0.0 else (0.0 if delta == 0.0 else inf)
        changes.append((ratio, delta, name))
    if not changes:
        return {"max_ratio": 0.0, "max_change_mol": 0.0, "worst_name": None}
    worst = max(changes, key=lambda item: item[0])
    return {
        "max_ratio": worst[0],
        "max_change_mol": max(item[1] for item in changes),
        "worst_name": worst[2],
    }

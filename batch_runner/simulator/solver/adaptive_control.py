"""Pure control logic for error-controlled, event-aware adaptive stepping."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any

import reaktoro as rkt

from batch_runner.config import ResolvedCase


@dataclass(frozen=True)
class RichardsonEstimate:
    norm: float
    variable_count: int
    worst_variable: str | None
    worst_ratio: float


@dataclass(frozen=True)
class EventSnapshot:
    time_s: float
    values: dict[str, float]


@dataclass(frozen=True)
class EventLimit:
    dt_s: float
    reason: str


def controlled_amounts(case: ResolvedCase, state: Any) -> dict[str, float]:
    """Extract amounts used by Richardson error control.

    Empty configured lists mean "use project defaults": requested aqueous species
    and all kinetic minerals. Explicit lists narrow the controlled set.
    """
    cfg = case.config.solver.timestep.error_control
    species_names = list(cfg.controlled_species)
    if not species_names:
        species_names = list(case.config.postprocessing.requested_species)

    mineral_names = list(cfg.controlled_minerals)
    if not mineral_names:
        mineral_names = [
            mineral.name for mineral in case.config.minerals if mineral.role == "kinetic"
        ]

    values: dict[str, float] = {}
    for name in _unique(species_names):
        values[f"species::{name}"] = float(state.speciesAmount(name))
    for name in _unique(mineral_names):
        index = _solid_species_index(state.system(), name)
        values[f"mineral::{name}"] = float(state.speciesAmount(index))
    if not values:
        raise ValueError(
            "adaptive error control requires at least one controlled aqueous species "
            "or kinetic mineral"
        )
    return values


def richardson_estimate(
    case: ResolvedCase,
    full_state: Any,
    half_state: Any,
) -> RichardsonEstimate:
    """Estimate local temporal error from one full step and two half steps."""
    cfg = case.config.solver.timestep.error_control
    if not cfg.enabled or cfg.temporal_order is None:
        raise ValueError("Richardson error estimation requires enabled error_control")

    full = controlled_amounts(case, full_state)
    half = controlled_amounts(case, half_state)
    if full.keys() != half.keys():
        raise ValueError("Richardson trial states expose different controlled variables")

    denominator = (2.0 ** float(cfg.temporal_order)) - 1.0
    if denominator <= 0.0 or not isfinite(denominator):
        raise ValueError("invalid Richardson denominator from temporal_order")

    worst_variable: str | None = None
    worst_ratio = -1.0
    for key in full:
        a = full[key]
        b = half[key]
        if not isfinite(a) or not isfinite(b):
            return RichardsonEstimate(float("inf"), len(full), key, float("inf"))

        absolute_tolerance = (
            cfg.species_absolute_tolerance_mol
            if key.startswith("species::")
            else cfg.mineral_absolute_tolerance_mol
        )
        scale = absolute_tolerance + cfg.relative_tolerance * abs(b)
        if scale <= 0.0 or not isfinite(scale):
            raise ValueError(f"invalid error-control scale for {key}")

        error = abs(b - a) / denominator
        ratio = error / scale
        if ratio > worst_ratio:
            worst_ratio = ratio
            worst_variable = key

    return RichardsonEstimate(
        norm=max(0.0, worst_ratio),
        variable_count=len(full),
        worst_variable=worst_variable,
        worst_ratio=max(0.0, worst_ratio),
    )


def error_rejection_dt(current_dt_s: float, error_norm: float, cfg: Any) -> float:
    """Reduced retry step after a completed trial fails the tolerance test."""
    if not isfinite(error_norm) or error_norm <= 0.0:
        factor = cfg.min_reduction_factor
    else:
        exponent = 1.0 / (float(cfg.temporal_order) + 1.0)
        factor = (cfg.safety_factor / error_norm) ** exponent
        factor = min(0.9, max(cfg.min_reduction_factor, factor))
    return current_dt_s * factor


def controller_dt(
    current_dt_s: float,
    error_norm: float,
    previous_error_norm: float | None,
    cfg: Any,
) -> tuple[float, str]:
    """Return the unconstrained next step from the startup I or PI controller."""
    k = float(cfg.temporal_order) + 1.0
    safe_error = max(error_norm, 1.0e-300)
    if previous_error_norm is None or previous_error_norm <= 0.0:
        exponent = cfg.startup_normalized_gain / k
        factor = (cfg.safety_factor / safe_error) ** exponent
        kind = "startup_i"
    else:
        previous = max(previous_error_norm, 1.0e-300)
        present_exponent = (
            cfg.pi_normalized_integral_gain + cfg.pi_normalized_proportional_gain
        ) / k
        previous_exponent = -cfg.pi_normalized_proportional_gain / k
        factor = (
            (cfg.safety_factor / safe_error) ** present_exponent
            * (cfg.safety_factor / previous) ** previous_exponent
        )
        kind = "pi"

    factor = min(
        cfg.max_growth_factor,
        max(cfg.min_reduction_factor, factor),
    )
    return current_dt_s * factor, kind


def event_snapshot(case: ResolvedCase, state: Any, time_s: float) -> EventSnapshot:
    cfg = case.config.solver.timestep.event_control
    if not cfg.enabled:
        return EventSnapshot(time_s=float(time_s), values={})

    names = list(cfg.minerals)
    if not names:
        names = [
            mineral.name for mineral in case.config.minerals if mineral.role == "kinetic"
        ]
    available = {mineral.name for mineral in case.config.minerals}
    unknown = sorted(set(names) - available)
    if unknown:
        raise ValueError(
            "event_control references minerals not present in case: " + ", ".join(unknown)
        )

    values: dict[str, float] = {}
    aqueous = rkt.AqueousProps(state) if cfg.saturation_crossing else None
    for name in _unique(names):
        if cfg.mineral_exhaustion:
            index = _solid_species_index(state.system(), name)
            values[f"amount::{name}"] = float(state.speciesAmount(index))
        if cfg.saturation_crossing:
            values[f"si::{name}"] = float(aqueous.saturationIndex(name))
    return EventSnapshot(time_s=float(time_s), values=values)


def predict_event_limit(
    previous: EventSnapshot | None,
    current: EventSnapshot,
    cfg: Any,
) -> EventLimit | None:
    """Predict the next zero crossing from two accepted states using a secant slope."""
    if previous is None or not current.values:
        return None
    history_dt = current.time_s - previous.time_s
    if history_dt <= 0.0:
        return None

    best: EventLimit | None = None
    for key, current_value in current.values.items():
        previous_value = previous.values.get(key)
        if previous_value is None:
            continue
        if not isfinite(current_value) or not isfinite(previous_value):
            continue
        slope = (current_value - previous_value) / history_dt
        if slope == 0.0:
            continue

        if key.startswith("amount::"):
            threshold = cfg.mineral_amount_event_tolerance_mol
            if current_value <= threshold or slope >= 0.0:
                continue
            dt_s = (threshold - current_value) / slope
        else:
            if abs(current_value) <= cfg.saturation_index_event_tolerance:
                continue
            dt_s = -current_value / slope

        if dt_s <= 0.0 or not isfinite(dt_s):
            continue
        candidate = EventLimit(dt_s=dt_s, reason=f"predicted_zero_crossing:{key}")
        if best is None or candidate.dt_s < best.dt_s:
            best = candidate
    return best


def event_overshoot_correction(
    start: EventSnapshot,
    trial: EventSnapshot,
    dt_s: float,
    cfg: Any,
) -> EventLimit | None:
    """Detect an event crossed inside a completed trial and interpolate a retry step."""
    best: EventLimit | None = None
    for key, start_value in start.values.items():
        trial_value = trial.values.get(key)
        if trial_value is None:
            continue
        if not isfinite(start_value) or not isfinite(trial_value):
            continue

        fraction: float | None = None
        if key.startswith("amount::"):
            threshold = cfg.mineral_amount_event_tolerance_mol
            if start_value > threshold and trial_value < threshold:
                denominator = start_value - trial_value
                if denominator > 0.0:
                    fraction = (start_value - threshold) / denominator
        else:
            tolerance = cfg.saturation_index_event_tolerance
            if (
                abs(start_value) > tolerance
                and abs(trial_value) > tolerance
                and start_value * trial_value < 0.0
            ):
                fraction = abs(start_value) / (abs(start_value) + abs(trial_value))

        if fraction is None or not 0.0 < fraction < 1.0:
            continue
        corrected = dt_s * fraction
        # Treat a root at the trial endpoint as a successful landing, not an overshoot.
        if corrected >= dt_s * (1.0 - 1.0e-10):
            continue
        candidate = EventLimit(
            dt_s=corrected,
            reason=f"corrected_zero_crossing:{key}",
        )
        if best is None or candidate.dt_s < best.dt_s:
            best = candidate
    return best


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _solid_species_index(system: Any, name: str) -> int:
    species_index = 0
    for phase in system.phases():
        phase_species = phase.species()
        if phase.name() == name:
            if len(phase_species) != 1:
                raise ValueError(f"mineral phase is not a pure phase: {name}")
            return species_index
        species_index += len(phase_species)
    raise ValueError(f"mineral phase is not present in the chemical system: {name}")

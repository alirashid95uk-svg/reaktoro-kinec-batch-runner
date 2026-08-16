"""Explicit thermodynamic and kinetic-parameter connection validation."""

from typing import Any

import reaktoro as rkt

from batch_runner.config import ResolvedCase
from batch_runner.simulator.kinetics.parameters import parameter_record_names


def build_kinetic_mapping(
    case: ResolvedCase,
    database: Any,
    params: Any | None,
) -> list[dict[str, Any]]:
    parameter_names = parameter_record_names(case, params)
    rows: list[dict[str, Any]] = []
    for mineral in case.config.minerals:
        thermodynamic_found = _is_thermodynamic_mineral(database, mineral.name)
        record_found = mineral.role == "kinetic" and mineral.name in parameter_names
        surface_present = mineral.surface_area is not None

        failures = []
        if not thermodynamic_found:
            failures.append("missing thermodynamic mineral")
        if mineral.role == "kinetic" and not record_found:
            failures.append(
                f"missing {case.config.kinetics.model} parameter record"
            )
        if mineral.role == "kinetic" and not surface_present:
            failures.append("missing surface area")

        if failures:
            status = "failed"
            reason = "; ".join(failures)
        elif mineral.role == "equilibrium":
            status = "active"
            reason = "equilibrium mineral; no kinetic record required"
        else:
            status = "active"
            reason = "configured connection checks passed"

        rows.append(
            {
                "case_name": case.config.case.name,
                "mineral_name": mineral.name,
                "role": mineral.role,
                "kinetic_model": (
                    case.config.kinetics.model if mineral.role == "kinetic" else None
                ),
                "thermodynamic_mineral_found": thermodynamic_found,
                "kinetic_parameter_record_found": record_found,
                "surface_area_present": surface_present,
                "status": status,
                "reason": reason,
            }
        )
    return rows


def require_valid_kinetic_mapping(mapping: list[dict[str, Any]]) -> None:
    failures = [row for row in mapping if row["status"] == "failed"]
    if failures:
        details = "; ".join(f"{row['mineral_name']}: {row['reason']}" for row in failures)
        if any(not row["thermodynamic_mineral_found"] for row in failures):
            raise ValueError(f"system construction: mineral connection validation failed: {details}")
        raise ValueError(f"kinetic attachment: mineral connection validation failed: {details}")


def _require_thermodynamic_mineral(database: Any, name: str) -> None:
    try:
        species = database.species(name)
    except RuntimeError as exc:
        raise ValueError(f"missing thermodynamic mineral: {name}") from exc
    if species.aggregateState() != rkt.AggregateState.Solid:
        raise ValueError(f"configured mineral is not a solid thermodynamic species: {name}")


def _is_thermodynamic_mineral(database: Any, name: str) -> bool:
    try:
        species = database.species(name)
    except RuntimeError:
        return False
    return species.aggregateState() == rkt.AggregateState.Solid

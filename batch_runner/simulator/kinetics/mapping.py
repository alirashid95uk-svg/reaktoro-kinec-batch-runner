"""Audit the exact connection between configured minerals and runtime inputs.

The preparation stage uses these rows both as debug evidence and as a hard
precondition for chemical-system construction.  Mineral names are exact: this
module performs no aliasing or fallback between thermodynamic and kinetic
records.
"""

from typing import Any

import reaktoro as rkt

from batch_runner.config import ResolvedCase
from batch_runner.simulator.kinetics.parameters import parameter_record_names


def build_kinetic_mapping(
    case: ResolvedCase,
    database: Any,
    params: Any | None,
) -> list[dict[str, Any]]:
    """Describe connection status for every configured mineral.

    Returns one deterministic row per mineral covering database presence,
    kinetic-parameter presence where required, and configured surface area.
    The function reports failures but does not raise; call
    :func:`require_valid_kinetic_mapping` to enforce them.
    """
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
    """Raise when any connection row reports ``status == 'failed'``.

    Thermodynamic lookup failures are identified as system-construction errors;
    missing parameter records or surface areas are identified as kinetic
    attachment errors.  The mapping is otherwise left unchanged.
    """
    failures = [row for row in mapping if row["status"] == "failed"]
    if failures:
        details = "; ".join(f"{row['mineral_name']}: {row['reason']}" for row in failures)
        if any(not row["thermodynamic_mineral_found"] for row in failures):
            raise ValueError(f"system construction: mineral connection validation failed: {details}")
        raise ValueError(f"kinetic attachment: mineral connection validation failed: {details}")


def _require_thermodynamic_mineral(database: Any, name: str) -> None:
    if not _is_thermodynamic_mineral(database, name):
        raise ValueError(f"missing thermodynamic mineral: {name}")


def _is_thermodynamic_mineral(database: Any, name: str) -> bool:
    """Return whether *name* exists as a solid thermodynamic species."""
    return any(
        species.name() == name
        for species in database.speciesWithAggregateState(rkt.AggregateState.Solid)
    )

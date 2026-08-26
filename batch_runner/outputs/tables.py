"""Project accepted result records into stable CSV schemas.

Column selection is derived from resolved postprocessing/output configuration.
Rows are deterministic views of existing accepted observations and solver
history; this module performs no Reaktoro calls or temporal interpolation.
Summary interpretations describe batch amount changes only.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from batch_runner.config import ResolvedCase

if TYPE_CHECKING:
    from batch_runner.simulator import SimulationResult


TIMESERIES_CORE_COLUMNS = [
    "time_s",
    "time_days",
    "stage",
    "pH",
    "ionic_strength_molal",
    "alkalinity_eq_per_l",
]
TIMESERIES_SOLVER_COLUMNS = ["solver_succeeded", "solver_iterations", "dt_s"]
SOLVER_HISTORY_COLUMNS = [
    "step_index",
    "attempt_index",
    "time_start_s",
    "time_end_s",
    "dt_s",
    "stage",
    "accepted",
    "solver_succeeded",
    "iterations",
    "wall_time_s",
    "failure_reason",
    "next_dt_s",
]
ERROR_CONTROL_SOLVER_HISTORY_COLUMNS = SOLVER_HISTORY_COLUMNS + [
    "timestep_mode",
    "accepted_time_before_s",
    "accepted_time_after_s",
    "proposed_dt_s",
    "effective_dt_s",
    "full_step_succeeded",
    "first_half_step_succeeded",
    "second_half_step_succeeded",
    "full_step_iterations",
    "first_half_step_iterations",
    "second_half_step_iterations",
    "full_step_wall_time_s",
    "first_half_step_wall_time_s",
    "second_half_step_wall_time_s",
    "reaktoro_solve_calls",
    "richardson_error",
    "worst_controlled_mineral",
    "raw_error_mol",
    "error_tolerance_mol",
    "scaled_error",
    "rejection_reason",
    "solver_failure",
    "temporal_error_rejection",
    "event_cap_type",
    "event_target_time_s",
    "retry_count",
    "solver_reconstruction",
    "controller_history_reset",
]
MINERAL_SUMMARY_COLUMNS = [
    "mineral",
    "initial_amount_mol",
    "final_amount_mol",
    "delta_mol",
    "delta_percent",
    "initial_SI",
    "final_SI",
    "final_saturation_state",
    "net_change",
]
AQUEOUS_SUMMARY_COLUMNS = [
    "species",
    "initial_amount_mol",
    "final_amount_mol",
    "delta_amount_mol",
    "initial_molality_mol_kgw",
    "final_molality_mol_kgw",
    "delta_molality_mol_kgw",
    "delta_percent",
    "interpretation",
]


def timeseries_columns(case: ResolvedCase) -> list[str]:
    """Return ordered timeseries columns selected by output configuration."""
    config = case.config
    output = config.outputs.timeseries
    columns = list(TIMESERIES_CORE_COLUMNS)
    if output.include_species_amounts:
        columns.extend(
            f"species_amount_mol::{name}" for name in config.postprocessing.requested_species
        )
    if output.include_species_molalities:
        columns.extend(
            f"species_molality_mol_kgw::{name}" for name in config.postprocessing.requested_species
        )
    columns.extend(
        f"element_molality_mol_kgw::{name}" for name in config.postprocessing.requested_elements
    )
    if output.include_mineral_amounts:
        columns.extend(
            f"mineral_amount_mol::{name}" for name in config.postprocessing.requested_minerals
        )
    if output.include_mineral_deltas:
        columns.extend(
            f"mineral_delta_mol::{name}" for name in config.postprocessing.requested_minerals
        )
    if output.include_saturation_indices:
        columns.extend(
            f"saturation_index::{name}" for name in config.postprocessing.requested_minerals
        )
    if output.include_solver_columns:
        columns.extend(TIMESERIES_SOLVER_COLUMNS)
    return columns


def solver_history_columns(case: ResolvedCase) -> list[str]:
    """Return the base or Richardson-extended solver-history schema."""
    return (
        ERROR_CONTROL_SOLVER_HISTORY_COLUMNS
        if case.config.solver.timestep.mode == "adaptive_error_controlled"
        else SOLVER_HISTORY_COLUMNS
    )


def timeseries_rows(case: ResolvedCase, result: SimulationResult) -> Iterator[dict[str, Any]]:
    """Yield configured timeseries columns from accepted result rows.

    Raises:
        KeyError: A required observation column is missing, indicating a schema
            mismatch between runtime collection and output configuration.
    """
    columns = timeseries_columns(case)
    for row in result.iter_rows():
        yield {column: row[column] for column in columns}


def mineral_summary_rows(case: ResolvedCase, result: SimulationResult) -> list[dict[str, Any]]:
    """Summarize initial-to-final mineral amounts and saturation indices.

    Amounts and changes are mol; percentage change is undefined when the
    initial amount is zero.  ``net_change`` reports the sign of the batch amount
    difference and does not imply transport or fracture-scale behaviour.
    """
    initial = result.initial_row
    final = result.final_row
    rows = []
    for name in case.config.postprocessing.requested_minerals:
        initial_amount = initial[f"mineral_amount_mol::{name}"]
        final_amount = final[f"mineral_amount_mol::{name}"]
        delta = final_amount - initial_amount
        delta_percent, net_change = _change_interpretation(initial_amount, final_amount)
        final_si = final[f"saturation_index::{name}"]
        rows.append(
            {
                "mineral": name,
                "initial_amount_mol": initial_amount,
                "final_amount_mol": final_amount,
                "delta_mol": delta,
                "delta_percent": delta_percent,
                "initial_SI": initial[f"saturation_index::{name}"],
                "final_SI": final_si,
                "final_saturation_state": _saturation_state(final_si),
                "net_change": net_change,
            }
        )
    return rows


def aqueous_summary_rows(case: ResolvedCase, result: SimulationResult) -> list[dict[str, Any]]:
    """Summarize initial-to-final requested species amounts and molalities.

    Amounts are mol and molalities mol/kgw.  Percentage change uses species
    amount and remains undefined for a zero initial inventory.
    """
    initial = result.initial_row
    final = result.final_row
    rows = []
    for name in case.config.postprocessing.requested_species:
        initial_amount = initial[f"species_amount_mol::{name}"]
        final_amount = final[f"species_amount_mol::{name}"]
        initial_molality = initial[f"species_molality_mol_kgw::{name}"]
        final_molality = final[f"species_molality_mol_kgw::{name}"]
        delta_percent, interpretation = _aqueous_change_interpretation(initial_amount, final_amount)
        rows.append(
            {
                "species": name,
                "initial_amount_mol": initial_amount,
                "final_amount_mol": final_amount,
                "delta_amount_mol": final_amount - initial_amount,
                "initial_molality_mol_kgw": initial_molality,
                "final_molality_mol_kgw": final_molality,
                "delta_molality_mol_kgw": final_molality - initial_molality,
                "delta_percent": delta_percent,
                "interpretation": interpretation,
            }
        )
    return rows


def write_csv(path: Path, columns: list[str], rows: Iterable[dict[str, Any]]) -> None:
    """Write one UTF-8 CSV with deterministic column order.

    Parent-directory creation is intentionally owned by output orchestration.
    I/O errors and unexpected row keys propagate to its partial-package handler.
    """
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _change_interpretation(initial: float, final: float) -> tuple[float | None, str]:
    delta = final - initial
    if initial == 0:
        return (None, "precipitation_from_zero" if final > 0 else "unchanged_zero")
    if delta > 0:
        interpretation = "precipitation"
    elif delta < 0:
        interpretation = "dissolution"
    else:
        interpretation = "unchanged"
    return 100.0 * delta / initial, interpretation


def _aqueous_change_interpretation(initial: float, final: float) -> tuple[float | None, str]:
    delta = final - initial
    if initial == 0:
        return (None, "increase_from_zero" if final > 0 else "unchanged_zero")
    if delta > 0:
        interpretation = "increase"
    elif delta < 0:
        interpretation = "decrease"
    else:
        interpretation = "unchanged"
    return 100.0 * delta / initial, interpretation


def _saturation_state(si: float) -> str:
    if si < 0:
        return "undersaturated"
    if si > 0:
        return "supersaturated"
    return "near_equilibrium"

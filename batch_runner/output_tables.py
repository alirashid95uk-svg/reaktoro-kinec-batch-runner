"""Deterministic output-table construction."""

from __future__ import annotations

import csv
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from batch_runner.config import ResolvedCase
from batch_runner.simulator.simulation import SimulationResult


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
    "acceptance_reason",
    "next_dt_s",
    "delta_pH",
    "max_delta_saturation_index",
    "max_selected_species_change_mol",
    "max_selected_species_tolerance_ratio",
    "worst_selected_species",
    "max_mineral_change_mol",
    "max_mineral_tolerance_ratio",
    "worst_mineral",
    "minimum_species_amount_mol",
    "tolerated_negative_species_count",
    "most_negative_tolerated_amount_mol",
    "max_element_balance_error_mol",
    "max_element_balance_error_ratio",
    "worst_element",
    "trial_charge_mol",
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


def timeseries_rows(case: ResolvedCase, result: SimulationResult) -> Iterator[dict[str, Any]]:
    columns = timeseries_columns(case)
    for row in result.iter_rows():
        yield {column: row[column] for column in columns}


def mineral_summary_rows(case: ResolvedCase, result: SimulationResult) -> list[dict[str, Any]]:
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

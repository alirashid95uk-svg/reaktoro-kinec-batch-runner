"""Config-controlled plots reproducible from result rows."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from batch_runner.config import ResolvedCase
from batch_runner.simulator.simulation import SimulationResult


def write_plots(case: ResolvedCase, result: SimulationResult, plots_dir: Path) -> list[Path]:
    config = case.config.outputs.plots
    if not config.enabled:
        return []
    plots_dir.mkdir(parents=True, exist_ok=True)
    written = []
    if config.pH:
        written.append(_plot_pH(result.iter_rows(), plots_dir / "pH_vs_time.png"))
    if config.mineral_change:
        written.append(
            _plot_mineral_change(
                result.iter_rows(),
                case.config.postprocessing.requested_minerals,
                result.initial_row,
                plots_dir / "mineral_change_vs_time.png",
            )
        )
    if config.saturation_index:
        written.append(
            _plot_saturation_indices(
                result.iter_rows(),
                case.config.postprocessing.requested_minerals,
                plots_dir / "saturation_index_vs_time.png",
            )
        )
    if config.solver_dt:
        written.append(_plot_solver_value(result.iter_solver_history(), "dt_s", plots_dir / "solver_dt_vs_time.png"))
    if config.solver_iterations:
        written.append(
            _plot_solver_value(
                result.iter_solver_history(),
                "iterations",
                plots_dir / "solver_iterations_vs_time.png",
            )
        )
    return written


def _plot_pH(rows: Iterable[dict[str, Any]], path: Path) -> Path:
    times = []
    values = []
    for row in rows:
        times.append(row["time_days"])
        values.append(row["pH"])
    fig, axis = plt.subplots()
    axis.plot(times, values)
    axis.set(xlabel="Time (days)", ylabel="pH")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def _plot_mineral_change(
    rows: Iterable[dict[str, Any]],
    names: list[str],
    initial_row: dict[str, Any],
    path: Path,
) -> Path:
    initial = {name: initial_row[f"mineral_amount_mol::{name}"] for name in names}
    times = []
    values = {name: [] for name in names if initial[name] != 0}
    for row in rows:
        times.append(row["time_days"])
        for name in values:
            values[name].append(100.0 * row[f"mineral_delta_mol::{name}"] / initial[name])
    fig, axis = plt.subplots()
    for name, mineral_values in values.items():
        axis.plot(times, mineral_values, label=name)
    axis.set(xlabel="Time (days)", ylabel="Mineral change (%)")
    if axis.lines:
        axis.legend()
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def _plot_saturation_indices(rows: Iterable[dict[str, Any]], names: list[str], path: Path) -> Path:
    times = []
    values = {name: [] for name in names}
    for row in rows:
        times.append(row["time_days"])
        for name in names:
            values[name].append(row[f"saturation_index::{name}"])
    fig, axis = plt.subplots()
    for name in names:
        axis.plot(times, values[name], label=name)
    axis.axhline(0.0, color="black", linewidth=0.8)
    axis.set(xlabel="Time (days)", ylabel="Saturation index")
    if axis.lines:
        axis.legend()
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def _plot_solver_value(records: Iterable[dict[str, Any]], column: str, path: Path) -> Path:
    times = []
    values = []
    for row in records:
        if row[column] is not None:
            times.append(row["time_end_s"])
            values.append(row[column])
    fig, axis = plt.subplots()
    axis.plot(times, values)
    axis.set(xlabel="Time (s)", ylabel=column)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path

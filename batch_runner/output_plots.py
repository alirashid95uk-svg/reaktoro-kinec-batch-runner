"""Config-controlled plots reproducible from result rows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from batch_runner.config import ResolvedCase
from batch_runner.simulation import SimulationResult


def write_plots(case: ResolvedCase, result: SimulationResult, plots_dir: Path) -> list[Path]:
    config = case.config.outputs.plots
    if not config.enabled:
        return []
    plots_dir.mkdir(parents=True, exist_ok=True)
    written = []
    if config.pH:
        written.append(_plot_pH(result.rows, plots_dir / "pH_vs_time.png"))
    if config.mineral_change:
        written.append(
            _plot_mineral_change(
                result.rows,
                case.config.postprocessing.requested_minerals,
                plots_dir / "mineral_change_vs_time.png",
            )
        )
    if config.saturation_index:
        written.append(
            _plot_saturation_indices(
                result.rows,
                case.config.postprocessing.requested_minerals,
                plots_dir / "saturation_index_vs_time.png",
            )
        )
    if config.solver_dt:
        written.append(_plot_solver_value(result.solver_history, "dt_s", plots_dir / "solver_dt_vs_time.png"))
    if config.solver_iterations:
        written.append(
            _plot_solver_value(
                result.solver_history,
                "iterations",
                plots_dir / "solver_iterations_vs_time.png",
            )
        )
    return written


def _plot_pH(rows: list[dict[str, Any]], path: Path) -> Path:
    fig, axis = plt.subplots()
    axis.plot([row["time_days"] for row in rows], [row["pH"] for row in rows])
    axis.set(xlabel="Time (days)", ylabel="pH")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def _plot_mineral_change(rows: list[dict[str, Any]], names: list[str], path: Path) -> Path:
    fig, axis = plt.subplots()
    for name in names:
        initial = rows[0][f"mineral_amount_mol::{name}"]
        if initial == 0:
            continue
        values = [100.0 * row[f"mineral_delta_mol::{name}"] / initial for row in rows]
        axis.plot([row["time_days"] for row in rows], values, label=name)
    axis.set(xlabel="Time (days)", ylabel="Mineral change (%)")
    if axis.lines:
        axis.legend()
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def _plot_saturation_indices(rows: list[dict[str, Any]], names: list[str], path: Path) -> Path:
    fig, axis = plt.subplots()
    for name in names:
        axis.plot(
            [row["time_days"] for row in rows],
            [row[f"saturation_index::{name}"] for row in rows],
            label=name,
        )
    axis.axhline(0.0, color="black", linewidth=0.8)
    axis.set(xlabel="Time (days)", ylabel="Saturation index")
    if axis.lines:
        axis.legend()
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def _plot_solver_value(records: list[dict[str, Any]], column: str, path: Path) -> Path:
    rows = [row for row in records if row[column] is not None]
    fig, axis = plt.subplots()
    axis.plot([row["time_end_s"] for row in rows], [row[column] for row in rows])
    axis.set(xlabel="Time (s)", ylabel=column)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path

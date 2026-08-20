"""Generate Jayasekara measured-vs-Reaktoro pH and ICP figures.

The plotting parameters are preserved from the preliminary FYPR code:
- pH styling from ``jayasekara_reaktoro/figures.py`` (600 dpi),
- split ICP styling from ``Plots/plotting.ipynb`` (300 dpi).

The Reaktoro timeseries must contain pH and aqueous element molalities named
``element_molality_mol_kgw::<Element>`` at the Jayasekara observation times.
Element molality is converted to ppm using the same preliminary-code formula:

    ppm = molality [mol/kgw] * atomic mass [g/mol] * 1000

This is therefore mg/kg water (reported as ppm), with no density correction to
mg/L. That definition is intentionally preserved for reproducibility.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TIMESERIES = PROJECT_ROOT / "outputs" / "jayasekara_2020_reproduction" / "timeseries.csv"
DEFAULT_EXPERIMENT = PROJECT_ROOT / "data" / "experimental" / "jayasekara_2020_digitized.csv"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "jayasekara_2020_reproduction" / "validation_figures"

OBSERVATION_WEEKS = (2.0, 5.0, 9.0, 18.0, 37.0)
ELEMENT_MOLAR_MASS_G_MOL = {
    "Si": 28.0855,
    "Al": 26.9815385,
    "Fe": 55.845,
    "Ca": 40.078,
    "Mg": 24.305,
    "K": 39.0983,
    "Na": 22.98976928,
}
ICP_STYLES = {
    "Si": {"marker": "x", "color": "#1f77b4"},
    "Al": {"marker": "+", "color": "#ff7f0e"},
    "Fe": {"marker": "*", "color": "#7f7f7f"},
    "Ca": {"marker": "o", "color": "#f2b701"},
    "Mg": {"marker": "^", "color": "#4169b1"},
    "K": {"marker": "s", "color": "#5aa645"},
    "Na": {"marker": "o", "color": "#1b5e93"},
}
ICP_GROUPS = {
    "ICP_Na_Ca_Mg.png": ("Na", "Ca", "Mg"),
    "ICP_Si_Al_Fe_K.png": ("Si", "Al", "Fe", "K"),
}


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _model_rows_by_week(timeseries_rows: Iterable[dict[str, str]]) -> dict[float, dict[str, str]]:
    rows = list(timeseries_rows)
    selected: dict[float, dict[str, str]] = {}
    for target_week in OBSERVATION_WEEKS:
        target_days = target_week * 7.0
        matches = [
            row
            for row in rows
            if row.get("time_days") not in (None, "")
            and abs(float(row["time_days"]) - target_days) <= 1.0e-9
        ]
        if len(matches) != 1:
            raise ValueError(
                f"expected exactly one model row at week {target_week:g} "
                f"({target_days:g} days), found {len(matches)}"
            )
        selected[target_week] = matches[0]
    return selected


def build_ph_comparison(
    timeseries_rows: Iterable[dict[str, str]], measured_rows: Iterable[dict[str, str]]
) -> list[dict[str, float]]:
    model = _model_rows_by_week(timeseries_rows)
    measured = {
        float(row["week"]): float(row["value"])
        for row in measured_rows
        if row["dataset"] == "pH" and row["variable"] == "pH"
    }
    missing = [week for week in OBSERVATION_WEEKS if week not in measured]
    if missing:
        raise ValueError(f"missing measured pH values at weeks: {missing}")
    return [
        {
            "Week": week,
            "Experiment": measured[week],
            "Model": float(model[week]["pH"]),
        }
        for week in OBSERVATION_WEEKS
    ]


def build_icp_comparison(
    timeseries_rows: Iterable[dict[str, str]], measured_rows: Iterable[dict[str, str]]
) -> list[dict[str, float | str]]:
    model = _model_rows_by_week(timeseries_rows)
    measured = {
        (row["variable"], float(row["week"])): float(row["value"])
        for row in measured_rows
        if row["dataset"] == "ICP"
    }

    comparison: list[dict[str, float | str]] = []
    for element in ELEMENT_MOLAR_MASS_G_MOL:
        column = f"element_molality_mol_kgw::{element}"
        for week in OBSERVATION_WEEKS:
            key = (element, week)
            if key not in measured:
                raise ValueError(f"missing measured ICP value for {element} at week {week:g}")
            if column not in model[week] or model[week][column] == "":
                raise ValueError(
                    f"timeseries is missing {column}; rerun the Jayasekara case with "
                    "postprocessing.requested_elements enabled"
                )
            molality = float(model[week][column])
            ppm = molality * ELEMENT_MOLAR_MASS_G_MOL[element] * 1000.0
            comparison.append(
                {
                    "Variable": element,
                    "Week": week,
                    "Experiment": measured[key],
                    "Model": ppm,
                }
            )
    return comparison


def _set_global_style(*, dpi: int) -> None:
    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.serif": ["Times New Roman", "Times", "DejaVu Serif"],
            "font.size": 12,
            "axes.labelsize": 14,
            "axes.titlesize": 15,
            "xtick.labelsize": 12,
            "ytick.labelsize": 12,
            "legend.fontsize": 11,
            "legend.title_fontsize": 12,
            "axes.linewidth": 1.2,
            "savefig.dpi": dpi,
        }
    )


def plot_ph(comparison: list[dict[str, float]], output_dir: Path) -> Path:
    _set_global_style(dpi=600)
    fig, ax = plt.subplots(figsize=(6.4, 4.2))

    weeks = [row["Week"] for row in comparison]
    experimental = [row["Experiment"] for row in comparison]
    model = [row["Model"] for row in comparison]

    ax.scatter(
        weeks,
        experimental,
        marker="o",
        s=48,
        color="black",
        label="Experiment",
        zorder=4,
    )
    ax.plot(
        weeks,
        model,
        linestyle="--",
        linewidth=1.8,
        marker="s",
        markersize=6.2,
        markerfacecolor="white",
        markeredgecolor="black",
        markeredgewidth=1.3,
        color="black",
        label="Reaktoro model",
        zorder=3,
    )
    ax.set_xlim(0, 40)
    ax.set_xticks([0, 5, 10, 15, 20, 25, 30, 35, 40])
    ax.set_xlabel("Reaction time (weeks)")
    ax.set_ylabel("pH")
    ax.set_title("pH: model vs experiment")
    ax.grid(True, which="major", linewidth=0.8, alpha=0.35)
    ax.legend(frameon=True)
    fig.tight_layout()

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "pH_model_vs_experiment.png"
    fig.savefig(path, dpi=600, bbox_inches="tight")
    plt.close(fig)
    return path


def _log_tick_formatter(y: float, _position: int) -> str:
    if y >= 1:
        return f"{y:g}"
    return f"{y:.1f}"


def plot_icp(comparison: list[dict[str, float | str]], output_dir: Path) -> list[Path]:
    _set_global_style(dpi=300)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    for output_name, variables in ICP_GROUPS.items():
        fig, ax = plt.subplots(figsize=(7.4, 5.2))
        element_handles = []

        for var in variables:
            data = sorted(
                (row for row in comparison if row["Variable"] == var),
                key=lambda row: float(row["Week"]),
            )
            data = [
                row
                for row in data
                if np.isfinite(float(row["Experiment"]))
                and np.isfinite(float(row["Model"]))
                and float(row["Experiment"]) > 0.0
                and float(row["Model"]) > 0.0
            ]
            if not data:
                continue

            marker = ICP_STYLES[var]["marker"]
            color = ICP_STYLES[var]["color"]
            ax.scatter(
                [float(row["Week"]) for row in data],
                [float(row["Experiment"]) for row in data],
                marker=marker,
                s=48,
                color=color,
                linewidths=1.4,
                zorder=4,
            )
            ax.plot(
                [float(row["Week"]) for row in data],
                [float(row["Model"]) for row in data],
                linestyle="--",
                linewidth=1.8,
                color=color,
                zorder=3,
            )
            element_handles.append(
                Line2D(
                    [0],
                    [0],
                    color=color,
                    marker=marker,
                    linestyle="None",
                    markersize=6.2,
                    markeredgewidth=1.3,
                    label=var,
                )
            )

        ax.set_yscale("log")
        ax.set_xlim(0, 40)
        ax.set_xticks(np.arange(0, 41, 5))
        if output_name == "ICP_Na_Ca_Mg.png":
            ax.set_ylim(10, 100000)
        elif output_name == "ICP_Si_Al_Fe_K.png":
            ax.set_ylim(0.1, 1000)

        ax.set_xlabel("Reaction time (weeks)")
        ax.set_ylabel("Concentration (ppm)")
        ax.yaxis.set_major_locator(LogLocator(base=10.0, numticks=7))
        ax.yaxis.set_minor_locator(
            LogLocator(base=10.0, subs=np.arange(2, 10) * 0.1, numticks=80)
        )
        ax.yaxis.set_minor_formatter(NullFormatter())
        ax.yaxis.set_major_formatter(FuncFormatter(_log_tick_formatter))
        ax.grid(True, which="major", axis="both", linewidth=0.8, alpha=0.35)
        ax.grid(True, which="minor", axis="y", linewidth=0.35, alpha=0.18)

        element_legend = ax.legend(
            handles=element_handles,
            title="Element",
            loc="upper left",
            bbox_to_anchor=(1.02, 0.98),
            frameon=False,
            borderaxespad=0.0,
        )
        ax.add_artist(element_legend)
        series_handles = [
            Line2D(
                [0],
                [0],
                marker="o",
                linestyle="None",
                color="black",
                markersize=6,
                label="Experiment",
            ),
            Line2D(
                [0],
                [0],
                linestyle="--",
                color="black",
                linewidth=1.8,
                label="Simulation",
            ),
        ]
        ax.legend(
            handles=series_handles,
            title="Series",
            loc="upper left",
            bbox_to_anchor=(1.02, 0.23),
            frameon=True,
            borderaxespad=0.0,
        )

        fig.tight_layout(rect=[0.0, 0.0, 0.78, 1.0])
        path = output_dir / output_name
        fig.savefig(path, dpi=300, bbox_inches="tight")
        plt.close(fig)
        paths.append(path)

    return paths


def generate_figures(timeseries: Path, experimental_data: Path, output_dir: Path) -> list[Path]:
    model_rows = _read_csv(timeseries)
    measured_rows = _read_csv(experimental_data)
    ph = build_ph_comparison(model_rows, measured_rows)
    icp = build_icp_comparison(model_rows, measured_rows)
    return [plot_ph(ph, output_dir), *plot_icp(icp, output_dir)]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate Jayasekara measured-vs-Reaktoro pH and ICP figures."
    )
    parser.add_argument("--timeseries", type=Path, default=DEFAULT_TIMESERIES)
    parser.add_argument("--experimental-data", type=Path, default=DEFAULT_EXPERIMENT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser


def main() -> int:
    args = _parser().parse_args()
    for path in generate_figures(args.timeseries, args.experimental_data, args.output_dir):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

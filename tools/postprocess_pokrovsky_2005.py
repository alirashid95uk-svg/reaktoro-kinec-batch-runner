from __future__ import annotations

"""Postprocess the Pokrovsky et al. (2005) Calcite batch cases.

This script fixes the *comparison*, not the underlying batch physics.

The runner time-zero rate is evaluated from its PHREEQC bulk state. Pokrovsky's
transport-corrected intrinsic benchmark is tied to the published surface pH.
A postprocessor cannot retroactively replace the runner thermodynamics with
MINTEQA2/Davies or reconstruct the rotating-disc boundary layer.

Instead this script reports, side by side:

1. the unmodified runner time-zero Calcite flux;
2. the source-traced Pokrovsky intrinsic-flux benchmark;
3. a Weiss acid-mechanism diagnostic evaluated at Pokrovsky's published
   surface proton activity.

The third quantity is deliberately an acid-term diagnostic only. Full surface
carbonate speciation is not supplied by the Pokrovsky benchmark, so the script
does not invent a full surface-state Palandri-Kharaka rate.
"""

import csv
import json
import math
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ROOT / "data" / "validation" / "pokrovsky_2005_calcite_intrinsic_targets.csv"
KINETICS = ROOT / "data" / "kinetics" / "PalandriKharaka_pokrovsky_2005_weiss_calcite.yaml"
OUTPUT_ROOT = ROOT / "outputs" / "pokrovsky_2005"
RESULT_CSV = OUTPUT_ROOT / "pokrovsky_2005_intrinsic_comparison.csv"
RESULT_JSON = OUTPUT_ROOT / "pokrovsky_2005_intrinsic_comparison_summary.json"

POKROVSKY_DOI = "10.1016/j.chemgeo.2004.12.012"
WEISS_DOI = "10.1016/j.apgeochem.2025.106611"


def load_targets() -> list[dict[str, str]]:
    with TARGETS.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    pressures = [float(row["pCO2_atm"]) for row in rows]
    if pressures != [2.0, 10.0, 50.0]:
        raise RuntimeError(f"unexpected Pokrovsky pressure rows: {pressures}")
    return rows


def load_weiss_acid_mechanism() -> tuple[float, float]:
    """Return the configured Calcite acid lgk and H+ reaction order."""
    with KINETICS.open(encoding="utf-8") as stream:
        data = yaml.safe_load(stream)
    acid = data["ReactionRateModelParams"]["PalandriKharaka"]["Calcite"]["Mechanisms"]["Acid"]
    lgk = float(acid["lgk"])
    order = float(acid["a(H+)"])
    return lgk, order


def read_time_zero_flux(label: str) -> float:
    path = OUTPUT_ROOT / label / "reaction_rates.csv"
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    matches = [
        row
        for row in rows
        if row["mineral"] == "Calcite" and math.isclose(float(row["time_s"]), 0.0, abs_tol=1e-15)
    ]
    if len(matches) != 1:
        raise RuntimeError(f"expected one time-zero Calcite rate in {path}; found {len(matches)}")
    row = matches[0]
    if row["rate_evaluation_status"] != "evaluated":
        raise RuntimeError(f"time-zero Calcite rate was not evaluated in {path}")
    if row["surface_area_unit"] != "m2":
        raise RuntimeError(f"unexpected time-zero surface-area unit in {path}: {row['surface_area_unit']}")
    return float(row["rate_mol_m2_s"])


def read_time_zero_bulk_pH(label: str) -> float:
    path = OUTPUT_ROOT / label / "timeseries.csv"
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    matches = [row for row in rows if math.isclose(float(row["time_s"]), 0.0, abs_tol=1e-15)]
    if len(matches) != 1:
        raise RuntimeError(f"expected one time-zero state in {path}; found {len(matches)}")
    return float(matches[0]["pH"])


def log10_residual(model: float, target: float) -> float:
    if model <= 0.0 or target <= 0.0:
        raise RuntimeError("log residual requires positive model and target fluxes")
    return math.log10(model / target)


def log10_rmse(residuals: list[float]) -> float:
    return math.sqrt(sum(value * value for value in residuals) / len(residuals))


def main() -> None:
    targets = load_targets()
    acid_lgk, acid_order = load_weiss_acid_mechanism()
    acid_k_25c = 10.0**acid_lgk

    rows: list[dict[str, object]] = []
    raw_residuals: list[float] = []
    surface_acid_residuals: list[float] = []
    bulk_pH_deltas: list[float] = []

    for target in targets:
        pco2 = float(target["pCO2_atm"])
        label = f"{int(pco2)}atm"

        experimental_flux = float(target["intrinsic_flux_mol_m2_s"])
        flux_low = float(target["flux_low_from_kC"])
        flux_high = float(target["flux_high_from_kC"])
        published_bulk_pH = float(target["pH_bulk"])
        published_surface_pH = float(target["pH_surface"])
        surface_h_activity = float(target["aH_surface"])

        runner_flux = read_time_zero_flux(label)
        runner_bulk_pH = read_time_zero_bulk_pH(label)

        # Weiss et al. acid mechanism at 25 C using the published surface H+
        # activity from the source-traced Pokrovsky benchmark.
        surface_acid_flux = acid_k_25c * surface_h_activity**acid_order

        raw_residual = log10_residual(runner_flux, experimental_flux)
        surface_residual = log10_residual(surface_acid_flux, experimental_flux)
        pH_delta = runner_bulk_pH - published_bulk_pH

        raw_residuals.append(raw_residual)
        surface_acid_residuals.append(surface_residual)
        bulk_pH_deltas.append(pH_delta)

        rows.append(
            {
                "pCO2_atm": pco2,
                "published_bulk_pH": published_bulk_pH,
                "runner_time0_bulk_pH": runner_bulk_pH,
                "runner_minus_published_bulk_pH": pH_delta,
                "published_surface_pH": published_surface_pH,
                "published_surface_H_activity": surface_h_activity,
                "experimental_intrinsic_flux_mol_m2_s": experimental_flux,
                "experimental_flux_low_from_kC": flux_low,
                "experimental_flux_high_from_kC": flux_high,
                "runner_time0_flux_mol_m2_s": runner_flux,
                "runner_to_experiment_ratio": runner_flux / experimental_flux,
                "runner_log10_residual": raw_residual,
                "weiss_surface_acid_flux_mol_m2_s": surface_acid_flux,
                "weiss_surface_acid_to_experiment_ratio": surface_acid_flux / experimental_flux,
                "weiss_surface_acid_log10_residual": surface_residual,
                "weiss_surface_acid_within_kC_regression_bounds": flux_low <= surface_acid_flux <= flux_high,
                "comparison_scope": "raw runner bulk-state rate versus transport-corrected experiment; Weiss value is surface-H+ acid-term diagnostic only",
            }
        )

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    with RESULT_CSV.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    summary = {
        "pokrovsky_source_doi": POKROVSKY_DOI,
        "weiss_source_doi": WEISS_DOI,
        "weiss_acid_lgk_25c": acid_lgk,
        "weiss_acid_H_order": acid_order,
        "runner_time0_log10_rmse": log10_rmse(raw_residuals),
        "weiss_surface_acid_log10_rmse": log10_rmse(surface_acid_residuals),
        "max_abs_runner_minus_published_bulk_pH": max(abs(value) for value in bulk_pH_deltas),
        "interpretation": (
            "The postprocessor does not alter runner outputs or reconstruct rotating-disc transport. "
            "It separates the raw PHREEQC bulk-state prediction from a source-supported surface-H+ "
            "acid-term diagnostic and the transport-corrected Pokrovsky intrinsic benchmark."
        ),
    }
    RESULT_JSON.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print(f"wrote {RESULT_CSV.relative_to(ROOT)}")
    print(f"wrote {RESULT_JSON.relative_to(ROOT)}")
    print(f"runner_time0_log10_rmse={summary['runner_time0_log10_rmse']:.6g}")
    print(f"weiss_surface_acid_log10_rmse={summary['weiss_surface_acid_log10_rmse']:.6g}")
    print(
        "max_abs_runner_minus_published_bulk_pH="
        f"{summary['max_abs_runner_minus_published_bulk_pH']:.6g}"
    )


if __name__ == "__main__":
    main()

from types import SimpleNamespace

import pytest

from batch_runner.outputs.tables import timeseries_columns
from validation.jayasekara_comparison_figures import (
    ELEMENT_MOLAR_MASS_G_MOL,
    OBSERVATION_WEEKS,
    build_icp_comparison,
    build_ph_comparison,
)


def _timeseries_rows(element_molality: float = 1.0):
    rows = []
    for index, week in enumerate(OBSERVATION_WEEKS):
        row = {
            "time_days": str(week * 7.0),
            "pH": str(3.0 + 0.1 * index),
        }
        for element in ELEMENT_MOLAR_MASS_G_MOL:
            row[f"element_molality_mol_kgw::{element}"] = str(element_molality)
        rows.append(row)
    return rows


def _measured_rows():
    rows = [
        {
            "dataset": "pH",
            "variable": "pH",
            "week": str(week),
            "value": str(4.0 + 0.1 * index),
        }
        for index, week in enumerate(OBSERVATION_WEEKS)
    ]
    for element in ELEMENT_MOLAR_MASS_G_MOL:
        rows.extend(
            {
                "dataset": "ICP",
                "variable": element,
                "week": str(week),
                "value": "1.0",
            }
            for week in OBSERVATION_WEEKS
        )
    return rows


def test_requested_elements_are_written_as_timeseries_columns() -> None:
    timeseries = SimpleNamespace(
        include_species_amounts=False,
        include_species_molalities=False,
        include_mineral_amounts=False,
        include_mineral_deltas=False,
        include_saturation_indices=False,
        include_solver_columns=False,
    )
    case = SimpleNamespace(
        config=SimpleNamespace(
            outputs=SimpleNamespace(timeseries=timeseries),
            postprocessing=SimpleNamespace(
                requested_species=[],
                requested_elements=["Na", "Ca"],
                requested_minerals=[],
            ),
        )
    )

    columns = timeseries_columns(case)

    assert "element_molality_mol_kgw::Na" in columns
    assert "element_molality_mol_kgw::Ca" in columns


def test_icp_conversion_preserves_preliminary_molality_to_ppm_definition() -> None:
    comparison = build_icp_comparison(_timeseries_rows(), _measured_rows())

    first_si = next(row for row in comparison if row["Variable"] == "Si" and row["Week"] == 2.0)
    first_na = next(row for row in comparison if row["Variable"] == "Na" and row["Week"] == 2.0)

    assert first_si["Model"] == pytest.approx(28.0855 * 1000.0)
    assert first_na["Model"] == pytest.approx(22.98976928 * 1000.0)


def test_ph_comparison_uses_existing_output_schedule_weeks() -> None:
    comparison = build_ph_comparison(_timeseries_rows(), _measured_rows())

    assert [row["Week"] for row in comparison] == list(OBSERVATION_WEEKS)
    assert comparison[0]["Experiment"] == pytest.approx(4.0)
    assert comparison[0]["Model"] == pytest.approx(3.0)


def test_icp_comparison_rejects_missing_element_total_column() -> None:
    rows = _timeseries_rows()
    del rows[0]["element_molality_mol_kgw::Na"]

    with pytest.raises(ValueError, match="element_molality_mol_kgw::Na"):
        build_icp_comparison(rows, _measured_rows())

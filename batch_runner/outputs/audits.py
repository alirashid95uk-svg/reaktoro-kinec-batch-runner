"""Derive optional, explicitly scoped scientific audit tables.

These functions consume accepted observation rows and configured stoichiometric
coefficients.  They do not query the live Reaktoro state or influence solver
execution.  Carbon and element totals cover only the species/minerals listed in
configuration; rate-sign checks compare signs only and are not kinetic-model
validation, calibration, transport, or fracture-sealing evidence.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from batch_runner.config import ResolvedCase

if TYPE_CHECKING:
    from batch_runner.simulator import SimulationResult


REACTION_RATE_COLUMNS = [
    "time_s",
    "time_days",
    "mineral",
    "rate_mol_s",
    "rate_mol_m2_s",
    "saturation_index",
    "saturation_ratio",
    "surface_area_value",
    "surface_area_unit",
    "rate_evaluation_status",
]
REACTION_RATE_VALIDATION_COLUMNS = [
    "time_s",
    "time_days",
    "mineral",
    "saturation_index",
    "expected_rate_sign_from_si",
    "observed_rate_sign",
    "sign_check",
    "scope_note",
]
CARBON_INVENTORY_COLUMNS = [
    "time_s",
    "time_days",
    "aqueous_carbon_mol",
    "gas_carbon_mol",
    "mineral_carbon_mol",
    "total_carbon_mol",
    "initial_total_carbon_mol",
    "carbon_balance_error_mol",
    "carbon_balance_error_percent",
]
ELEMENT_BUDGET_COLUMNS = [
    "time_s",
    "time_days",
    "element",
    "aqueous_mol",
    "mineral_mol",
    "gas_mol",
    "total_mol",
    "initial_total_mol",
    "delta_mol",
    "relative_error_percent",
]
SURFACE_AREA_COLUMNS = [
    "mineral",
    "role",
    "surface_area_value",
    "surface_area_unit",
    "surface_area_basis",
    "surface_area_provenance",
    "comparability_status",
]
def reaction_rate_rows(case: ResolvedCase, result: SimulationResult) -> Iterator[dict[str, Any]]:
    """Yield live rate observations for each configured kinetic mineral.

    Total rates are mol/s, surface-normalized rates mol/(m2 s), surface area m2,
    and saturation quantities dimensionless.  Values are copied from accepted
    rows collected by Reaktoro-facing observation code.
    """
    kinetic_minerals = [mineral for mineral in case.config.minerals if mineral.role == "kinetic"]
    for row in result.iter_rows():
        for mineral in kinetic_minerals:
            name = mineral.name
            yield {
                "time_s": row["time_s"],
                "time_days": row["time_days"],
                "mineral": name,
                "rate_mol_s": row[f"reaction_rate_mol_s::{name}"],
                "rate_mol_m2_s": row[f"reaction_rate_mol_m2_s::{name}"],
                "saturation_index": row[f"saturation_index::{name}"],
                "saturation_ratio": row[f"reaction_rate_saturation_ratio::{name}"],
                "surface_area_value": row[f"reaction_rate_surface_area_m2::{name}"],
                "surface_area_unit": "m2",
                "rate_evaluation_status": row[f"reaction_rate_status::{name}"],
            }


def reaction_rate_validation_rows(
    case: ResolvedCase, result: SimulationResult
) -> Iterator[dict[str, Any]]:
    """Yield a sign-only diagnostic comparing rate direction with saturation.

    The expected sign follows the runtime rate convention encoded here:
    undersaturation maps to positive and supersaturation to negative.  A passed
    sign check does not establish rate magnitude, calibration, or validity.
    """
    for row in reaction_rate_rows(case, result):
        expected = _expected_rate_sign(row["saturation_index"])
        observed = _sign(row["rate_mol_s"])
        yield {
            "time_s": row["time_s"],
            "time_days": row["time_days"],
            "mineral": row["mineral"],
            "saturation_index": row["saturation_index"],
            "expected_rate_sign_from_si": expected,
            "observed_rate_sign": observed,
            "sign_check": "passed" if expected == observed else "failed",
            "scope_note": "batch rate-sign diagnostic; not a transport or fracture-sealing result",
        }


def carbon_inventory_rows(case: ResolvedCase, result: SimulationResult) -> Iterator[dict[str, Any]]:
    """Yield configured aqueous, gas, mineral, and total carbon inventories.

    Inventories are mol of carbon using user-supplied coefficients.  Balance
    error is relative to the first emitted row and is incomplete if configured
    mappings omit a carbon-bearing phase or species.
    """
    config = case.config.postprocessing.carbon_inventory
    initial_total = None
    for row in result.iter_rows():
        aqueous = _weighted_sum(row, "species_amount_mol", config.carbon_species)
        gas = _weighted_sum(row, "species_amount_mol", config.carbon_gas_species)
        mineral = _weighted_sum(row, "mineral_amount_mol", config.carbon_minerals)
        total = aqueous + gas + mineral
        if initial_total is None:
            initial_total = total
        yield {
            "time_s": row["time_s"],
            "time_days": row["time_days"],
            "aqueous_carbon_mol": aqueous,
            "gas_carbon_mol": gas,
            "mineral_carbon_mol": mineral,
            "total_carbon_mol": total,
            "initial_total_carbon_mol": initial_total,
            "carbon_balance_error_mol": total - initial_total,
            "carbon_balance_error_percent": _percent_error(total - initial_total, initial_total),
        }


def element_budget_rows(case: ResolvedCase, result: SimulationResult) -> Iterator[dict[str, Any]]:
    """Yield configured per-element inventories relative to the first row.

    Totals are mol of element computed from user-supplied stoichiometric
    mappings.  This table audits that declared subset; it is not an automatic
    whole-system conservation calculation.
    """
    config = case.config.postprocessing.element_budget
    initial_totals: dict[str, float] = {}
    for row in result.iter_rows():
        for element in config.elements:
            aqueous = _element_sum(row, "species_amount_mol", config.species, element)
            mineral = _element_sum(row, "mineral_amount_mol", config.minerals, element)
            gas = _element_sum(row, "species_amount_mol", config.gas_species, element)
            total = aqueous + mineral + gas
            initial = initial_totals.setdefault(element, total)
            yield {
                "time_s": row["time_s"],
                "time_days": row["time_days"],
                "element": element,
                "aqueous_mol": aqueous,
                "mineral_mol": mineral,
                "gas_mol": gas,
                "total_mol": total,
                "initial_total_mol": initial,
                "delta_mol": total - initial,
                "relative_error_percent": _percent_error(total - initial, initial),
            }


def surface_area_audit_rows(case: ResolvedCase) -> list[dict[str, Any]]:
    """Describe configured mineral surface-area basis and provenance.

    ``comparable_within_case`` means every kinetic mineral has the same declared
    unit and non-empty basis.  It does not verify conversion, measurement
    quality, or comparability with another case.
    """
    kinetic = [mineral for mineral in case.config.minerals if mineral.role == "kinetic"]
    comparable = len({(m.surface_area.unit, m.surface_area_basis) for m in kinetic}) == 1 and all(
        m.surface_area_basis for m in kinetic
    )
    status = "comparable_within_case" if comparable else "mixed_or_not_configured"
    return [
        {
            "mineral": mineral.name,
            "role": mineral.role,
            "surface_area_value": mineral.surface_area.value if mineral.surface_area else None,
            "surface_area_unit": mineral.surface_area.unit if mineral.surface_area else None,
            "surface_area_basis": mineral.surface_area_basis,
            "surface_area_provenance": mineral.surface_area_provenance,
            "comparability_status": status if mineral.role == "kinetic" else "not_applicable",
        }
        for mineral in case.config.minerals
    ]


def _weighted_sum(row: dict[str, Any], prefix: str, mapping: dict[str, float]) -> float:
    return sum(float(row[f"{prefix}::{name}"]) * coefficient for name, coefficient in mapping.items())


def _element_sum(
    row: dict[str, Any],
    prefix: str,
    mapping: dict[str, dict[str, float]],
    element: str,
) -> float:
    return sum(float(row[f"{prefix}::{name}"]) * stoich.get(element, 0.0) for name, stoich in mapping.items())


def _percent_error(delta: float, initial: float) -> float | None:
    return None if initial == 0 else 100.0 * delta / initial


def _sign(value: float) -> str:
    if value > 0:
        return "positive"
    if value < 0:
        return "negative"
    return "zero"


def _expected_rate_sign(saturation_index: float) -> str:
    if saturation_index < 0:
        return "positive"
    if saturation_index > 0:
        return "negative"
    return "zero"

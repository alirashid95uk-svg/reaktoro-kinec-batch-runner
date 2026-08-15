"""Minimal Kinec -> Reaktoro mineral-rate models.

This file follows Reaktoro's mineral-kinetics structure:

    params = KinecParams.local("kinec_rates_minimal.yaml")
    MineralReaction("Calcite").setRateModel(ReactionRateModelKinec(params, "Calcite"))
    MineralSurface("Calcite", 6.0, "cm2/cm3")

The YAML stores only kinetic parameters. Reaktoro supplies live temperature,
activities, saturation ratio, and mineral surface area through ChemicalProps.

Sign convention:
    Kinec equation is positive for dissolution.
    Reaktoro 2.13's Python setRateModel binding uses the generated Calcite
    dissolution reaction, for which a positive rate dissolves Calcite.
"""

from collections.abc import Mapping
from math import exp
from pathlib import Path
from typing import Any

import yaml

R = 8.314462618  # J mol-1 K-1


class KinecParams:
    """Tiny Reaktoro-style loader for cleaned Kinec YAML parameters."""

    def __init__(self, data: Mapping[str, Any]):
        self.data = dict(data)

    @staticmethod
    def local(path: str | Path) -> "KinecParams":
        """Load local cleaned Kinec YAML, similar in spirit to Params.local(...)."""
        with Path(path).open("r", encoding="utf-8") as f:
            return KinecParams(yaml.safe_load(f))

    @staticmethod
    def fromFile(path: str | Path) -> "KinecParams":
        """Alias kept to mirror Reaktoro Params.fromFile(...)."""
        return KinecParams.local(path)

    def __getitem__(self, mineral: str) -> Mapping[str, Any]:
        return self.data[mineral]


def ReactionRateModelKinec(
    params: KinecParams | Mapping[str, Any],
    mineral: str,
    *,
    hco3_species: str = "HCO3-",
    co3_species: str = "CO3-2",
):
    """Return a Reaktoro MineralReactionRateModel for one Kinec mineral.

    Use with:
        MineralReaction(mineral).setRateModel(ReactionRateModelKinec(params, mineral))
    Surface area must be supplied separately with MineralSurface(...); inside
    the rate model it is read from the live Reaktoro chemical properties.
    """
    from reaktoro import ChemicalProps, ReactionRate, ReactionRateModel

    record = params[mineral] if isinstance(params, KinecParams) else params

    def rate(props: ChemicalProps) -> ReactionRate:
        diagnostic = evaluate_kinec_rate(
            record,
            mineral,
            props,
            hco3_species=hco3_species,
            co3_species=co3_species,
        )
        return ReactionRate(diagnostic["rate_mol_s"])

    return ReactionRateModel(rate)


def evaluate_kinec_rate(
    record: Mapping[str, Any],
    reaktoro_mineral: str,
    props: Any,
    *,
    hco3_species: str = "HCO3-",
    co3_species: str = "CO3-2",
) -> dict[str, float]:
    """Evaluate the custom Kinec equation used by the Reaktoro callback."""
    from reaktoro import AqueousProps

    family = record["family"]
    sigma = float(record["sigma"])
    terms = record.get("terms", {})
    T = float(props.temperature())
    surface_area = float(props.surfaceArea(reaktoro_mineral))
    omega = float(AqueousProps(props).saturationRatio(reaktoro_mineral))

    aH = float(props.speciesActivity("H+"))
    flux = 0.0
    if "acid" in terms:
        flux += _term_flux(terms["acid"], T, aH)
    if "neutral" in terms:
        flux += _term_flux(terms["neutral"], T)
    if "basic" in terms:
        flux += _term_flux(terms["basic"], T, aH)
    if family == "carbonate" and "carbonate" in terms:
        a_hco3 = float(props.speciesActivity(hco3_species))
        a_co3 = float(props.speciesActivity(co3_species))
        flux += _carbonate_flux(terms["carbonate"], T, a_hco3, a_co3)

    affinity_factor = 1.0 - omega ** (1.0 / sigma)
    return {
        "rate_mol_s": surface_area * flux * affinity_factor,
        "surface_normalized_rate": flux * affinity_factor,
        "surface_area": surface_area,
        "saturation_ratio": omega,
    }


def _term_flux(term: Mapping[str, Any], T: float, activity: float | None = None) -> float:
    A = float(term["A"])
    E = float(term["E"])
    J = A * exp(-E / (R * T))

    if "n" in term:
        if activity is None:
            raise ValueError("activity is required for a term with reaction order n")
        n = float(term["n"])
        if activity <= 0.0 and n != 0.0:
            raise ValueError("activity must be positive when raised to a nonzero power")
        J *= activity ** n

    return J


def _carbonate_flux(term: Mapping[str, Any], T: float, a_hco3: float, a_co3: float) -> float:
    A = float(term["A"])
    E = float(term["E"])
    Kc = float(term.get("Kc", 0.0))
    return A * exp(-E / (R * T)) / (1.0 + Kc * (a_hco3 + a_co3))

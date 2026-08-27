from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml

from batch_runner.config import CaseConfig
from batch_runner.doe.models import Target
from batch_runner.doe.targets import materialise_candidate, resolve_target


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_CASE = PROJECT_ROOT / "tests" / "fixtures" / "cases" / "synthetic_kinec_case.yaml"
ADAPTIVE_CASE = PROJECT_ROOT / "cases" / "jayasekara_2020_reproduction.yaml"
ERROR_CASE = PROJECT_ROOT / "cases" / "jayasekara_2020_reproduction_error_controlled.yaml"
POKROVSKY_CASE = PROJECT_ROOT / "cases" / "pokrovsky_2005" / "pokrovsky_2005_2atm.yaml"
PALANDRI_PATH = PROJECT_ROOT / "data" / "kinetics" / "PalandriKharaka_pokrovsky_2005_weiss_calcite.yaml"
KINEC_PATH = PROJECT_ROOT / "data" / "kinetics" / "kinec_rates_minimal.yaml"


def _load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _diff_paths(
    left: Any, right: Any, prefix: tuple[str, ...] = ()
) -> set[tuple[str, ...]]:
    if type(left) is not type(right):
        return {prefix}
    if isinstance(left, dict):
        if set(left) != set(right):
            return {prefix}
        result: set[tuple[str, ...]] = set()
        for key in left:
            result |= _diff_paths(left[key], right[key], prefix + (str(key),))
        return result
    if isinstance(left, list):
        if len(left) != len(right):
            return {prefix}
        result: set[tuple[str, ...]] = set()
        for index, (a, b) in enumerate(zip(left, right)):
            result |= _diff_paths(a, b, prefix + (str(index),))
        return result
    return set() if left == right else {prefix}


def _error_case_with_events() -> dict[str, Any]:
    raw = _load_yaml(ERROR_CASE)
    raw["solver"]["timestep"]["events"] = {
        "hard_mineral_exhaustion": {
            "amount_tolerance": {"value": 1.0e-10, "unit": "mol"},
            "time_tolerance": {"value": 60.0, "unit": "seconds"},
            "restart_dt": {"value": 300.0, "unit": "seconds"},
            "max_localizations": 10,
        },
        "soft": {
            "timestep_cap_factor": 0.5,
            "saturation_index_crossing": False,
            "max_pH_change": 0.2,
            "secondary_mineral_appearance": {"value": 1.0e-8, "unit": "mol"},
            "max_reaction_rate_relative_change": 0.5,
            "reaction_rate_floor": {"value": 1.0e-12, "unit": "mol/s"},
        },
    }
    return raw


def _case_for_target(kind: str) -> dict[str, Any]:
    adaptive = {
        "co2_fugacity",
        "adaptive_dt_initial",
        "adaptive_dt_min",
        "adaptive_dt_max",
        "adaptive_growth_factor",
        "adaptive_shrink_factor",
        "adaptive_max_retries",
    }
    error_controlled = {
        "error_dt_initial",
        "error_dt_min",
        "error_dt_max",
        "error_safety_factor",
        "error_growth_factor",
        "error_shrink_factor",
        "solver_failure_shrink_factor",
        "error_max_retries",
        "richardson_temporal_order",
        "richardson_relative_tolerance",
        "controlled_mineral_absolute_tolerance",
        "controlled_mineral_reference_floor",
        "hard_exhaustion_amount_tolerance",
        "hard_exhaustion_time_tolerance",
        "hard_exhaustion_restart_dt",
        "hard_exhaustion_max_localizations",
        "soft_timestep_cap_factor",
        "soft_max_pH_change",
        "soft_secondary_mineral_appearance",
        "soft_max_reaction_rate_relative_change",
        "soft_reaction_rate_floor",
    }
    if kind in adaptive:
        raw = _load_yaml(ADAPTIVE_CASE)
    elif kind in error_controlled:
        raw = _error_case_with_events()
    else:
        raw = _load_yaml(SYNTHETIC_CASE)

    if kind == "redox_pe":
        raw["redox"] = {
            "enabled": True,
            "pe": 4.0,
            "apply_during": "initial_equilibrium_only",
        }
    elif kind == "co2_initial_amount":
        raw["activity_models"]["gas"] = "peng_robinson_phreeqc"
        raw["co2"] = {
            "mode": "finite",
            "gas_species": "CO2(g)",
            "initial_amount": {"value": 1.0, "unit": "mol"},
        }
    elif kind == "brine_element_amount":
        raw["brine"].pop("species_amounts", None)
        raw["brine"]["element_amounts"] = {"H": {"value": 1.0, "unit": "mol"}}
    elif kind == "solver_max_internal_steps":
        raw["solver"]["timestep"]["max_internal_steps"] = 100_000

    CaseConfig.model_validate(raw)
    return raw


CASE_TARGETS = [
    ("temperature", {}, 31.0, ("physical", "temperature_c")),
    ("pressure", {}, 2.0, ("physical", "pressure_bar")),
    ("redox_pe", {}, 5.0, ("redox", "pe")),
    ("co2_fugacity", {}, 60.0, ("co2", "fugacity_bar")),
    ("co2_initial_amount", {}, 2.0, ("co2", "initial_amount", "value")),
    ("brine_species_amount", {"species": "H2O"}, 2.0, ("brine", "species_amounts", "H2O", "value")),
    ("brine_element_amount", {"element": "H"}, 2.0, ("brine", "element_amounts", "H", "value")),
    ("mineral_initial_amount", {"mineral": "Calcite"}, 2.0, ("minerals", "0", "initial_amount", "value")),
    ("mineral_surface_area", {"mineral": "Calcite"}, 2.0, ("minerals", "0", "surface_area", "value")),
    ("solver_duration", {}, 3.0, ("solver", "timestep", "time", "duration_value")),
    ("solver_max_internal_steps", {}, 200_000, ("solver", "timestep", "max_internal_steps")),
    ("fixed_dt", {}, 0.5, ("solver", "timestep", "step_size", "dt", "value")),
    ("adaptive_dt_initial", {}, 1800.0, ("solver", "timestep", "step_size", "dt_initial", "value")),
    ("adaptive_dt_min", {}, 30.0, ("solver", "timestep", "step_size", "dt_min", "value")),
    ("adaptive_dt_max", {}, 3600.0, ("solver", "timestep", "step_size", "dt_max", "value")),
    ("adaptive_growth_factor", {}, 1.6, ("solver", "timestep", "step_size", "growth_factor")),
    ("adaptive_shrink_factor", {}, 0.4, ("solver", "timestep", "step_size", "shrink_factor")),
    ("adaptive_max_retries", {}, 9, ("solver", "timestep", "step_size", "max_retries_per_step")),
    ("error_dt_initial", {}, 1800.0, ("solver", "timestep", "step_size", "dt_initial", "value")),
    ("error_dt_min", {}, 30.0, ("solver", "timestep", "step_size", "dt_min", "value")),
    ("error_dt_max", {}, 43200.0, ("solver", "timestep", "step_size", "dt_max", "value")),
    ("error_safety_factor", {}, 0.7, ("solver", "timestep", "step_size", "safety_factor")),
    ("error_growth_factor", {}, 1.6, ("solver", "timestep", "step_size", "growth_factor")),
    ("error_shrink_factor", {}, 0.2, ("solver", "timestep", "step_size", "shrink_factor")),
    ("solver_failure_shrink_factor", {}, 0.4, ("solver", "timestep", "step_size", "solver_failure_shrink_factor")),
    ("error_max_retries", {}, 9, ("solver", "timestep", "step_size", "max_retries_per_step")),
    ("richardson_temporal_order", {}, 1.2, ("solver", "timestep", "error_control", "temporal_order")),
    ("richardson_relative_tolerance", {}, 2.0e-4, ("solver", "timestep", "error_control", "relative_tolerance")),
    ("controlled_mineral_absolute_tolerance", {"mineral": "Quartz"}, 2.0e-10, ("solver", "timestep", "error_control", "controlled_minerals", "0", "absolute_tolerance", "value")),
    ("controlled_mineral_reference_floor", {"mineral": "Quartz"}, 2.0e-10, ("solver", "timestep", "error_control", "controlled_minerals", "0", "reference_floor", "value")),
    ("hard_exhaustion_amount_tolerance", {}, 2.0e-10, ("solver", "timestep", "events", "hard_mineral_exhaustion", "amount_tolerance", "value")),
    ("hard_exhaustion_time_tolerance", {}, 30.0, ("solver", "timestep", "events", "hard_mineral_exhaustion", "time_tolerance", "value")),
    ("hard_exhaustion_restart_dt", {}, 120.0, ("solver", "timestep", "events", "hard_mineral_exhaustion", "restart_dt", "value")),
    ("hard_exhaustion_max_localizations", {}, 12, ("solver", "timestep", "events", "hard_mineral_exhaustion", "max_localizations")),
    ("soft_timestep_cap_factor", {}, 0.4, ("solver", "timestep", "events", "soft", "timestep_cap_factor")),
    ("soft_max_pH_change", {}, 0.1, ("solver", "timestep", "events", "soft", "max_pH_change")),
    ("soft_secondary_mineral_appearance", {}, 2.0e-8, ("solver", "timestep", "events", "soft", "secondary_mineral_appearance", "value")),
    ("soft_max_reaction_rate_relative_change", {}, 0.4, ("solver", "timestep", "events", "soft", "max_reaction_rate_relative_change")),
    ("soft_reaction_rate_floor", {}, 2.0e-12, ("solver", "timestep", "events", "soft", "reaction_rate_floor", "value")),
]


@pytest.mark.parametrize(
    ("kind", "selectors", "canonical_value", "expected_path"),
    CASE_TARGETS,
    ids=[item[0] for item in CASE_TARGETS],
)
def test_case_target_materialises_only_declared_path(
    kind: str,
    selectors: dict[str, str],
    canonical_value: float | int,
    expected_path: tuple[str, ...],
) -> None:
    raw = _case_for_target(kind)
    target = Target(kind=kind, **selectors)
    resolved = resolve_target(target, raw, None)

    materialised, kinetics = materialise_candidate(
        raw, [(resolved, canonical_value)], None
    )

    assert kinetics is None
    assert _diff_paths(raw, materialised) == {expected_path}
    CaseConfig.model_validate(materialised)


PALANDRI_TARGETS = [
    ("pk_lgk", {}, -0.4, "lgk"),
    ("pk_activation_energy", {}, -0.1, "E"),
    ("pk_p", {}, 1.1, "p"),
    ("pk_q", {}, 1.2, "q"),
    ("pk_catalyst_power", {"catalyst_property": "a(H+)"}, 0.9, "a(H+)"),
]


@pytest.mark.parametrize(
    ("kind", "extra", "canonical_value", "field"),
    PALANDRI_TARGETS,
    ids=[item[0] for item in PALANDRI_TARGETS],
)
def test_palandri_target_materialises_only_declared_field(
    kind: str,
    extra: dict[str, str],
    canonical_value: float,
    field: str,
) -> None:
    raw = _load_yaml(POKROVSKY_CASE)
    kinetics = _load_yaml(PALANDRI_PATH)
    acid = kinetics["ReactionRateModelParams"]["PalandriKharaka"]["Calcite"]["Mechanisms"]["Acid"]
    acid.setdefault("p", 1.0)
    acid.setdefault("q", 1.0)
    target = Target(
        kind=kind,
        record="Calcite",
        mechanism="Acid",
        **extra,
    )
    resolved = resolve_target(target, raw, kinetics)

    materialised, generated = materialise_candidate(
        raw, [(resolved, canonical_value)], kinetics
    )

    assert materialised == raw
    assert generated is not None
    expected = (
        "ReactionRateModelParams",
        "PalandriKharaka",
        "Calcite",
        "Mechanisms",
        "Acid",
        field,
    )
    assert _diff_paths(kinetics, generated) == {expected}
    CaseConfig.model_validate(materialised)


KINEC_TARGETS = [
    ("kinec_sigma", {}, 2.0, ("Calcite", "sigma")),
    ("kinec_A", {"term": "acid"}, 0.5, ("Calcite", "terms", "acid", "A")),
    ("kinec_E", {"term": "acid"}, -100.0, ("Calcite", "terms", "acid", "E")),
    ("kinec_n", {"term": "acid"}, 0.2, ("Calcite", "terms", "acid", "n")),
    ("kinec_Kc", {"term": "acid"}, 0.5, ("Calcite", "terms", "acid", "Kc")),
]


@pytest.mark.parametrize(
    ("kind", "extra", "canonical_value", "expected_path"),
    KINEC_TARGETS,
    ids=[item[0] for item in KINEC_TARGETS],
)
def test_kinec_target_materialises_only_declared_field(
    kind: str,
    extra: dict[str, str],
    canonical_value: float,
    expected_path: tuple[str, ...],
) -> None:
    raw = _load_yaml(SYNTHETIC_CASE)
    kinetics = _load_yaml(KINEC_PATH)
    calcite = kinetics["Calcite"]
    calcite.setdefault("sigma", 1.0)
    acid = calcite.setdefault("terms", {}).setdefault("acid", {})
    acid.setdefault("A", 1.0)
    acid.setdefault("E", 1.0)
    acid.setdefault("n", 1.0)
    acid.setdefault("Kc", 1.0)
    target = Target(kind=kind, mineral="Calcite", **extra)
    resolved = resolve_target(target, raw, kinetics)

    materialised, generated = materialise_candidate(
        raw, [(resolved, canonical_value)], kinetics
    )

    assert materialised == raw
    assert generated is not None
    assert _diff_paths(kinetics, generated) == {expected_path}
    CaseConfig.model_validate(materialised)

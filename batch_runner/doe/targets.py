"""Semantic DoE target registry, units, and materialisation helpers."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from math import isfinite
from typing import Any, Literal

from .models import ParameterSpec, Target

TARGET_REGISTRY_VERSION = "1.0"
EXPLICIT = "explicit_values"
DISCRETE = "discrete_uniform"
UNIFORM = "uniform"
LOG = "log_uniform"
IMPORTED = "imported_column"
CONTINUOUS = frozenset({EXPLICIT, UNIFORM, IMPORTED})
POSITIVE_CONTINUOUS = frozenset({EXPLICIT, UNIFORM, LOG, IMPORTED})
INTEGER = frozenset({EXPLICIT, DISCRETE, IMPORTED})
KINETIC_KINDS = {
    "pk_lgk", "pk_activation_energy", "pk_p", "pk_q", "pk_catalyst_power",
    "kinec_sigma", "kinec_A", "kinec_E", "kinec_n", "kinec_Kc",
}


@dataclass(frozen=True)
class ResolvedTarget:
    target: Target
    classification: Literal["scientific_input", "numerical_control"]
    data_type: Literal["float", "int"]
    canonical_unit: str
    source_unit: str | None
    allowed_sampling_kinds: frozenset[str]
    lower_bound: float | None = None
    lower_inclusive: bool = True
    upper_bound: float | None = None
    upper_inclusive: bool = True

    def validate_value(self, value: float | int) -> float | int:
        if self.data_type == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{self.target.kind} requires an integer value")
            numeric: float | int = value
        else:
            numeric = float(value)
            if not isfinite(numeric):
                raise ValueError(f"{self.target.kind} requires a finite value")
        if self.lower_bound is not None:
            ok = numeric >= self.lower_bound if self.lower_inclusive else numeric > self.lower_bound
            if not ok:
                op = ">=" if self.lower_inclusive else ">"
                raise ValueError(f"{self.target.kind} requires value {op} {self.lower_bound}")
        if self.upper_bound is not None:
            ok = numeric <= self.upper_bound if self.upper_inclusive else numeric < self.upper_bound
            if not ok:
                op = "<=" if self.upper_inclusive else "<"
                raise ValueError(f"{self.target.kind} requires value {op} {self.upper_bound}")
        return numeric


def _get(raw: dict[str, Any], *path: str) -> Any:
    value: Any = raw
    for key in path:
        if not isinstance(value, dict) or key not in value:
            raise ValueError(f"required base field does not exist: {'.'.join(path)}")
        value = value[key]
    return value


def _find_named(items: list[dict[str, Any]], name: str, label: str) -> dict[str, Any]:
    matches = [item for item in items if item.get("name") == name]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one {label} named {name!r}")
    return matches[0]


def _amount_unit(value: Any, label: str) -> str:
    if not isinstance(value, dict) or "value" not in value or not value.get("unit"):
        raise ValueError(f"{label} must already exist with value and unit")
    return str(value["unit"])


def _time_unit(raw: dict[str, Any], field: str) -> str:
    timestep = _get(raw, "solver", "timestep")
    if field == "duration":
        return str(_get(timestep, "time", "duration_unit"))
    return str(_get(timestep, "step_size", field, "unit"))


def _rule(
    target: Target,
    classification: str,
    data_type: str,
    canonical_unit: str,
    source_unit: str | None,
    allowed: frozenset[str],
    *,
    lower: float | None = None,
    lower_inclusive: bool = True,
    upper: float | None = None,
    upper_inclusive: bool = True,
) -> ResolvedTarget:
    return ResolvedTarget(
        target=target,
        classification=classification,  # type: ignore[arg-type]
        data_type=data_type,  # type: ignore[arg-type]
        canonical_unit=canonical_unit,
        source_unit=source_unit,
        allowed_sampling_kinds=allowed,
        lower_bound=lower,
        lower_inclusive=lower_inclusive,
        upper_bound=upper,
        upper_inclusive=upper_inclusive,
    )


def resolve_target(
    target: Target, base_raw: dict[str, Any], kinetics_raw: dict[str, Any] | None
) -> ResolvedTarget:
    kind = target.kind
    scientific = "scientific_input"
    numerical = "numerical_control"

    if kind == "temperature":
        _get(base_raw, "physical", "temperature_c")
        return _rule(target, scientific, "float", "degC", "degC", CONTINUOUS)
    if kind == "pressure":
        _get(base_raw, "physical", "pressure_bar")
        return _rule(target, scientific, "float", "bar", "bar", POSITIVE_CONTINUOUS, lower=0, lower_inclusive=False)
    if kind == "redox_pe":
        redox = _get(base_raw, "redox")
        if not redox.get("enabled") or redox.get("pe") is None:
            raise ValueError("redox_pe requires enabled redox with existing pe")
        return _rule(target, scientific, "float", "1", None, CONTINUOUS)
    if kind == "co2_fugacity":
        co2 = _get(base_raw, "co2")
        if co2.get("mode") != "fixed_fugacity" or co2.get("fugacity_bar") is None:
            raise ValueError("co2_fugacity requires fixed_fugacity mode")
        return _rule(target, scientific, "float", "bar", "bar", POSITIVE_CONTINUOUS, lower=0, lower_inclusive=False)
    if kind == "co2_initial_amount":
        co2 = _get(base_raw, "co2")
        if co2.get("mode") != "finite":
            raise ValueError("co2_initial_amount requires finite CO2 mode")
        unit = _amount_unit(co2.get("initial_amount"), "co2.initial_amount")
        return _rule(target, scientific, "float", unit, unit, POSITIVE_CONTINUOUS, lower=0)
    if kind == "brine_species_amount":
        values = _get(base_raw, "brine", "species_amounts")
        if not isinstance(values, dict) or target.species not in values:
            raise ValueError(f"brine species {target.species!r} does not exist")
        unit = _amount_unit(values[target.species], f"brine species {target.species}")
        return _rule(target, scientific, "float", unit, unit, POSITIVE_CONTINUOUS, lower=0)
    if kind == "brine_element_amount":
        values = _get(base_raw, "brine", "element_amounts")
        if not isinstance(values, dict) or target.element not in values:
            raise ValueError(f"brine element {target.element!r} does not exist")
        unit = _amount_unit(values[target.element], f"brine element {target.element}")
        return _rule(target, scientific, "float", unit, unit, POSITIVE_CONTINUOUS, lower=0)
    if kind in {"mineral_initial_amount", "mineral_surface_area"}:
        mineral = _find_named(_get(base_raw, "minerals"), str(target.mineral), "mineral")
        field = "initial_amount" if kind == "mineral_initial_amount" else "surface_area"
        unit = _amount_unit(mineral.get(field), f"mineral {target.mineral} {field}")
        return _rule(
            target, scientific, "float", unit, unit, POSITIVE_CONTINUOUS,
            lower=0, lower_inclusive=kind == "mineral_initial_amount",
        )

    timestep = _get(base_raw, "solver", "timestep")
    mode = timestep.get("mode")
    if kind == "solver_duration":
        return _rule(target, numerical, "float", "s", _time_unit(base_raw, "duration"), POSITIVE_CONTINUOUS, lower=0, lower_inclusive=False)
    if kind == "solver_max_internal_steps":
        return _rule(target, numerical, "int", "1", None, INTEGER, lower=1)
    if kind == "fixed_dt":
        if mode != "fixed": raise ValueError("fixed_dt requires fixed timestep mode")
        return _rule(target, numerical, "float", "s", _time_unit(base_raw, "dt"), POSITIVE_CONTINUOUS, lower=0, lower_inclusive=False)
    if kind in {"adaptive_dt_initial", "adaptive_dt_min", "adaptive_dt_max"}:
        if mode != "adaptive": raise ValueError(f"{kind} requires adaptive timestep mode")
        field = {"adaptive_dt_initial":"dt_initial", "adaptive_dt_min":"dt_min", "adaptive_dt_max":"dt_max"}[kind]
        return _rule(target, numerical, "float", "s", _time_unit(base_raw, field), POSITIVE_CONTINUOUS, lower=0, lower_inclusive=False)
    if kind == "adaptive_growth_factor":
        if mode != "adaptive": raise ValueError("adaptive_growth_factor requires adaptive mode")
        return _rule(target, numerical, "float", "1", None, POSITIVE_CONTINUOUS, lower=1, lower_inclusive=False)
    if kind == "adaptive_shrink_factor":
        if mode != "adaptive": raise ValueError("adaptive_shrink_factor requires adaptive mode")
        return _rule(target, numerical, "float", "1", None, CONTINUOUS, lower=0, lower_inclusive=False, upper=1, upper_inclusive=False)
    if kind == "adaptive_max_retries":
        if mode != "adaptive": raise ValueError("adaptive_max_retries requires adaptive mode")
        return _rule(target, numerical, "int", "1", None, INTEGER, lower=0)
    if kind in {"error_dt_initial", "error_dt_min", "error_dt_max"}:
        if mode != "adaptive_error_controlled": raise ValueError(f"{kind} requires adaptive_error_controlled mode")
        field = {"error_dt_initial":"dt_initial", "error_dt_min":"dt_min", "error_dt_max":"dt_max"}[kind]
        return _rule(target, numerical, "float", "s", _time_unit(base_raw, field), POSITIVE_CONTINUOUS, lower=0, lower_inclusive=False)
    if kind == "error_safety_factor":
        if mode != "adaptive_error_controlled": raise ValueError("error_safety_factor requires adaptive_error_controlled mode")
        return _rule(target, numerical, "float", "1", None, CONTINUOUS, lower=0, lower_inclusive=False, upper=1, upper_inclusive=False)
    if kind == "error_growth_factor":
        if mode != "adaptive_error_controlled": raise ValueError("error_growth_factor requires adaptive_error_controlled mode")
        return _rule(target, numerical, "float", "1", None, POSITIVE_CONTINUOUS, lower=1, lower_inclusive=False)
    if kind in {"error_shrink_factor", "solver_failure_shrink_factor"}:
        if mode != "adaptive_error_controlled": raise ValueError(f"{kind} requires adaptive_error_controlled mode")
        return _rule(target, numerical, "float", "1", None, CONTINUOUS, lower=0, lower_inclusive=False, upper=1, upper_inclusive=False)
    if kind == "error_max_retries":
        if mode != "adaptive_error_controlled": raise ValueError("error_max_retries requires adaptive_error_controlled mode")
        return _rule(target, numerical, "int", "1", None, INTEGER, lower=0)
    if kind == "richardson_temporal_order":
        if mode != "adaptive_error_controlled": raise ValueError("Richardson controls require adaptive_error_controlled mode")
        return _rule(target, numerical, "float", "1", None, POSITIVE_CONTINUOUS, lower=0, lower_inclusive=False)
    if kind == "richardson_relative_tolerance":
        if mode != "adaptive_error_controlled": raise ValueError("Richardson controls require adaptive_error_controlled mode")
        return _rule(target, numerical, "float", "1", None, POSITIVE_CONTINUOUS, lower=0)
    if kind in {"controlled_mineral_absolute_tolerance", "controlled_mineral_reference_floor"}:
        if mode != "adaptive_error_controlled": raise ValueError("controlled mineral tolerances require adaptive_error_controlled mode")
        _find_named(_get(timestep, "error_control", "controlled_minerals"), str(target.mineral), "controlled mineral")
        return _rule(target, numerical, "float", "mol", "mol", POSITIVE_CONTINUOUS, lower=0)
    if kind.startswith("hard_exhaustion_"):
        if mode != "adaptive_error_controlled": raise ValueError("hard exhaustion controls require adaptive_error_controlled mode")
        hard = _get(timestep, "events", "hard_mineral_exhaustion")
        if not isinstance(hard, dict): raise ValueError("hard_mineral_exhaustion must already be configured")
        if kind == "hard_exhaustion_amount_tolerance":
            return _rule(target, numerical, "float", "mol", "mol", POSITIVE_CONTINUOUS, lower=0, lower_inclusive=False)
        if kind == "hard_exhaustion_time_tolerance":
            unit = str(_get(hard, "time_tolerance", "unit"))
            return _rule(target, numerical, "float", "s", unit, POSITIVE_CONTINUOUS, lower=0, lower_inclusive=False)
        if kind == "hard_exhaustion_restart_dt":
            unit = str(_get(hard, "restart_dt", "unit"))
            return _rule(target, numerical, "float", "s", unit, POSITIVE_CONTINUOUS, lower=0, lower_inclusive=False)
        return _rule(target, numerical, "int", "1", None, INTEGER, lower=1)
    if kind.startswith("soft_"):
        if mode != "adaptive_error_controlled": raise ValueError("soft event controls require adaptive_error_controlled mode")
        soft = _get(timestep, "events", "soft")
        if not isinstance(soft, dict): raise ValueError("soft events must already be configured")
        field_map = {
            "soft_timestep_cap_factor":"timestep_cap_factor", "soft_max_pH_change":"max_pH_change",
            "soft_secondary_mineral_appearance":"secondary_mineral_appearance",
            "soft_max_reaction_rate_relative_change":"max_reaction_rate_relative_change",
            "soft_reaction_rate_floor":"reaction_rate_floor",
        }
        if soft.get(field_map[kind]) is None: raise ValueError(f"{kind} field must already be configured")
        if kind == "soft_timestep_cap_factor":
            return _rule(target, numerical, "float", "1", None, CONTINUOUS, lower=0, lower_inclusive=False, upper=1, upper_inclusive=False)
        if kind == "soft_max_pH_change":
            return _rule(target, numerical, "float", "1", None, POSITIVE_CONTINUOUS, lower=0, lower_inclusive=False)
        if kind == "soft_secondary_mineral_appearance":
            return _rule(target, numerical, "float", "mol", "mol", POSITIVE_CONTINUOUS, lower=0)
        if kind == "soft_max_reaction_rate_relative_change":
            return _rule(target, numerical, "float", "1", None, POSITIVE_CONTINUOUS, lower=0, lower_inclusive=False)
        return _rule(target, numerical, "float", "mol/s", "mol/s", POSITIVE_CONTINUOUS, lower=0, lower_inclusive=False)

    if kind in KINETIC_KINDS:
        if kinetics_raw is None:
            raise ValueError(f"kinetic target {kind} requires enabled kinetics")
        model = _get(base_raw, "kinetics").get("model") or "palandri_kharaka"
        if kind.startswith("pk_"):
            if model != "palandri_kharaka": raise ValueError(f"{kind} requires palandri_kharaka kinetics")
            pk = _get(kinetics_raw, "ReactionRateModelParams", "PalandriKharaka")
            record = _get(pk, str(target.record), "Mechanisms", str(target.mechanism))
            key = {"pk_lgk":"lgk", "pk_activation_energy":"E", "pk_p":"p", "pk_q":"q"}.get(kind)
            if kind == "pk_catalyst_power": key = str(target.catalyst_property)
            if key not in record: raise ValueError(f"kinetic target field does not exist: {kind}")
            if kind == "pk_lgk":
                return _rule(target, scientific, "float", "lg10(mol m^-2 s^-1)", "lg10(mol m^-2 s^-1)", CONTINUOUS)
            if kind == "pk_activation_energy":
                return _rule(target, scientific, "float", "kJ/mol", "kJ/mol", POSITIVE_CONTINUOUS)
            return _rule(target, scientific, "float", "1", None, CONTINUOUS)
        if model != "kinec": raise ValueError(f"{kind} requires kinec kinetics")
        record = _get(kinetics_raw, str(target.mineral))
        if kind == "kinec_sigma":
            _get(record, "sigma")
            return _rule(target, scientific, "float", "1", None, POSITIVE_CONTINUOUS, lower=0, lower_inclusive=False)
        term = _get(record, "terms", str(target.term))
        key = kind.split("kinec_", 1)[1]
        if key not in term: raise ValueError(f"kinetic target field does not exist: {kind}")
        if kind == "kinec_A":
            return _rule(target, scientific, "float", "mol m^-2 s^-1", "mol m^-2 s^-1", POSITIVE_CONTINUOUS, lower=0, lower_inclusive=False)
        if kind == "kinec_E":
            return _rule(target, scientific, "float", "J/mol", "J/mol", POSITIVE_CONTINUOUS)
        if kind == "kinec_Kc":
            return _rule(target, scientific, "float", "1", None, POSITIVE_CONTINUOUS, lower=0)
        return _rule(target, scientific, "float", "1", None, CONTINUOUS)
    raise ValueError(f"unsupported DoE target: {kind}")


def validate_sampling(parameter: ParameterSpec, resolved: ResolvedTarget) -> None:
    sampling = parameter.sampling
    if sampling.kind not in resolved.allowed_sampling_kinds:
        raise ValueError(
            f"target {resolved.target.kind} does not allow sampling kind {sampling.kind}"
        )
    if resolved.canonical_unit == "1":
        if sampling.entered_unit is not None:
            raise ValueError(f"dimensionless target {resolved.target.kind} must omit entered_unit")
    elif sampling.entered_unit is None:
        raise ValueError(f"dimensional target {resolved.target.kind} requires entered_unit")
    if resolved.data_type == "int" and sampling.kind in {EXPLICIT, DISCRETE}:
        if any(isinstance(v, bool) or not isinstance(v, int) for v in sampling.values):
            raise ValueError(f"integer target {resolved.target.kind} requires integer sample values")


def _time_factor(unit: str, year_days: float | None) -> float:
    factors = {
        "second":1.0, "seconds":1.0, "minute":60.0, "minutes":60.0,
        "hour":3600.0, "hours":3600.0, "day":86400.0, "days":86400.0,
    }
    if unit in {"year", "years"}:
        if year_days is None: raise ValueError("year_definition_days is required for year conversion")
        return float(year_days) * 86400.0
    if unit not in factors: raise ValueError(f"unsupported time unit {unit!r}")
    return factors[unit]


def convert_unit(
    value: float | int,
    entered_unit: str | None,
    canonical_unit: str,
    *,
    year_days: float | None = None,
) -> float | int:
    if canonical_unit == "1":
        if entered_unit is not None: raise ValueError("dimensionless values must not declare a unit")
        return value
    if entered_unit is None: raise ValueError(f"unit is required for canonical unit {canonical_unit}")
    value_f = float(value)
    if canonical_unit == "degC":
        if entered_unit == "degC": return value_f
        if entered_unit == "K": return value_f - 273.15
    if canonical_unit == "bar":
        factors = {"Pa":1e-5, "kPa":1e-2, "MPa":10.0, "bar":1.0}
        if entered_unit in factors: return value_f * factors[entered_unit]
    if canonical_unit == "s": return value_f * _time_factor(entered_unit, year_days)
    if canonical_unit == "kJ/mol":
        if entered_unit == "kJ/mol": return value_f
        if entered_unit == "J/mol": return value_f / 1000.0
    if canonical_unit == "J/mol":
        if entered_unit == "J/mol": return value_f
        if entered_unit == "kJ/mol": return value_f * 1000.0
    if entered_unit == canonical_unit: return value_f
    raise ValueError(f"unsupported conversion {entered_unit!r} -> {canonical_unit!r}")


def from_canonical(
    value: float | int,
    canonical_unit: str,
    source_unit: str | None,
    *,
    year_days: float | None = None,
) -> float | int:
    if canonical_unit == "1": return value
    if source_unit is None: raise ValueError(f"source unit required for {canonical_unit}")
    value_f = float(value)
    if canonical_unit == "degC":
        if source_unit == "degC": return value_f
        if source_unit == "K": return value_f + 273.15
    if canonical_unit == "bar":
        factors = {"Pa":1e-5, "kPa":1e-2, "MPa":10.0, "bar":1.0}
        if source_unit in factors: return value_f / factors[source_unit]
    if canonical_unit == "s": return value_f / _time_factor(source_unit, year_days)
    if canonical_unit == "kJ/mol":
        if source_unit == "kJ/mol": return value_f
        if source_unit == "J/mol": return value_f * 1000.0
    if canonical_unit == "J/mol":
        if source_unit == "J/mol": return value_f
        if source_unit == "kJ/mol": return value_f / 1000.0
    if source_unit == canonical_unit: return value_f
    raise ValueError(f"unsupported conversion {canonical_unit!r} -> {source_unit!r}")


def canonicalize_sampling(
    parameter: ParameterSpec, resolved: ResolvedTarget, *, year_days: float | None
) -> dict[str, Any]:
    validate_sampling(parameter, resolved)
    sampling = parameter.sampling
    data = sampling.model_dump(mode="json")
    if sampling.kind in {EXPLICIT, DISCRETE}:
        data["values"] = [
            resolved.validate_value(
                convert_unit(v, sampling.entered_unit, resolved.canonical_unit, year_days=year_days)
            )
            for v in sampling.values
        ]
    elif sampling.kind in {UNIFORM, LOG}:
        data["lower"] = resolved.validate_value(
            convert_unit(sampling.lower, sampling.entered_unit, resolved.canonical_unit, year_days=year_days)
        )
        data["upper"] = resolved.validate_value(
            convert_unit(sampling.upper, sampling.entered_unit, resolved.canonical_unit, year_days=year_days)
        )
        if not data["lower"] < data["upper"]:
            raise ValueError(f"canonical bounds for {parameter.parameter_id} require lower < upper")
        if sampling.kind == LOG and data["lower"] <= 0:
            raise ValueError("log_uniform canonical lower bound must be positive")
    data["canonical_unit"] = resolved.canonical_unit
    data.pop("entered_unit", None)
    return data


def _year_days(raw: dict[str, Any]) -> float | None:
    return _get(raw, "solver", "timestep", "time").get("year_definition_days")


def apply_case_target(
    raw: dict[str, Any], resolved: ResolvedTarget, canonical_value: float | int
) -> None:
    target = resolved.target
    kind = target.kind
    value = from_canonical(
        canonical_value, resolved.canonical_unit, resolved.source_unit, year_days=_year_days(raw)
    )
    if kind == "temperature": raw["physical"]["temperature_c"] = value; return
    if kind == "pressure": raw["physical"]["pressure_bar"] = value; return
    if kind == "redox_pe": raw["redox"]["pe"] = value; return
    if kind == "co2_fugacity": raw["co2"]["fugacity_bar"] = value; return
    if kind == "co2_initial_amount": raw["co2"]["initial_amount"]["value"] = value; return
    if kind == "brine_species_amount": raw["brine"]["species_amounts"][target.species]["value"] = value; return
    if kind == "brine_element_amount": raw["brine"]["element_amounts"][target.element]["value"] = value; return
    if kind in {"mineral_initial_amount", "mineral_surface_area"}:
        mineral = _find_named(raw["minerals"], str(target.mineral), "mineral")
        field = "initial_amount" if kind == "mineral_initial_amount" else "surface_area"
        mineral[field]["value"] = value; return
    ts = raw["solver"]["timestep"]
    if kind == "solver_duration": ts["time"]["duration_value"] = value; return
    if kind == "solver_max_internal_steps": ts["max_internal_steps"] = int(value); return
    timed = {
        "fixed_dt":"dt", "adaptive_dt_initial":"dt_initial", "adaptive_dt_min":"dt_min",
        "adaptive_dt_max":"dt_max", "error_dt_initial":"dt_initial",
        "error_dt_min":"dt_min", "error_dt_max":"dt_max",
    }
    if kind in timed: ts["step_size"][timed[kind]]["value"] = value; return
    step = {
        "adaptive_growth_factor":"growth_factor", "adaptive_shrink_factor":"shrink_factor",
        "adaptive_max_retries":"max_retries_per_step", "error_safety_factor":"safety_factor",
        "error_growth_factor":"growth_factor", "error_shrink_factor":"shrink_factor",
        "solver_failure_shrink_factor":"solver_failure_shrink_factor",
        "error_max_retries":"max_retries_per_step",
    }
    if kind in step:
        ts["step_size"][step[kind]] = int(value) if "retries" in kind else value; return
    if kind == "richardson_temporal_order": ts["error_control"]["temporal_order"] = value; return
    if kind == "richardson_relative_tolerance": ts["error_control"]["relative_tolerance"] = value; return
    if kind in {"controlled_mineral_absolute_tolerance", "controlled_mineral_reference_floor"}:
        mineral = _find_named(ts["error_control"]["controlled_minerals"], str(target.mineral), "controlled mineral")
        field = "absolute_tolerance" if kind.endswith("absolute_tolerance") else "reference_floor"
        mineral[field]["value"] = value; return
    hard = ts.get("events", {}).get("hard_mineral_exhaustion")
    if kind == "hard_exhaustion_amount_tolerance": hard["amount_tolerance"]["value"] = value; return
    if kind == "hard_exhaustion_time_tolerance": hard["time_tolerance"]["value"] = value; return
    if kind == "hard_exhaustion_restart_dt": hard["restart_dt"]["value"] = value; return
    if kind == "hard_exhaustion_max_localizations": hard["max_localizations"] = int(value); return
    soft = ts.get("events", {}).get("soft")
    soft_map = {
        "soft_timestep_cap_factor":"timestep_cap_factor",
        "soft_max_pH_change":"max_pH_change",
        "soft_max_reaction_rate_relative_change":"max_reaction_rate_relative_change",
    }
    if kind in soft_map: soft[soft_map[kind]] = value; return
    if kind == "soft_secondary_mineral_appearance": soft["secondary_mineral_appearance"]["value"] = value; return
    if kind == "soft_reaction_rate_floor": soft["reaction_rate_floor"]["value"] = value; return
    if kind in KINETIC_KINDS: return
    raise ValueError(f"unsupported case target {kind}")


def apply_kinetic_target(
    raw: dict[str, Any], target: Target, canonical_value: float | int
) -> None:
    kind = target.kind
    if kind.startswith("pk_"):
        mechanism = raw["ReactionRateModelParams"]["PalandriKharaka"][target.record]["Mechanisms"][target.mechanism]
        key = {"pk_lgk":"lgk", "pk_activation_energy":"E", "pk_p":"p", "pk_q":"q"}.get(kind, target.catalyst_property)
        if key not in mechanism: raise ValueError(f"kinetic target field no longer exists: {kind}")
        mechanism[key] = float(canonical_value); return
    if kind.startswith("kinec_"):
        record = raw[target.mineral]
        if kind == "kinec_sigma": record["sigma"] = float(canonical_value); return
        key = kind.split("kinec_", 1)[1]
        if key not in record["terms"][target.term]:
            raise ValueError(f"kinetic target field no longer exists: {kind}")
        record["terms"][target.term][key] = float(canonical_value); return
    raise ValueError(f"not a kinetic target: {kind}")


def materialise_candidate(
    base_raw: dict[str, Any],
    parameters: list[tuple[ResolvedTarget, float | int]],
    kinetics_raw: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    case = deepcopy(base_raw)
    kinetics = deepcopy(kinetics_raw) if kinetics_raw is not None else None
    for resolved, value in parameters:
        resolved.validate_value(value)
        if resolved.target.kind in KINETIC_KINDS:
            if kinetics is None: raise ValueError("kinetic target requires kinetics data")
            apply_kinetic_target(kinetics, resolved.target, value)
        else:
            apply_case_target(case, resolved, value)
    return case, kinetics

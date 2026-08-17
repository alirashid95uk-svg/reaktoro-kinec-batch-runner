#!/usr/bin/env python
"""Generate a standalone, readable Reaktoro equivalent from a case YAML.

The generator intentionally does not import ``batch_runner``.  It is an
independent audit path: YAML in, plain Reaktoro Python out.

Safety rule: every current Reaktoro-facing field is either translated or
explicitly classified as non-Reaktoro metadata. Unknown fields fail loudly so
new physics cannot be silently omitted from the generated equivalent.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent

DEFAULT_KINETIC_PATHS = {
    "palandri_kharaka": "data/kinetics/PalandriKharaka_local.yaml",
    "kinec": "data/kinetics/kinec_rates_minimal.yaml",
}

TOP_LEVEL_KEYS = {
    "case", "paths", "database", "activity_models", "physical", "brine",
    "co2", "redox", "kinetics", "minerals", "solver", "postprocessing",
    "validation", "outputs",
}

SCHEMA_KEYS = {
    "database": {"source", "name", "path"},
    "activity_models": {"aqueous", "gas"},
    "physical": {"temperature_c", "pressure_bar"},
    "brine": {"aqueous_elements", "species_amounts"},
    "co2": {"mode", "gas_species", "initial_amount", "fugacity_bar"},
    "redox": {"enabled", "pe", "apply_during"},
    "kinetics": {"enabled", "model", "path"},
    "mineral": {
        "name", "role", "initial_amount", "surface_area",
        "surface_area_basis", "surface_area_provenance", "selection_reason",
    },
    "solver": {"workflow", "timestep", "restart"},
    "workflow": {"mode", "precondition_kinetics"},
    "restart": {"enabled", "from_checkpoint"},
    "timestep_fixed": {
        "mode", "time", "step_size", "max_internal_steps",
        "output_schedule", "checkpoint_schedule",
    },
    "timestep_adaptive": {
        "mode", "time", "step_size", "acceptance", "max_internal_steps",
        "output_schedule", "checkpoint_schedule",
    },
    "time": {"duration_value", "duration_unit", "year_definition_days"},
    "fixed_step_size": {"dt"},
    "adaptive_step_size": {
        "dt_initial", "dt_min", "dt_max", "growth_factor", "shrink_factor",
        "max_retries_per_step",
    },
    "time_value": {"value", "unit"},
    "output_schedule": {
        "mode", "include_initial", "include_final", "explicit_times", "logarithmic",
    },
    "logarithmic": {"start", "end", "points_per_decade"},
    "checkpoint_schedule": {"enabled", "times"},
    "acceptance": {
        "enabled", "fail_on_non_finite", "negative_amount_tolerance_mol",
        "max_delta_pH", "max_delta_saturation_index", "selected_species_change",
        "mineral_change", "element_conservation", "max_relative_rate_change",
    },
    "amount_change": {
        "absolute_tolerance_mol", "relative_tolerance", "reference_floor_mol",
    },
    "element_conservation": {
        "enabled", "relative_tolerance", "absolute_tolerance_mol",
    },
    "postprocessing": {
        "requested_species", "requested_minerals", "aqueous_molalities",
        "saturation_indices", "reaction_rates", "element_budget",
        "carbon_inventory", "mineral_volume_change", "regime_classification",
        "surface_area_audit", "workflow_comparison",
        "secondary_mineral_assemblage", "surrogate_dataset",
        "porosity_permeability",
    },
}

WORKFLOW_MODES = {
    "equilibrium_only",
    "closed_kinetics",
    "fixed_fugacity_initial_equilibrium_then_closed_kinetics",
    "fixed_fugacity_during_kinetic_steps",
}

TIME_FACTORS = {
    "second": 1.0, "seconds": 1.0,
    "minute": 60.0, "minutes": 60.0,
    "hour": 3600.0, "hours": 3600.0,
    "day": 86400.0, "days": 86400.0,
}


def _expect_mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be a mapping")
    return value


def _expect_keys(value: Any, allowed: set[str], path: str) -> dict[str, Any]:
    mapping = _expect_mapping(value, path)
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise ValueError(
            f"unsupported YAML field(s) at {path}: {', '.join(unknown)}. "
            "Generation stopped because the Reaktoro equivalent may be incomplete."
        )
    return mapping


def _py(value: Any) -> str:
    return repr(value)


def _amount_code(target: str, amount: dict[str, Any]) -> str:
    amount = _expect_keys(amount, {"value", "unit"}, target)
    return f"{target}, {_py(amount['value'])}, {_py(amount['unit'])}"


def _resolve_project_path(value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _time_seconds(item: dict[str, Any], year_days: float | None, path: str) -> float:
    item = _expect_keys(item, SCHEMA_KEYS["time_value"], path)
    value = float(item["value"])
    unit = item["unit"]
    if unit in {"year", "years"}:
        if year_days is None:
            raise ValueError(f"{path} uses years but year_definition_days is missing")
        return value * float(year_days) * 86400.0
    if unit not in TIME_FACTORS:
        raise ValueError(f"unsupported time unit at {path}: {unit}")
    return value * TIME_FACTORS[unit]


def _duration_seconds(time_cfg: dict[str, Any]) -> tuple[float, float | None]:
    time_cfg = _expect_keys(time_cfg, SCHEMA_KEYS["time"], "solver.timestep.time")
    year_days = time_cfg.get("year_definition_days")
    unit = time_cfg["duration_unit"]
    value = float(time_cfg["duration_value"])
    if unit in {"year", "years"}:
        if year_days is None:
            raise ValueError("year_definition_days is required when duration uses years")
        return value * float(year_days) * 86400.0, float(year_days)
    if unit not in TIME_FACTORS:
        raise ValueError(f"unsupported duration unit: {unit}")
    return value * TIME_FACTORS[unit], (None if year_days is None else float(year_days))


def _resolve_schedule(
    schedule: dict[str, Any] | None,
    duration_s: float,
    year_days: float | None,
) -> tuple[str, bool, bool, list[float] | None]:
    if schedule is None:
        return "every_internal_step", True, True, None
    schedule = _expect_keys(schedule, SCHEMA_KEYS["output_schedule"], "solver.timestep.output_schedule")
    mode = schedule.get("mode", "every_internal_step")
    include_initial = bool(schedule.get("include_initial", True))
    include_final = bool(schedule.get("include_final", True))
    if mode not in {"every_internal_step", "explicit", "logarithmic", "hybrid"}:
        raise ValueError(f"unsupported output_schedule.mode: {mode}")
    if mode == "every_internal_step":
        return mode, include_initial, include_final, None

    times: set[float] = set()
    for i, item in enumerate(schedule.get("explicit_times", [])):
        times.add(_time_seconds(item, year_days, f"solver.timestep.output_schedule.explicit_times[{i}]"))

    log = schedule.get("logarithmic")
    if log is not None:
        log = _expect_keys(log, SCHEMA_KEYS["logarithmic"], "solver.timestep.output_schedule.logarithmic")
        start = _time_seconds(log["start"], year_days, "solver.timestep.output_schedule.logarithmic.start")
        end = _time_seconds(log["end"], year_days, "solver.timestep.output_schedule.logarithmic.end")
        ppd = int(log["points_per_decade"])
        if start <= 0 or end < start or ppd <= 0:
            raise ValueError("invalid logarithmic output schedule")
        span = math.log10(end / start) if end > start else 0.0
        last_index = int(math.floor(span * ppd + 1e-12))
        for i in range(last_index + 1):
            value = start * (10.0 ** (i / ppd))
            if value <= end * (1.0 + 1e-12):
                times.add(value)
        times.add(end)

    if include_initial:
        times.add(0.0)
    if include_final:
        times.add(duration_s)
    if any(t < 0 or t > duration_s for t in times):
        raise ValueError("output schedule contains time outside configured duration")
    return mode, include_initial, include_final, sorted(times)


def _resolve_checkpoints(
    cfg: dict[str, Any] | None,
    duration_s: float,
    year_days: float | None,
) -> list[float]:
    if cfg is None:
        return []
    cfg = _expect_keys(cfg, SCHEMA_KEYS["checkpoint_schedule"], "solver.timestep.checkpoint_schedule")
    enabled = bool(cfg.get("enabled", False))
    raw = cfg.get("times", [])
    if not enabled:
        if raw:
            raise ValueError("disabled checkpoint_schedule must not define times")
        return []
    times = [
        _time_seconds(item, year_days, f"solver.timestep.checkpoint_schedule.times[{i}]")
        for i, item in enumerate(raw)
    ]
    if any(t < 0 or t > duration_s for t in times):
        raise ValueError("checkpoint time exceeds configured duration")
    return sorted(set(times))


def _validate_structure(cfg: dict[str, Any]) -> None:
    unknown_top = sorted(set(cfg) - TOP_LEVEL_KEYS)
    if unknown_top:
        raise ValueError(
            "unsupported top-level YAML section(s): " + ", ".join(unknown_top)
            + ". Generation stopped because new physics must be mapped explicitly."
        )

    for key in ("database", "activity_models", "physical", "brine", "co2", "redox", "kinetics"):
        _expect_keys(cfg[key], SCHEMA_KEYS[key], key)

    for i, mineral in enumerate(cfg["minerals"]):
        _expect_keys(mineral, SCHEMA_KEYS["mineral"], f"minerals[{i}]")

    solver = _expect_keys(cfg["solver"], SCHEMA_KEYS["solver"], "solver")
    workflow = _expect_keys(solver["workflow"], SCHEMA_KEYS["workflow"], "solver.workflow")
    if workflow["mode"] not in WORKFLOW_MODES:
        raise ValueError(f"unsupported solver.workflow.mode: {workflow['mode']}")
    restart = _expect_keys(solver.get("restart", {}), SCHEMA_KEYS["restart"], "solver.restart")
    if restart.get("enabled", False) or restart.get("from_checkpoint") is not None:
        raise ValueError("automatic restart is not supported by the current runner or generator")

    timestep = _expect_mapping(solver["timestep"], "solver.timestep")
    mode = timestep.get("mode")
    if mode == "fixed":
        _expect_keys(timestep, SCHEMA_KEYS["timestep_fixed"], "solver.timestep")
        _expect_keys(timestep["step_size"], SCHEMA_KEYS["fixed_step_size"], "solver.timestep.step_size")
    elif mode in {"adaptive", "adaptive_long_horizon"}:
        _expect_keys(timestep, SCHEMA_KEYS["timestep_adaptive"], "solver.timestep")
        _expect_keys(timestep["step_size"], SCHEMA_KEYS["adaptive_step_size"], "solver.timestep.step_size")
        acceptance = _expect_keys(timestep["acceptance"], SCHEMA_KEYS["acceptance"], "solver.timestep.acceptance")
        for name in ("selected_species_change", "mineral_change"):
            if acceptance.get(name) is not None:
                _expect_keys(acceptance[name], SCHEMA_KEYS["amount_change"], f"solver.timestep.acceptance.{name}")
        _expect_keys(
            acceptance["element_conservation"],
            SCHEMA_KEYS["element_conservation"],
            "solver.timestep.acceptance.element_conservation",
        )
        if acceptance.get("max_relative_rate_change") is not None:
            raise ValueError("max_relative_rate_change is not implemented by the current runner")
    else:
        raise ValueError(f"unsupported solver.timestep.mode: {mode}")

    _expect_keys(cfg["postprocessing"], SCHEMA_KEYS["postprocessing"], "postprocessing")


def _generate_kinec_helpers() -> list[str]:
    return [
        "# Custom Kinec rate model reproduced from the project adapter.",
        "R_GAS = 8.314462618",
        "",
        "def _kinec_term_flux(term, T, activity=None):",
        "    value = float(term['A']) * math.exp(-float(term['E']) / (R_GAS * T))",
        "    if 'n' in term:",
        "        if activity is None:",
        "            raise ValueError('activity is required for a term with reaction order n')",
        "        n = float(term['n'])",
        "        if activity <= 0.0 and n != 0.0:",
        "            raise ValueError('activity must be positive when raised to a nonzero power')",
        "        value *= activity ** n",
        "    return value",
        "",
        "def _kinec_carbonate_flux(term, T, a_hco3, a_co3):",
        "    return (",
        "        float(term['A']) * math.exp(-float(term['E']) / (R_GAS * T))",
        "        / (1.0 + float(term.get('Kc', 0.0)) * (a_hco3 + a_co3))",
        "    )",
        "",
        "def make_kinec_rate_model(record, mineral):",
        "    def rate(props):",
        "        T = float(props.temperature())",
        "        area = float(props.surfaceArea(mineral))",
        "        omega = float(rkt.AqueousProps(props).saturationRatio(mineral))",
        "        aH = float(props.speciesActivity('H+'))",
        "        terms = record.get('terms', {})",
        "        flux = 0.0",
        "        if 'acid' in terms:",
        "            flux += _kinec_term_flux(terms['acid'], T, aH)",
        "        if 'neutral' in terms:",
        "            flux += _kinec_term_flux(terms['neutral'], T)",
        "        if 'basic' in terms:",
        "            flux += _kinec_term_flux(terms['basic'], T, aH)",
        "        if record['family'] == 'carbonate' and 'carbonate' in terms:",
        "            flux += _kinec_carbonate_flux(",
        "                terms['carbonate'], T,",
        "                float(props.speciesActivity('HCO3-')),",
        "                float(props.speciesActivity('CO3-2')),",
        "            )",
        "        affinity = 1.0 - omega ** (1.0 / float(record['sigma']))",
        "        return rkt.ReactionRate(area * flux * affinity)",
        "    return rkt.ReactionRateModel(rate)",
        "",
    ]


def _generate_conditions_function(cfg: dict[str, Any]) -> list[str]:
    co2 = cfg["co2"]
    redox = cfg["redox"]
    workflow = cfg["solver"]["workflow"]["mode"]
    t = cfg["physical"]["temperature_c"]
    p = cfg["physical"]["pressure_bar"]

    return [
        "def build_conditions(stage, system, state):",
        f"    workflow = {_py(workflow)}",
        f"    co2_mode = {_py(co2['mode'])}",
        f"    redox_enabled = {_py(bool(redox['enabled']))}",
        f"    redox_stage = {_py(redox.get('apply_during'))}",
        "    fixed_fugacity = False",
        "    if co2_mode == 'fixed_fugacity':",
        "        if workflow in ('equilibrium_only', 'fixed_fugacity_initial_equilibrium_then_closed_kinetics'):",
        "            fixed_fugacity = stage == 'initial_equilibrium'",
        "        elif workflow == 'fixed_fugacity_during_kinetic_steps':",
        "            fixed_fugacity = True",
        "    redox_applies = redox_enabled and (",
        "        (redox_stage == 'initial_equilibrium_only' and stage == 'initial_equilibrium')",
        "        or (redox_stage == 'kinetic_steps' and stage == 'kinetic_steps')",
        "    )",
        "    if not fixed_fugacity and not redox_applies:",
        "        return None, None",
        "    specs = rkt.EquilibriumSpecs.TP(system)",
        *((["    if fixed_fugacity:", f"        specs.fugacity({_py(co2.get('gas_species'))})"])
          if co2["mode"] == "fixed_fugacity" else []),
        "    if redox_applies:",
        "        specs.pE()",
        "    conditions = rkt.EquilibriumConditions(specs)",
        f"    conditions.temperature({_py(t)}, 'celsius')",
        f"    conditions.pressure({_py(p)}, 'bar')",
        *((["    if fixed_fugacity:", f"        conditions.fugacity({_py(co2.get('gas_species'))}, {_py(co2.get('fugacity_bar'))}, 'bar')"])
          if co2["mode"] == "fixed_fugacity" else []),
        *((["    if redox_applies:", f"        conditions.pE({_py(redox.get('pe'))})"])
          if redox["enabled"] else []),
        "    conditions.setInitialComponentAmountsFromState(state)",
        "    return specs, conditions",
        "",
    ]


def _generate_acceptance_function(cfg: dict[str, Any]) -> list[str]:
    ts = cfg["solver"]["timestep"]
    if ts["mode"] == "fixed":
        return []
    a = ts["acceptance"]
    requested_species = cfg["postprocessing"]["requested_species"]
    minerals = [m["name"] for m in cfg["minerals"]]
    lines = [
        "def trial_accepted(system, accepted_state, trial_state):",
        "    reasons = []",
        "    trial_amounts = [float(x) for x in trial_state.speciesAmounts()]",
    ]
    if a.get("negative_amount_tolerance_mol") is not None:
        lines += [
            f"    negative_tol = {_py(a['negative_amount_tolerance_mol'])}",
            "    if any(x < -negative_tol for x in trial_amounts):",
            "        reasons.append('negative_species_amount_below_tolerance')",
        ]
    if a.get("max_delta_pH") is not None or a.get("fail_on_non_finite"):
        lines += [
            "    accepted_aq = rkt.AqueousProps(accepted_state)",
            "    trial_aq = rkt.AqueousProps(trial_state)",
            "    accepted_pH = float(accepted_aq.pH())",
            "    trial_pH = float(trial_aq.pH())",
        ]
        if a.get("max_delta_pH") is not None:
            lines += [
                f"    if abs(trial_pH - accepted_pH) > {_py(a['max_delta_pH'])}:",
                "        reasons.append('max_delta_pH')",
            ]
    else:
        lines += [
            "    accepted_aq = rkt.AqueousProps(accepted_state)",
            "    trial_aq = rkt.AqueousProps(trial_state)",
        ]
    if a.get("max_delta_saturation_index") is not None or a.get("fail_on_non_finite"):
        lines += [
            "    trial_saturation_indices = []",
            f"    for mineral in {_py(minerals)}:",
            "        accepted_si = float(accepted_aq.saturationIndex(mineral))",
            "        trial_si = float(trial_aq.saturationIndex(mineral))",
            "        trial_saturation_indices.append(trial_si)",
        ]
        if a.get("max_delta_saturation_index") is not None:
            lines += [
                f"        if abs(trial_si - accepted_si) > {_py(a['max_delta_saturation_index'])}:",
                "            reasons.append('max_delta_saturation_index')",
            ]
    for key, names, reason in (
        ("selected_species_change", requested_species, "selected_species_change_tolerance"),
        ("mineral_change", minerals, "mineral_change_tolerance"),
    ):
        tol = a.get(key)
        if tol is not None:
            lines += [
                f"    for name in {_py(names)}:",
                "        before = float(accepted_state.speciesAmount(name))",
                "        after = float(trial_state.speciesAmount(name))",
                "        delta = abs(after - before)",
                f"        allowed = {_py(tol['absolute_tolerance_mol'])} + {_py(tol['relative_tolerance'])} * max(abs(before), {_py(tol['reference_floor_mol'])})",
                "        if delta > allowed:",
                f"            reasons.append({_py(reason)})",
                "            break",
            ]
    ec = a["element_conservation"]
    if ec["enabled"]:
        rel = ec.get("relative_tolerance") or 0.0
        abs_tol = ec.get("absolute_tolerance_mol") or 0.0
        lines += [
            "    before_elements = [float(x) for x in accepted_state.elementAmounts()]",
            "    after_elements = [float(x) for x in trial_state.elementAmounts()]",
            "    for before, after in zip(before_elements, after_elements):",
            f"        allowed = {_py(abs_tol)} + {_py(rel)} * abs(before)",
            "        if abs(after - before) > allowed:",
            "            reasons.append('element_conservation')",
            "            break",
        ]
    if a.get("fail_on_non_finite"):
        lines += [
            "    values = trial_amounts + [float(trial_state.temperature()), float(trial_state.pressure()), float(trial_state.charge())]",
            "    values += [float(trial_aq.pH())]",
            "    values += trial_saturation_indices",
            "    if any(not math.isfinite(x) for x in values):",
            "        reasons.append('non_finite_state_value')",
        ]
    lines += ["    return not reasons, ';'.join(dict.fromkeys(reasons))", ""]
    return lines


def generate_reaktoro_code(cfg: dict[str, Any], source_path: Path) -> str:
    _validate_structure(cfg)

    db = cfg["database"]
    activity = cfg["activity_models"]
    physical = cfg["physical"]
    brine = cfg["brine"]
    co2 = cfg["co2"]
    kinetics = cfg["kinetics"]
    minerals = cfg["minerals"]
    solver = cfg["solver"]
    workflow = solver["workflow"]
    timestep = solver["timestep"]

    if activity["aqueous"] != "phreeqc":
        raise ValueError(f"unsupported aqueous activity model: {activity['aqueous']}")
    if co2["mode"] == "finite" and activity.get("gas") != "peng_robinson_phreeqc":
        raise ValueError("finite CO2 requires gas activity model peng_robinson_phreeqc")
    if activity.get("gas") not in {None, "peng_robinson_phreeqc"}:
        raise ValueError(f"unsupported gas activity model: {activity.get('gas')}")

    kinetic_model = kinetics.get("model") or ("palandri_kharaka" if kinetics["enabled"] else None)
    kinetic_path = kinetics.get("path") or (DEFAULT_KINETIC_PATHS[kinetic_model] if kinetic_model else None)
    if kinetic_model not in {None, "palandri_kharaka", "kinec"}:
        raise ValueError(f"unsupported kinetics.model: {kinetic_model}")

    duration_s, year_days = _duration_seconds(timestep["time"])
    output_mode, include_initial, include_final, output_times = _resolve_schedule(
        timestep.get("output_schedule"), duration_s, year_days
    )
    checkpoints = _resolve_checkpoints(timestep.get("checkpoint_schedule"), duration_s, year_days)

    lines = [
        '"""Generated Reaktoro equivalent. Review this file before trusting the case physics.',
        '',
        f'Source YAML: {source_path.as_posix()}',
        'Generated independently from batch_runner.simulator.',
        '"""',
        '',
        'from pathlib import Path',
        'import math',
        'import yaml',
        'import reaktoro as rkt',
        '',
    ]

    if kinetic_model == "kinec":
        lines.extend(_generate_kinec_helpers())

    lines += ["# Thermodynamic database"]
    if db["source"] == "local":
        resolved = _resolve_project_path(db["path"])
        lines += [f"database = rkt.PhreeqcDatabase.fromFile({_py(str(resolved))})"]
    elif db["source"] == "embedded":
        lines += [f"database = rkt.PhreeqcDatabase.withName({_py(db['name'])})"]
    else:
        raise ValueError(f"unsupported database.source: {db['source']}")

    lines += [
        "",
        "# Chemical system",
        f"aqueous = rkt.AqueousPhase(rkt.speciate({_py(brine['aqueous_elements'])}))",
        "aqueous.setActivityModel(rkt.ActivityModelPhreeqc(database))",
        "components = [aqueous]",
    ]
    if co2["mode"] == "finite":
        lines += [
            f"gas = rkt.GaseousPhase([{_py(co2['gas_species'])}])",
            "gas.setActivityModel(rkt.ActivityModelPengRobinsonPhreeqc())",
            "components.append(gas)",
        ]
    elif co2["mode"] not in {"disabled", "fixed_fugacity"}:
        raise ValueError(f"unsupported co2.mode: {co2['mode']}")

    mineral_names = [m["name"] for m in minerals]
    lines += [f"components.append(rkt.MineralPhases({_py(mineral_names)}))"]

    if kinetics["enabled"]:
        kpath = _resolve_project_path(kinetic_path)
        if kinetic_model == "palandri_kharaka":
            lines += [f"kinetic_params = rkt.Params.local({_py(str(kpath))})"]
        else:
            lines += [
                f"with Path({_py(str(kpath))}).open(encoding='utf-8') as stream:",
                "    kinetic_params = yaml.safe_load(stream)",
            ]
        for m in minerals:
            if m["role"] != "kinetic":
                continue
            if m.get("surface_area") is None:
                raise ValueError(f"kinetic mineral {m['name']} is missing surface_area")
            lines += [f"reaction = rkt.MineralReaction({_py(m['name'])})"]
            if kinetic_model == "palandri_kharaka":
                lines += ["reaction.setRateModel(rkt.ReactionRateModelPalandriKharaka(kinetic_params))"]
            else:
                lines += [f"reaction.setRateModel(make_kinec_rate_model(kinetic_params[{_py(m['name'])}], {_py(m['name'])}))"]
            lines += [
                "components.append(reaction)",
                f"components.append(rkt.MineralSurface({_py(m['name'])}, {_py(m['surface_area']['value'])}, {_py(m['surface_area']['unit'])}))",
            ]
    elif any(m["role"] == "kinetic" for m in minerals):
        raise ValueError("kinetic minerals present while kinetics.enabled is false")

    lines += [
        "system = rkt.ChemicalSystem(database, *components)",
        "",
        "# Initial chemical state",
        "state = rkt.ChemicalState(system)",
        f"state.temperature({_py(physical['temperature_c'])}, 'celsius')",
        f"state.pressure({_py(physical['pressure_bar'])}, 'bar')",
    ]
    for species, amount in brine["species_amounts"].items():
        lines += [f"state.set({_amount_code(_py(species), amount)})"]
    for m in minerals:
        if m.get("initial_amount") is not None:
            lines += [f"state.set({_amount_code(_py(m['name']), m['initial_amount'])})"]
    if co2["mode"] == "finite":
        lines += [f"state.set({_amount_code(_py(co2['gas_species']), co2['initial_amount'])})"]

    lines += [""]
    lines.extend(_generate_conditions_function(cfg))
    lines.extend(_generate_acceptance_function(cfg))

    need_initial_equilibrium = (
        workflow["mode"] == "equilibrium_only"
        or workflow["mode"] == "fixed_fugacity_initial_equilibrium_then_closed_kinetics"
        or (cfg["redox"]["enabled"] and cfg["redox"].get("apply_during") == "initial_equilibrium_only")
    )

    lines += ["# Solver workflow"]
    if need_initial_equilibrium:
        lines += [
            "initial_specs, initial_conditions = build_conditions('initial_equilibrium', system, state)",
            "equilibrium_solver = rkt.EquilibriumSolver(initial_specs) if initial_specs is not None else rkt.EquilibriumSolver(system)",
            "result = equilibrium_solver.solve(state, initial_conditions) if initial_conditions is not None else equilibrium_solver.solve(state)",
            "if not result.succeeded():",
            "    raise RuntimeError('initial equilibrium failed')",
        ]
    if workflow["mode"] == "equilibrium_only":
        lines += ["print(state)", ""]
        return "\n".join(lines) + "\n"

    lines += [
        "kinetic_specs, kinetic_conditions = build_conditions('kinetic_steps', system, state)",
        "kinetic_solver = rkt.KineticsSolver(kinetic_specs) if kinetic_specs is not None else rkt.KineticsSolver(system)",
    ]
    if workflow.get("precondition_kinetics") and workflow["mode"] != "fixed_fugacity_initial_equilibrium_then_closed_kinetics":
        lines += [
            "precondition_result = (",
            "    kinetic_solver.precondition(state, kinetic_conditions)",
            "    if kinetic_conditions is not None",
            "    else kinetic_solver.precondition(state)",
            ")",
            "if not precondition_result.succeeded():",
            "    raise RuntimeError('kinetics precondition failed')",
        ]

    lines += [
        f"duration_s = {_py(duration_s)}",
        f"output_mode = {_py(output_mode)}",
        f"include_initial = {_py(include_initial)}",
        f"include_final = {_py(include_final)}",
        f"output_times_s = {_py(output_times)}",
        f"checkpoint_times_s = {_py(checkpoints)}",
        "",
        "def solve_kinetic_step(dt_s):",
        "    result = (",
        "        kinetic_solver.solve(state, dt_s, kinetic_conditions)",
        "        if kinetic_conditions is not None",
        "        else kinetic_solver.solve(state, dt_s)",
        "    )",
        "    return result",
        "",
    ]

    if timestep["mode"] == "fixed":
        dt_s = _time_seconds(timestep["step_size"]["dt"], year_days, "solver.timestep.step_size.dt")
        forced = set(checkpoints)
        if output_times is not None:
            forced.update(output_times)
        forced.discard(0.0)
        lines += [
            f"base_dt_s = {_py(dt_s)}",
            f"max_internal_steps = {_py(timestep.get('max_internal_steps', 100000))}",
            f"forced_targets_s = {_py(sorted(forced))}",
            "time_s = 0.0",
            "attempts = 0",
            "base_target_s = base_dt_s",
            "forced_index = 0",
            "while time_s < duration_s:",
            "    if attempts >= max_internal_steps:",
            "        raise RuntimeError('max_internal_steps exceeded')",
            "    target_s = min(base_target_s, duration_s)",
            "    if forced_index < len(forced_targets_s):",
            "        forced = forced_targets_s[forced_index]",
            "        if forced > time_s and forced < target_s:",
            "            target_s = forced",
            "        elif forced <= time_s:",
            "            forced_index += 1",
            "            continue",
            "    dt_s = target_s - time_s",
            "    accepted_state = rkt.ChemicalState(state)",
            "    result = solve_kinetic_step(dt_s)",
            "    attempts += 1",
            "    if not result.succeeded():",
            "        state.assign(accepted_state)",
            "        raise RuntimeError(f'kinetic step failed at target {target_s} s')",
            "    time_s = target_s",
            "    if abs(time_s - base_target_s) <= 1e-12:",
            "        base_target_s += base_dt_s",
            "    if forced_index < len(forced_targets_s) and abs(time_s - forced_targets_s[forced_index]) <= 1e-12:",
            "        forced_index += 1",
        ]
    else:
        step = timestep["step_size"]
        dt_initial = _time_seconds(step["dt_initial"], year_days, "solver.timestep.step_size.dt_initial")
        dt_min = _time_seconds(step["dt_min"], year_days, "solver.timestep.step_size.dt_min")
        dt_max = _time_seconds(step["dt_max"], year_days, "solver.timestep.step_size.dt_max")
        forced = set(checkpoints)
        if output_times is not None:
            forced.update(output_times)
        forced.add(duration_s)
        forced.discard(0.0)
        lines += [
            f"controller_dt_s = {_py(dt_initial)}",
            f"dt_min_s = {_py(dt_min)}",
            f"dt_max_s = {_py(dt_max)}",
            f"growth_factor = {_py(step['growth_factor'])}",
            f"shrink_factor = {_py(step['shrink_factor'])}",
            f"max_retries_per_step = {_py(step['max_retries_per_step'])}",
            f"max_internal_steps = {_py(timestep.get('max_internal_steps', 100000))}",
            f"forced_targets_s = {_py(sorted(forced))}",
            "time_s = 0.0",
            "attempts = 0",
            "retries_at_current_time = 0",
            "while time_s < duration_s:",
            "    if attempts >= max_internal_steps:",
            "        raise RuntimeError('max_internal_steps exceeded')",
            "    forced_target_s = min(t for t in forced_targets_s if t > time_s)",
            "    target_s = min(time_s + controller_dt_s, forced_target_s)",
            "    dt_s = target_s - time_s",
            "    accepted_state = rkt.ChemicalState(state)",
            "    result = solve_kinetic_step(dt_s)",
            "    attempts += 1",
            "    accepted, reason = (False, 'solver_failure') if not result.succeeded() else trial_accepted(system, accepted_state, state)",
            "    if not accepted:",
            "        state.assign(accepted_state)",
            "        retries_at_current_time += 1",
            "        if retries_at_current_time > max_retries_per_step or dt_s <= dt_min_s:",
            "            raise RuntimeError(f'adaptive step rejected: {reason}')",
            "        controller_dt_s = max(dt_min_s, dt_s * shrink_factor)",
            "        continue",
            "    time_s = target_s",
            "    retries_at_current_time = 0",
            "    controller_dt_s = min(dt_max_s, max(dt_min_s, controller_dt_s * growth_factor))",
        ]

    lines += ["", "print(state)", ""]
    return "\n".join(lines) + "\n"


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        data = yaml.safe_load(stream)
    if not isinstance(data, dict):
        raise ValueError("case YAML must contain a top-level mapping")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("yaml_path", type=Path, help="Case YAML to translate")
    parser.add_argument("-o", "--output", type=Path, help="Output Python file")
    parser.add_argument("--stdout", action="store_true", help="Print generated code instead of writing it")
    args = parser.parse_args()

    yaml_path = args.yaml_path.resolve()
    cfg = load_yaml(yaml_path)
    code = generate_reaktoro_code(cfg, yaml_path)

    if args.stdout:
        print(code, end="")
        return 0

    output = args.output or yaml_path.with_name(yaml_path.stem + "_reaktoro.py")
    output = output.resolve()
    output.write_text(code, encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

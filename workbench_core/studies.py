"""Deterministic, constraint-gated generation of parameter-study cases."""

from __future__ import annotations

import copy
import csv
import io
import itertools
import json
import math
import os
import random
import re
from pathlib import Path
from typing import Any, Callable

from ruamel.yaml import YAML

from batch_runner.config import CaseConfig
from workbench_core.fingerprints import sha256_file
from workbench_core.persistence import atomic_write_json, atomic_write_text
from workbench_core.schemas.common import utc_now
from workbench_core.schemas.study_spec import (
    ConstraintDefinition,
    ConstraintOutcome,
    GeneratedSampleRecord,
    ParameterDefinition,
    StudyManifest,
    StudySpec,
)


STUDY_GENERATOR_VERSION = "1.0"
APPROVED_PATHS = (
    (re.compile(r"^physical\.temperature_c$"), "number", "degC"),
    (re.compile(r"^physical\.pressure_bar$"), "number", "bar"),
    (re.compile(r"^co2\.fugacity_bar$"), "number", "bar"),
    (re.compile(r"^co2\.initial_amount\.value$"), "number", None),
    (re.compile(r"^brine\.species_amounts\.[^.]+\.value$"), "number", None),
    (re.compile(r"^minerals\.\d+\.initial_amount\.value$"), "number", None),
    (re.compile(r"^minerals\.\d+\.surface_area\.value$"), "number", None),
    (re.compile(r"^solver\.timestep\.time\.duration_value$"), "number", None),
    (
        re.compile(
            r"^solver\.timestep\.step_size\.(?:dt|dt_initial|dt_min|dt_max)\.value$"
        ),
        "number",
        None,
    ),
    (re.compile(r"^co2\.mode$"), "string", None),
    (re.compile(r"^solver\.workflow\.mode$"), "string", None),
)
UNIT_CONVERSIONS = {
    ("degC", "K"): (1.0, 273.15),
    ("K", "degC"): (1.0, -273.15),
    ("bar", "Pa"): (100_000.0, 0.0),
    ("Pa", "bar"): (1.0 / 100_000.0, 0.0),
    ("bar", "MPa"): (0.1, 0.0),
    ("MPa", "bar"): (10.0, 0.0),
    ("fraction", "%"): (100.0, 0.0),
    ("%", "fraction"): (0.01, 0.0),
    ("s", "min"): (1.0 / 60.0, 0.0),
    ("min", "s"): (60.0, 0.0),
    ("s", "h"): (1.0 / 3600.0, 0.0),
    ("h", "s"): (3600.0, 0.0),
    ("s", "day"): (1.0 / 86400.0, 0.0),
    ("day", "s"): (86400.0, 0.0),
}


def validate_study_spec_text(text: str) -> StudySpec:
    """Parse one round-trip YAML document and apply the strict study schema."""
    yaml = YAML(typ="rt")
    yaml.allow_duplicate_keys = False
    value = yaml.load(io.StringIO(text))
    if not isinstance(value, dict):
        raise ValueError("study specification must contain one YAML mapping")
    return StudySpec.model_validate_json(json.dumps(value))


def save_study_spec_text(
    path: str | Path,
    text: str,
    *,
    expected_sha256: str | None = None,
) -> StudySpec:
    """Validate and atomically save exact study-spec text without silent overwrite."""
    target = Path(path).resolve()
    if expected_sha256 is not None:
        if not target.is_file() or sha256_file(target) != expected_sha256:
            raise ValueError("study specification changed outside the workbench")
    specification = validate_study_spec_text(text)
    atomic_write_text(target, text)
    return specification


def generate_study(
    specification_path: str | Path,
    *,
    preflight: Callable[[Path], dict[str, Any]],
) -> Path:
    """Generate immutable cases; every non-rejected case receives full preflight."""
    if preflight is None:
        raise ValueError("authoritative full preflight is required for study generation")
    spec_path = Path(specification_path).resolve()
    # JSON validation preserves strict scalar types while accepting YAML sequences for tuple fields.
    spec = StudySpec.model_validate_json(json.dumps(_load_yaml(spec_path)))
    baseline = _resolve(spec_path.parent, spec.baseline_case_path)
    if sha256_file(baseline) != spec.baseline_case_sha256:
        raise ValueError("baseline_case_sha256 does not match the baseline case bytes")
    baseline_document = _load_yaml(baseline)
    baseline_config = CaseConfig.model_validate(baseline_document)
    _require_outputs(baseline_config, spec.required_outputs)
    _validate_parameter_paths(spec, baseline_document)
    baseline_validation = preflight(baseline)
    baseline_ready, _, baseline_fingerprint = _validation_fields(baseline_validation)
    if not baseline_ready:
        raise ValueError("baseline case failed authoritative full preflight")
    if baseline_fingerprint != spec.baseline_scientific_fingerprint:
        raise ValueError("baseline_scientific_fingerprint does not match current preflight")

    output = _resolve(spec_path.parent, spec.generated_case_directory)
    output.mkdir(parents=True, exist_ok=False)
    records: list[GeneratedSampleRecord] = []
    seen_fingerprints: dict[str, str] = {}
    for index, entered in enumerate(_sample_vectors(spec, spec_path.parent), start=1):
        sample_id = f"sample-{index:06d}"
        try:
            if "__existing_case_path" in entered:
                record = _register_existing(
                    spec,
                    sample_id,
                    Path(str(entered["__existing_case_path"])),
                    preflight,
                    seen_fingerprints,
                )
            else:
                record = _generate_case(
                    spec,
                    sample_id,
                    entered,
                    baseline_document,
                    output,
                    preflight,
                    seen_fingerprints,
                )
        except Exception as error:
            record = _rejected_record(
                spec,
                sample_id,
                entered,
                {},
                (
                    ConstraintOutcome(
                        constraint_id="sample_generation", passed=False, detail=str(error)
                    ),
                ),
            )
        records.append(record)

    records = [_portable_sample_paths(record, output) for record in records]

    generated = [record for record in records if record.generation_outcome == "generated"]
    ready = bool(generated) and all(record.validation_status == "ready" for record in generated)
    now = utc_now()
    manifest = StudyManifest(
        study_manifest_schema_version="1.0",
        study_id=spec.study_id,
        study_name=spec.study_name,
        created_at_utc=now,
        finalised_at_utc=now,
        specification_sha256=sha256_file(spec_path),
        generator_version=STUDY_GENERATOR_VERSION,
        sampling_method=spec.sampling_method,
        seed=spec.seed,
        samples=tuple(records),
        required_outputs=spec.required_outputs,
        validity_domain=spec.validity_domain,
        dataset_exports=(),
        ready=ready,
    )
    manifest_path = output / "study_manifest.json"
    atomic_write_json(manifest_path, manifest)
    return manifest_path


def update_sample_status(
    manifest_path: str | Path,
    sample_id: str,
    *,
    run_id: str | None,
    completion_state: str | None,
    qc_state: str | None,
) -> None:
    """Atomically update the append/finalise study projection."""
    path = Path(manifest_path)
    manifest = StudyManifest.model_validate_json(path.read_bytes())
    samples = []
    found = False
    for sample in manifest.samples:
        if sample.sample_id == sample_id:
            sample = sample.model_copy(
                update={
                    "run_id": run_id,
                    "completion_state": completion_state,
                    "qc_state": qc_state,
                }
            )
            found = True
        samples.append(sample)
    if not found:
        raise KeyError(f"unknown sample_id: {sample_id}")
    atomic_write_json(
        path,
        manifest.model_copy(
            update={"samples": tuple(samples), "finalised_at_utc": utc_now()}
        ),
    )


def _register_existing(spec, sample_id, case_path, preflight, seen):
    try:
        CaseConfig.model_validate(_load_yaml(case_path))
        validation = preflight(case_path)
        ready, receipt_path, fingerprint = _validation_fields(validation)
        if not ready:
            raise ValueError("authoritative preflight blocked the existing case")
    except Exception as error:
        return GeneratedSampleRecord(
            study_id=spec.study_id,
            sample_id=sample_id,
            baseline_case_sha256=spec.baseline_case_sha256,
            input_parameter_vector={},
            canonical_parameter_vector={},
            constraint_outcomes=(
                ConstraintOutcome(
                    constraint_id="existing_case_validation", passed=False, detail=str(error)
                ),
            ),
            generation_outcome="rejected",
            deliberate_replicate=False,
            validation_status="blocked",
        )
    return _validated_record(
        spec,
        sample_id,
        {},
        {},
        (ConstraintOutcome(constraint_id="existing_case_validation", passed=True),),
        case_path,
        fingerprint,
        receipt_path,
        seen,
    )


def _generate_case(spec, sample_id, entered, baseline_document, output, preflight, seen):
    canonical = _canonical_vector(spec.parameters, entered)
    document = copy.deepcopy(baseline_document)
    for parameter in spec.parameters:
        _set_path(document, parameter.yaml_path, canonical[parameter.parameter_id])
    document["case"]["name"] = f"{_slug(spec.study_name)}_{sample_id}"
    document["paths"]["output_dir"] = f"runs/{_slug(spec.study_name)}/{sample_id}/results"
    outcomes = _check_constraints(document, spec)
    canonical = {
        parameter.parameter_id: _get_path(document, parameter.yaml_path)
        for parameter in spec.parameters
    }
    if any(not outcome.passed for outcome in outcomes):
        return _rejected_record(spec, sample_id, entered, canonical, outcomes)
    try:
        CaseConfig.model_validate(document)
    except Exception as error:
        outcomes = (*outcomes, ConstraintOutcome(
            constraint_id="authoritative_case_schema", passed=False, detail=str(error)
        ))
        return _rejected_record(spec, sample_id, entered, canonical, outcomes)
    case_path = output / f"{sample_id}.yaml"
    _dump_yaml(case_path, document)
    validation = preflight(case_path)
    ready, receipt_path, fingerprint = _validation_fields(validation)
    if not ready or fingerprint is None or receipt_path is None:
        return GeneratedSampleRecord(
            study_id=spec.study_id,
            sample_id=sample_id,
            baseline_case_sha256=spec.baseline_case_sha256,
            input_parameter_vector=entered,
            canonical_parameter_vector=canonical,
            constraint_outcomes=outcomes,
            generation_outcome="generated",
            case_path=str(case_path),
            case_sha256=sha256_file(case_path),
            deliberate_replicate=False,
            validation_status="blocked",
            validation_receipt_path=receipt_path,
        )
    return _validated_record(
        spec, sample_id, entered, canonical, outcomes, case_path, fingerprint, receipt_path, seen
    )


def _validated_record(spec, sample_id, entered, canonical, outcomes, case_path, fingerprint, receipt, seen):
    duplicate_of = seen.get(fingerprint)
    deliberate = duplicate_of is not None and spec.execution_policy.allow_replicates
    if duplicate_of is None:
        seen[fingerprint] = sample_id
    return GeneratedSampleRecord(
        study_id=spec.study_id,
        sample_id=sample_id,
        baseline_case_sha256=spec.baseline_case_sha256,
        input_parameter_vector=entered,
        canonical_parameter_vector=canonical,
        constraint_outcomes=outcomes,
        generation_outcome="generated" if duplicate_of is None or deliberate else "duplicate",
        case_path=str(case_path),
        case_sha256=sha256_file(case_path),
        scientific_fingerprint=fingerprint,
        duplicate_of_sample_id=duplicate_of,
        deliberate_replicate=deliberate,
        validation_status="ready",
        validation_receipt_path=receipt,
    )


def _rejected_record(spec, sample_id, entered, canonical, outcomes):
    return GeneratedSampleRecord(
        study_id=spec.study_id,
        sample_id=sample_id,
        baseline_case_sha256=spec.baseline_case_sha256,
        input_parameter_vector=entered,
        canonical_parameter_vector=canonical,
        constraint_outcomes=outcomes,
        generation_outcome="rejected",
        deliberate_replicate=False,
        validation_status="not_checked",
    )


def _validation_fields(result: dict[str, Any]) -> tuple[bool, str | None, str | None]:
    return (
        result.get("ready") is True or result.get("readiness") is True,
        result.get("receipt_path") or result.get("validation_receipt_path"),
        result.get("scientific_fingerprint"),
    )


def _portable_sample_paths(
    record: GeneratedSampleRecord, manifest_directory: Path
) -> GeneratedSampleRecord:
    updates = {}
    for field in ("case_path", "validation_receipt_path"):
        value = getattr(record, field)
        if value:
            updates[field] = Path(
                os.path.relpath(Path(value).resolve(), manifest_directory.resolve())
            ).as_posix()
    return record.model_copy(update=updates) if updates else record


def _validate_parameter_paths(spec: StudySpec, baseline: Any) -> None:
    for parameter in spec.parameters:
        expected_type, fixed_unit = _approved_path(parameter.yaml_path)
        if parameter.data_type != expected_type:
            raise ValueError(
                f"data_type disagrees with approved path {_path_text(parameter.yaml_path)}"
            )
        _get_path(baseline, parameter.yaml_path)
        expected_unit = fixed_unit or _baseline_unit(baseline, parameter.yaml_path)
        if expected_unit is not None and parameter.canonical_unit != expected_unit:
            raise ValueError(
                f"canonical unit for {_path_text(parameter.yaml_path)} must be {expected_unit}"
            )
        if expected_unit is None and parameter.canonical_unit is not None:
            raise ValueError(f"{_path_text(parameter.yaml_path)} is unitless")
        if parameter.entered_unit is not None:
            _conversion(1.0, parameter.entered_unit, parameter.canonical_unit)


def _sample_vectors(spec: StudySpec, base: Path) -> list[dict[str, Any]]:
    parameters = spec.parameters
    if spec.sampling_method == "grid":
        axes = [_parameter_values(parameter) for parameter in parameters]
        vectors = [
            dict(zip((parameter.parameter_id for parameter in parameters), values))
            for values in itertools.product(*axes)
        ]
    elif spec.sampling_method == "imported_matrix":
        source = _resolve(base, str(spec.imported_matrix_path))
        if sha256_file(source) != spec.imported_matrix_sha256:
            raise ValueError("imported_matrix_sha256 does not match the sampling matrix bytes")
        mappings = {item.parameter_id: item.column_name for item in spec.imported_column_mapping or ()}
        with source.open(newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream)
            missing = sorted(set(mappings.values()) - set(reader.fieldnames or ()))
            if missing:
                raise ValueError(f"imported matrix columns missing: {missing}")
            vectors = [
                {parameter.parameter_id: row[mappings[parameter.parameter_id]] for parameter in parameters}
                for row in reader
            ]
    elif spec.sampling_method == "existing_cases":
        vectors = [
            {"__existing_case_path": str(_resolve(base, path))}
            for path in spec.existing_case_paths or ()
        ]
    else:
        rng = random.Random(spec.seed)
        vectors = [dict() for _ in range(spec.sample_count)]
        for parameter in parameters:
            values = (
                _latin_values(parameter, spec.sample_count, rng)
                if spec.sampling_method == "latin_hypercube"
                else [_draw(parameter, rng) for _ in range(spec.sample_count)]
            )
            for vector, value in zip(vectors, values):
                vector[parameter.parameter_id] = value
    if len(vectors) != spec.sample_count:
        raise ValueError(
            f"sampling produced {len(vectors)} rows but sample_count is {spec.sample_count}"
        )
    return vectors


def _parameter_values(parameter: ParameterDefinition) -> list[Any]:
    values = parameter.values or parameter.categories
    if not values:
        raise ValueError(f"grid parameter {parameter.parameter_id} requires values/categories")
    return list(values)


def _draw(parameter: ParameterDefinition, rng: random.Random) -> Any:
    choices = parameter.categories
    if choices:
        return rng.choice(choices)
    low, high = _numeric_range(parameter)
    if parameter.data_type == "integer":
        return rng.randint(math.ceil(low), math.floor(high))
    if parameter.sampling_distribution == "uniform":
        return rng.uniform(low, high)
    if parameter.sampling_distribution == "log_uniform" and low > 0:
        return math.exp(rng.uniform(math.log(low), math.log(high)))
    raise ValueError(f"unsupported sampling distribution: {parameter.sampling_distribution}")


def _latin_values(parameter: ParameterDefinition, count: int, rng: random.Random) -> list[Any]:
    if parameter.categories:
        values = [parameter.categories[index % len(parameter.categories)] for index in range(count)]
        rng.shuffle(values)
        return values
    low, high = _numeric_range(parameter)
    fractions = [(index + rng.random()) / count for index in range(count)]
    rng.shuffle(fractions)
    if parameter.sampling_distribution == "uniform":
        values = [low + fraction * (high - low) for fraction in fractions]
    elif parameter.sampling_distribution == "log_uniform" and low > 0:
        values = [
            math.exp(math.log(low) + fraction * (math.log(high) - math.log(low)))
            for fraction in fractions
        ]
    else:
        raise ValueError("latin_hypercube requires uniform or positive log_uniform ranges")
    return [int(round(value)) for value in values] if parameter.data_type == "integer" else values


def _numeric_range(parameter: ParameterDefinition) -> tuple[float, float]:
    if parameter.range is None:
        raise ValueError(f"parameter {parameter.parameter_id} requires a numeric range")
    return parameter.range.minimum, parameter.range.maximum


def _canonical_vector(parameters, entered):
    result = {}
    for parameter in parameters:
        value = entered[parameter.parameter_id]
        if parameter.transform != "identity":
            raise ValueError("parameter transforms must be explicit identity; distributions own sampling")
        if parameter.data_type in {"number", "integer"}:
            value = _conversion(float(value), parameter.entered_unit, parameter.canonical_unit)
            value = int(value) if parameter.data_type == "integer" and float(value).is_integer() else value
        elif parameter.data_type == "boolean":
            if isinstance(value, str):
                if value.lower() not in {"true", "false"}:
                    raise ValueError(f"illegal boolean for {parameter.parameter_id}: {value}")
                value = value.lower() == "true"
            else:
                value = bool(value)
        else:
            value = str(value)
        allowed = parameter.categories or parameter.values or parameter.imported_values
        if allowed is not None and value not in allowed:
            raise ValueError(f"illegal value for {parameter.parameter_id}: {value}")
        result[parameter.parameter_id] = value
    return result


def _check_constraints(document: Any, spec: StudySpec) -> tuple[ConstraintOutcome, ...]:
    parameters = {parameter.parameter_id: parameter for parameter in spec.parameters}
    outcomes = []
    for constraint in (*spec.constraint_groups, *spec.cross_parameter_constraints):
        try:
            passed, detail = _evaluate_constraint(document, constraint, parameters)
        except (KeyError, IndexError, TypeError, ValueError) as error:
            passed, detail = False, str(error)
        outcomes.append(
            ConstraintOutcome(
                constraint_id=constraint.constraint_id,
                passed=passed,
                detail=None if passed else detail,
            )
        )
    return tuple(outcomes)


def _evaluate_constraint(document, constraint: ConstraintDefinition, parameters):
    settings = constraint.settings
    values = {
        parameter_id: _get_path(document, parameters[parameter_id].yaml_path)
        for parameter_id in constraint.parameter_ids
    }
    kind = constraint.constraint_type
    if kind == "bounds":
        minimum, maximum = settings.get("minimum"), settings.get("maximum")
        passed = all(
            (minimum is None or float(value) >= float(minimum))
            and (maximum is None or float(value) <= float(maximum))
            for value in values.values()
        )
        return passed, f"values={values}, minimum={minimum}, maximum={maximum}"
    if kind == "categorical_legality":
        allowed = settings.get("allowed")
        if not isinstance(allowed, list):
            raise ValueError("categorical_legality requires settings.allowed")
        return all(value in allowed for value in values.values()), f"values={values}, allowed={allowed}"
    if kind in {"dependency", "conditional_field"}:
        active = values[str(settings["if_parameter"])] == settings.get("equals")
        required = settings.get("require_parameters", [])
        forbidden = settings.get("forbid_parameters", [])
        passed = not active or (
            all(_path_exists(document, parameters[item].yaml_path) for item in required)
            and all(not _path_exists(document, parameters[item].yaml_path) for item in forbidden)
        )
        return passed, f"active={active}, required={required}, forbidden={forbidden}"
    if kind == "temperature_pressure_domain":
        temperature = float(_get_path(document, ("physical", "temperature_c")))
        pressure = float(_get_path(document, ("physical", "pressure_bar")))
        passed = (
            float(settings["temperature_c_min"]) <= temperature <= float(settings["temperature_c_max"])
            and float(settings["pressure_bar_min"]) <= pressure <= float(settings["pressure_bar_max"])
        )
        return passed, f"temperature_c={temperature}, pressure_bar={pressure}"
    if kind == "co2_workflow_consistency":
        try:
            CaseConfig.model_validate(document)
        except Exception as error:
            return False, str(error)
        return True, "authoritative case schema accepted CO2/workflow combination"
    if kind == "kinetic_surface_area":
        missing = [
            mineral.get("name", "<unnamed>")
            for mineral in document.get("minerals", [])
            if mineral.get("role") == "kinetic" and not mineral.get("surface_area")
        ]
        return not missing, f"kinetic minerals missing surface area={missing}"
    if kind == "correlation":
        left, right = (float(values[item]) for item in constraint.parameter_ids[:2])
        expected = float(settings["slope"]) * left + float(settings.get("intercept", 0.0))
        tolerance = float(settings.get("tolerance", 0.0))
        return math.isclose(right, expected, abs_tol=tolerance, rel_tol=0.0), f"right={right}, expected={expected}"
    if kind in {"composition_closure", "group_total"}:
        numeric = [float(value) for value in values.values()]
        total = sum(numeric)
        if kind == "group_total":
            passed = float(settings["minimum"]) <= total <= float(settings["maximum"])
            return passed, f"total={total}"
        target = float(settings["closure_total"])
        tolerance = float(settings.get("tolerance", 0.0))
        if math.isclose(total, target, abs_tol=tolerance, rel_tol=0.0):
            return True, f"sum={total}, target={target}"
        policy = settings.get("repair_policy", "reject")
        if policy == "normalize" and settings.get("scientifically_approved") is True and total != 0:
            factor = target / total
            for parameter_id, value in values.items():
                _set_path(document, parameters[parameter_id].yaml_path, float(value) * factor)
            return True, f"approved deterministic normalization factor={factor}"
        if policy not in {"reject", "normalize"}:
            raise ValueError("composition repair_policy must be reject or normalize")
        return False, f"sum={total}, target={target}, policy={policy}"
    raise ValueError(f"unsupported constraint type: {kind}")


def _approved_path(path: tuple[str | int, ...]) -> tuple[str, str | None]:
    text = _path_text(path)
    for pattern, data_type, unit in APPROVED_PATHS:
        if pattern.fullmatch(text):
            return data_type, unit
    raise ValueError(f"YAML path is not approved for parameter studies: {text}")


def _require_outputs(config: CaseConfig, required: tuple[str, ...]) -> None:
    output = config.outputs
    fixed = {
        "manifest.json": output.manifest.enabled,
        "diagnostics.json": output.diagnostics.enabled,
        "timeseries.csv": output.timeseries.enabled,
        "solver_history.csv": output.solver_history.enabled,
    }
    summaries = {
        f"{name}.csv": bool(value)
        for name, value in output.summaries.model_dump(mode="python").items()
    }
    plots = {
        "plots/pH_vs_time.png": output.plots.enabled and output.plots.pH,
        "plots/mineral_change_vs_time.png": output.plots.enabled and output.plots.mineral_change,
        "plots/saturation_index_vs_time.png": output.plots.enabled and output.plots.saturation_index,
        "plots/solver_dt_vs_time.png": output.plots.enabled and output.plots.solver_dt,
        "plots/solver_iterations_vs_time.png": output.plots.enabled and output.plots.solver_iterations,
    }
    enabled = {**fixed, **summaries, **plots}
    unknown = sorted(set(required) - set(enabled))
    disabled = sorted(name for name in required if name in enabled and not enabled[name])
    if unknown or disabled:
        raise ValueError(
            f"required_outputs are not guaranteed by the baseline; unknown={unknown}, disabled={disabled}"
        )


def _baseline_unit(document: Any, path: tuple[str | int, ...]) -> str | None:
    parent = path[:-1]
    leaf = str(path[-1])
    if leaf != "value":
        if leaf == "duration_value":
            return str(_get_path(document, (*parent, "duration_unit")))
        return None
    return str(_get_path(document, (*parent, "unit")))


def _conversion(value: float, entered: str | None, canonical: str | None) -> float:
    if entered == canonical or entered is canonical is None:
        return value
    try:
        scale, offset = UNIT_CONVERSIONS[(str(entered), str(canonical))]
    except KeyError as error:
        raise ValueError(f"unsupported unit conversion: {entered} -> {canonical}") from error
    return value * scale + offset


def _get_path(document: Any, path: tuple[str | int, ...]) -> Any:
    current = document
    for token in path:
        current = current[token]
    return current


def _set_path(document: Any, path: tuple[str | int, ...], value: Any) -> None:
    current = document
    for token in path[:-1]:
        current = current[token]
    current[path[-1]] = value


def _path_exists(document: Any, path: tuple[str | int, ...]) -> bool:
    try:
        _get_path(document, path)
    except (KeyError, IndexError, TypeError):
        return False
    return True


def _load_yaml(path: Path) -> dict[str, Any]:
    yaml = YAML(typ="rt")
    yaml.allow_duplicate_keys = False
    with path.open(encoding="utf-8") as stream:
        value = yaml.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"YAML document must contain a mapping: {path}")
    return value


def _dump_yaml(path: Path, document: Any) -> None:
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        yaml.dump(document, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _resolve(base: Path, path: str | Path) -> Path:
    candidate = Path(path)
    return (candidate if candidate.is_absolute() else base / candidate).resolve()


def _path_text(path: tuple[str | int, ...]) -> str:
    return ".".join(str(part) for part in path)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "study"

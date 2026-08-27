"""Generate immutable, traceable DoE design packages for the batch runner."""

from __future__ import annotations

import csv
import json
import os
import shutil
import tempfile
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import yaml

from batch_runner.config import load_case
from batch_runner.config._base import DEFAULT_KINETIC_PATHS, PROJECT_ROOT
from batch_runner.simulator import preflight_case

from .constraints import evaluate_constraints
from .identity import (
    canonical_json_bytes,
    database_identity,
    design_point_fingerprint_v1,
    design_spec_hash_v1,
    file_sha256,
    kinetics_identity,
)
from .models import ExistingCasesDesignSpec, GeneratedDesignSpec, load_design_spec, verify_sha256
from .sampling import ResolvedParameter, fixed_design_vectors, random_vectors
from .targets import (
    KINETIC_KINDS,
    TARGET_REGISTRY_VERSION,
    ResolvedTarget,
    canonicalize_sampling,
    from_canonical,
    materialise_candidate,
    resolve_target,
)

DESIGN_MANIFEST_SCHEMA_VERSION = "1.0"
RESOLVED_SPEC_SCHEMA_VERSION = "1.0"
CONSTRAINT_SCHEMA_VERSION = "1.0"


def _source_path(spec_path: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (spec_path.parent / path).resolve()


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"YAML source must contain a mapping: {path}")
    return raw


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload) + b"\n")


def _copy_content_addressed(
    source: Path, package_root: Path, category: str
) -> tuple[str, str]:
    digest = file_sha256(source)
    suffix = source.suffix or ".bin"
    relative = Path("dependencies") / category / f"{digest}{suffix.lower()}"
    destination = package_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        shutil.copyfile(source, destination)
    if file_sha256(destination) != digest:
        raise RuntimeError(f"packaged dependency hash mismatch: {destination}")
    return relative.as_posix(), digest


def _dependency_record(case: Any, package_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    db_identity = database_identity(case)
    if db_identity["source"] == "local":
        assert case.database_path is not None
        db_path, _ = _copy_content_addressed(case.database_path, package_root, "database")
        db = {"identity": db_identity, "package_path": db_path}
    else:
        db = {"identity": db_identity, "package_path": None}

    kin_identity = kinetics_identity(case)
    if kin_identity.get("enabled"):
        assert case.kinetics_path is not None
        kin_path, _ = _copy_content_addressed(
            case.kinetics_path, package_root, "kinetics/baseline"
        )
        kin = {"identity": kin_identity, "package_path": kin_path}
    else:
        kin = {"identity": kin_identity, "package_path": None}
    return db, kin


def _raw_dependency_records(
    raw: dict[str, Any], package_root: Path
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Best-effort dependency identity for a case that may fail full CaseConfig loading."""
    db_record: dict[str, Any] | None = None
    database = raw.get("database")
    if isinstance(database, dict):
        if database.get("source") == "embedded" and database.get("name"):
            db_record = {
                "identity": {"source": "embedded", "name": str(database["name"])},
                "package_path": None,
            }
        elif database.get("source") == "local" and database.get("path"):
            source = Path(str(database["path"]))
            source = source.resolve() if source.is_absolute() else (PROJECT_ROOT / source).resolve()
            if source.is_file():
                relative, digest = _copy_content_addressed(source, package_root, "database")
                db_record = {
                    "identity": {"source": "local", "sha256": digest},
                    "package_path": relative,
                }

    kin_record: dict[str, Any] | None = None
    kinetics = raw.get("kinetics")
    if isinstance(kinetics, dict) and kinetics.get("enabled") is False:
        kin_record = {"identity": {"enabled": False}, "package_path": None}
    elif isinstance(kinetics, dict) and kinetics.get("enabled") is True:
        model = str(kinetics.get("model") or "palandri_kharaka")
        path_value = kinetics.get("path") or DEFAULT_KINETIC_PATHS.get(model)
        if path_value:
            source = Path(str(path_value))
            source = source.resolve() if source.is_absolute() else (PROJECT_ROOT / source).resolve()
            if source.is_file():
                relative, digest = _copy_content_addressed(
                    source, package_root, "kinetics/baseline"
                )
                kin_record = {
                    "identity": {"enabled": True, "model": model, "sha256": digest},
                    "package_path": relative,
                }
    return db_record, kin_record


def _dependency_key(record: dict[str, Any]) -> bytes:
    return canonical_json_bytes(record["identity"])


def _dedupe_dependencies(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[bytes, dict[str, Any]] = {}
    for record in records:
        unique.setdefault(_dependency_key(record), record)
    return [unique[key] for key in sorted(unique)]


def _rewrite_dependency_locators(
    raw: dict[str, Any], database_record: dict[str, Any], kinetics_record: dict[str, Any]
) -> None:
    if raw["database"]["source"] == "local":
        raw["database"]["path"] = database_record["package_path"]
    if raw["kinetics"]["enabled"]:
        raw["kinetics"]["path"] = kinetics_record["package_path"]


def _sampler_metadata(spec: GeneratedDesignSpec) -> dict[str, Any]:
    sampler = spec.sampler
    data: dict[str, Any] = {
        "kind": sampler.kind,
        "parameter_order": [item.parameter_id for item in spec.parameters],
        "numpy_version": np.__version__,
    }
    if hasattr(sampler, "seed"):
        data["seed"] = sampler.seed
    if hasattr(sampler, "sample_count"):
        data["sample_count"] = sampler.sample_count
    if sampler.kind == "random":
        data.update(
            rng="numpy.random.Generator",
            bit_generator="numpy.random.PCG64",
            max_candidates=sampler.max_candidates,
        )
    elif sampler.kind == "latin_hypercube":
        import scipy
        data.update(
            scipy_version=scipy.__version__,
            rng="numpy.random.Generator",
            bit_generator="numpy.random.PCG64",
            options={"scramble": True, "strength": 1, "optimization": None},
        )
    elif sampler.kind == "sobol":
        import scipy
        data.update(
            scipy_version=scipy.__version__,
            rng="numpy.random.Generator",
            bit_generator="numpy.random.PCG64",
            options={
                "scramble": True,
                "bits": 64,
                "optimization": None,
                "draw_method": "random_base2",
            },
        )
    elif sampler.kind == "grid":
        data["options"] = {"cartesian_order": "first_parameter_slowest"}
    return data


def _kinetics_raw(case: Any) -> dict[str, Any] | None:
    if not case.config.kinetics.enabled:
        return None
    assert case.kinetics_path is not None
    return _load_yaml_mapping(case.kinetics_path)


def _year_days(base_raw: dict[str, Any]) -> float | None:
    return base_raw["solver"]["timestep"]["time"].get("year_definition_days")


def _resolve_parameters(
    spec: GeneratedDesignSpec,
    base_raw: dict[str, Any],
    kinetics_raw: dict[str, Any] | None,
) -> tuple[list[ResolvedTarget], list[ResolvedParameter], list[dict[str, Any]]]:
    targets: list[ResolvedTarget] = []
    parameters: list[ResolvedParameter] = []
    records: list[dict[str, Any]] = []
    year_days = _year_days(base_raw)
    for parameter in spec.parameters:
        resolved = resolve_target(parameter.target, base_raw, kinetics_raw)
        canonical_sampling = canonicalize_sampling(
            parameter, resolved, year_days=year_days
        )
        targets.append(resolved)
        parameters.append(
            ResolvedParameter(
                parameter_id=parameter.parameter_id,
                data_type=resolved.data_type,
                canonical_unit=resolved.canonical_unit,
                sampling=canonical_sampling,
                entered_unit=parameter.sampling.entered_unit,
                year_days=year_days,
            )
        )
        records.append(
            {
                "parameter_id": parameter.parameter_id,
                "target": parameter.target.model_dump(mode="json"),
                "classification": resolved.classification,
                "data_type": resolved.data_type,
                "canonical_unit": resolved.canonical_unit,
                "sampling": canonical_sampling,
                "provenance": parameter.provenance.model_dump(mode="json"),
            }
        )
    return targets, parameters, records


def _validate_sampler_parameter_contract(
    spec: GeneratedDesignSpec, parameters: list[ResolvedParameter]
) -> None:
    allowed_by_sampler = {
        "grid": {"explicit_values"},
        "random": {"uniform", "log_uniform", "discrete_uniform"},
        "latin_hypercube": {"uniform", "log_uniform"},
        "sobol": {"uniform", "log_uniform"},
        "imported_matrix": {"imported_column"},
    }
    allowed = allowed_by_sampler[spec.sampler.kind]
    for parameter in parameters:
        kind = parameter.sampling["kind"]
        if kind not in allowed:
            raise ValueError(
                f"sampler {spec.sampler.kind} does not allow {kind} for "
                f"parameter {parameter.parameter_id}"
            )
        if spec.sampler.kind in {"latin_hypercube", "sobol"} and parameter.data_type != "float":
            raise ValueError(
                f"sampler {spec.sampler.kind} requires continuous float parameters"
            )


def _vector_records(
    spec: GeneratedDesignSpec,
    parameters: list[ResolvedParameter],
    values: list[float | int],
    *,
    year_days: float | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entered: list[dict[str, Any]] = []
    canonical: list[dict[str, Any]] = []
    for source, resolved, value in zip(spec.parameters, parameters, values):
        entered_unit = source.sampling.entered_unit
        entered.append(
            {
                "parameter_id": resolved.parameter_id,
                "value": from_canonical(
                    value,
                    resolved.canonical_unit,
                    entered_unit,
                    year_days=year_days,
                ),
                "unit": entered_unit,
            }
        )
        canonical.append(
            {
                "parameter_id": resolved.parameter_id,
                "value": value,
                "unit": resolved.canonical_unit,
            }
        )
    return entered, canonical


def _kinetic_expected_paths(spec: GeneratedDesignSpec) -> set[tuple[str, ...]]:
    expected: set[tuple[str, ...]] = set()
    for parameter in spec.parameters:
        target = parameter.target
        if target.kind not in KINETIC_KINDS:
            continue
        if target.kind.startswith("pk_"):
            key = {
                "pk_lgk": "lgk",
                "pk_activation_energy": "E",
                "pk_p": "p",
                "pk_q": "q",
            }.get(target.kind, target.catalyst_property)
            expected.add(
                (
                    "ReactionRateModelParams",
                    "PalandriKharaka",
                    str(target.record),
                    "Mechanisms",
                    str(target.mechanism),
                    str(key),
                )
            )
        elif target.kind == "kinec_sigma":
            expected.add((str(target.mineral), "sigma"))
        else:
            expected.add(
                (
                    str(target.mineral),
                    "terms",
                    str(target.term),
                    target.kind.split("kinec_", 1)[1],
                )
            )
    return expected


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


def _write_candidate_kinetics(
    package_root: Path,
    candidate_id: str,
    baseline_raw: dict[str, Any],
    generated_raw: dict[str, Any],
    expected_paths: set[tuple[str, ...]],
) -> tuple[str, str]:
    actual = _diff_paths(baseline_raw, generated_raw)
    if not actual.issubset(expected_paths):
        unexpected = sorted(".".join(path) for path in actual - expected_paths)
        raise RuntimeError(f"generated kinetics changed undeclared fields: {unexpected}")
    relative = Path("dependencies") / "kinetics" / "generated" / f"{candidate_id}.yaml"
    destination = package_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(yaml.safe_dump(generated_raw, sort_keys=False), encoding="utf-8")
    return relative.as_posix(), file_sha256(destination)


def _write_case(
    package_root: Path, candidate_id: str, raw: dict[str, Any]
) -> tuple[Path, str, str]:
    relative = Path("cases") / f"{candidate_id}.yaml"
    path = package_root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path, relative.as_posix(), file_sha256(path)


def _base_candidate_record(
    candidate_id: str,
    entered: list[dict[str, Any]],
    canonical: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "sample_id": None,
        "outcome": None,
        "entered_parameter_vector": entered,
        "canonical_parameter_vector": canonical,
        "constraint_outcomes": outcomes,
        "case_path": None,
        "case_sha256": None,
        "kinetics_path": None,
        "kinetics_sha256": None,
        "design_point_fingerprint_v1": None,
        "duplicate_of_sample_id": None,
        "preflight_result": None,
        "error": None,
    }


def _materialise_generated_candidate(
    package_root: Path,
    spec: GeneratedDesignSpec,
    base_raw: dict[str, Any],
    baseline_kinetics_raw: dict[str, Any] | None,
    db_record: dict[str, Any],
    kin_record: dict[str, Any],
    resolved_targets: list[ResolvedTarget],
    values: list[float | int],
    candidate_id: str,
) -> tuple[Path, str, str, str | None, str | None]:
    case_raw, generated_kinetics = materialise_candidate(
        base_raw,
        list(zip(resolved_targets, values)),
        baseline_kinetics_raw,
    )
    _rewrite_dependency_locators(case_raw, db_record, kin_record)
    kinetics_path = None
    kinetics_sha = None
    if generated_kinetics is not None and any(
        parameter.target.kind in KINETIC_KINDS for parameter in spec.parameters
    ):
        assert baseline_kinetics_raw is not None
        kinetics_path, kinetics_sha = _write_candidate_kinetics(
            package_root,
            candidate_id,
            baseline_kinetics_raw,
            generated_kinetics,
            _kinetic_expected_paths(spec),
        )
        case_raw["kinetics"]["path"] = kinetics_path
    path, relative, sha = _write_case(package_root, candidate_id, case_raw)
    return path, relative, sha, kinetics_path, kinetics_sha


def _process_generated(
    package_root: Path,
    spec_path: Path,
    spec: GeneratedDesignSpec,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    base_source = _source_path(spec_path, spec.base_case.path)
    verify_sha256(base_source, spec.base_case.sha256)
    base_snapshot = package_root / "base_case.snapshot.yaml"
    shutil.copyfile(base_source, base_snapshot)
    base_raw = _load_yaml_mapping(base_source)

    with tempfile.TemporaryDirectory(prefix="doe-base-") as temp_dir:
        base_case = load_case(
            base_source, output_dir_override=Path(temp_dir) / "results"
        )
        base_report = preflight_case(base_case)
    if not base_report["ready"]:
        raise ValueError(
            f"base case preflight failed at {base_report['failed_stage']}: "
            f"{base_report['error_message']}"
        )

    db_record, kin_record = _dependency_record(base_case, package_root)
    baseline_kinetics_raw = _kinetics_raw(base_case)
    resolved_targets, parameters, parameter_records = _resolve_parameters(
        spec, base_raw, baseline_kinetics_raw
    )
    _validate_sampler_parameter_contract(spec, parameters)

    matrix_snapshot: dict[str, Any] | None = None
    imported_matrix_path: Path | None = None
    if spec.sampler.kind == "imported_matrix":
        source = _source_path(spec_path, spec.sampler.path)
        verify_sha256(source, spec.sampler.sha256)
        relative = Path("sources") / "imported_matrix" / source.name
        destination = package_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        matrix_snapshot = {
            "package_path": relative.as_posix(),
            "sha256": file_sha256(destination),
        }
        imported_matrix_path = destination

    resolved_spec = {
        "resolved_spec_schema_version": RESOLVED_SPEC_SCHEMA_VERSION,
        "target_registry_version": TARGET_REGISTRY_VERSION,
        "constraint_schema_version": CONSTRAINT_SCHEMA_VERSION,
        "mode": "generated",
        "name": spec.name,
        "parameter_order": [item.parameter_id for item in spec.parameters],
        "parameters": parameter_records,
        "constraints": [item.model_dump(mode="json") for item in spec.constraints],
        "sampler": _sampler_metadata(spec),
        "base_identity": {
            "case_snapshot_sha256": file_sha256(base_snapshot),
            "database": db_record["identity"],
            "kinetics": kin_record["identity"],
        },
        "imported_matrix": matrix_snapshot,
    }
    spec_hash = design_spec_hash_v1(resolved_spec)
    design_id = f"doe-{spec_hash}"

    ledger: list[dict[str, Any]] = []
    accepted_by_fingerprint: dict[str, str] = {}
    accepted_count = 0
    exclusions = 0
    generation_failed = False
    year_days = _year_days(base_raw)

    if spec.sampler.kind == "random":
        vectors = random_vectors(
            parameters,
            seed=spec.sampler.seed,
            max_candidates=spec.sampler.max_candidates,
        )
    else:
        vectors = fixed_design_vectors(
            spec, parameters, imported_matrix_path=imported_matrix_path
        )

    for candidate_number, values in enumerate(vectors, start=1):
        if spec.sampler.kind == "random" and accepted_count >= spec.sampler.sample_count:
            break
        candidate_id = f"candidate-{candidate_number:06d}"
        values_list = list(values)
        entered, canonical = _vector_records(
            spec, parameters, values_list, year_days=year_days
        )
        try:
            for resolved_target, value in zip(resolved_targets, values_list):
                resolved_target.validate_value(value)
        except Exception as error:
            record = _base_candidate_record(candidate_id, entered, canonical, [])
            record["outcome"] = "schema_blocked"
            record["error"] = {
                "stage": "target_validation",
                "type": type(error).__name__,
                "message": str(error),
            }
            exclusions += 1
            ledger.append(record)
            continue

        vector = {
            parameter.parameter_id: value
            for parameter, value in zip(parameters, values_list)
        }
        outcomes = evaluate_constraints(
            spec.constraints, vector, parameters, year_days=year_days
        )
        record = _base_candidate_record(
            candidate_id, entered, canonical, [asdict(item) for item in outcomes]
        )
        if not all(item.passed for item in outcomes):
            record["outcome"] = "constraint_rejected"
            exclusions += 1
            ledger.append(record)
            continue

        try:
            case_path, relative, case_sha, generated_kinetics_path, generated_kinetics_sha = (
                _materialise_generated_candidate(
                    package_root,
                    spec,
                    base_raw,
                    baseline_kinetics_raw,
                    db_record,
                    kin_record,
                    resolved_targets,
                    values_list,
                    candidate_id,
                )
            )
            record.update(
                case_path=relative,
                case_sha256=case_sha,
                kinetics_path=generated_kinetics_path,
                kinetics_sha256=generated_kinetics_sha,
            )
        except Exception as error:
            record["outcome"] = "generation_error"
            record["error"] = {
                "stage": "materialisation",
                "type": type(error).__name__,
                "message": str(error),
            }
            ledger.append(record)
            generation_failed = True
            break

        try:
            with tempfile.TemporaryDirectory(prefix="doe-schema-") as temp_dir:
                resolved_case = load_case(
                    case_path,
                    output_dir_override=Path(temp_dir) / "results",
                    artifact_root=package_root,
                )
        except Exception as error:
            record["outcome"] = "schema_blocked"
            record["error"] = {
                "stage": "configuration_validation",
                "type": type(error).__name__,
                "message": str(error),
            }
            exclusions += 1
            ledger.append(record)
            continue

        fingerprint = design_point_fingerprint_v1(resolved_case)
        record["design_point_fingerprint_v1"] = fingerprint
        if fingerprint in accepted_by_fingerprint:
            record["outcome"] = "duplicate"
            record["duplicate_of_sample_id"] = accepted_by_fingerprint[fingerprint]
            exclusions += 1
            ledger.append(record)
            continue

        report = preflight_case(resolved_case)
        preflight_relative = Path("preflight") / f"{candidate_id}.json"
        _write_json(package_root / preflight_relative, report)
        record["preflight_result"] = {
            "path": preflight_relative.as_posix(),
            "ready": bool(report["ready"]),
            "failed_stage": report["failed_stage"],
        }
        if not report["ready"]:
            record["outcome"] = "preflight_blocked"
            record["error"] = {
                "stage": report["failed_stage"],
                "type": report["exception_type"],
                "message": report["error_message"],
            }
            exclusions += 1
            ledger.append(record)
            continue

        accepted_count += 1
        sample_id = f"sample-{accepted_count:06d}"
        record["sample_id"] = sample_id
        record["outcome"] = "accepted"
        accepted_by_fingerprint[fingerprint] = sample_id
        ledger.append(record)

    if generation_failed:
        status = "generation_failed"
    elif spec.sampler.kind == "random":
        status = "ready" if accepted_count == spec.sampler.sample_count else "incomplete"
    elif spec.sampler.kind in {"latin_hypercube", "sobol"}:
        status = "ready" if exclusions == 0 and accepted_count > 0 else "blocked"
    elif accepted_count == 0:
        status = "blocked"
    elif exclusions:
        status = "ready_with_exclusions"
    else:
        status = "ready"

    generated_kinetics_records = [
        {
            "identity": {
                "enabled": True,
                "model": base_case.config.kinetics.model,
                "sha256": record["kinetics_sha256"],
            },
            "package_path": record["kinetics_path"],
        }
        for record in ledger
        if record.get("kinetics_path") and record.get("kinetics_sha256")
    ]
    extra = {
        "base_case": {
            "package_path": "base_case.snapshot.yaml",
            "sha256": file_sha256(base_snapshot),
        },
        "imported_matrix": matrix_snapshot,
        "dependencies": {
            "databases": _dedupe_dependencies([db_record]),
            "kinetics": _dedupe_dependencies([kin_record, *generated_kinetics_records]),
        },
        "base_preflight": {
            "ready": True,
            "database_sha256": base_report["database_sha256"],
            "kinetic_parameter_sha256": base_report["kinetic_parameter_sha256"],
        },
    }
    return (
        {
            "design_id": design_id,
            "design_spec_hash_v1": spec_hash,
            "generation_status": status,
            "resolved_spec": resolved_spec,
        },
        ledger,
        extra,
    )


def _process_existing(
    package_root: Path,
    spec_path: Path,
    spec: ExistingCasesDesignSpec,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    db_records: list[dict[str, Any]] = []
    kin_records: list[dict[str, Any]] = []
    identities: list[dict[str, Any]] = []

    for item in spec.cases:
        source = _source_path(spec_path, item.path)
        verify_sha256(source, item.sha256)
        source_relative = Path("cases") / "source" / f"{item.case_id}.yaml"
        snapshot = package_root / source_relative
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, snapshot)

        raw: dict[str, Any] | None = None
        db_record: dict[str, Any] | None = None
        kin_record: dict[str, Any] | None = None
        initial_error: Exception | None = None
        try:
            raw = _load_yaml_mapping(source)
            db_record, kin_record = _raw_dependency_records(raw, package_root)
            with tempfile.TemporaryDirectory(prefix="doe-existing-") as temp_dir:
                resolved = load_case(
                    source, output_dir_override=Path(temp_dir) / "results"
                )
            db_record, kin_record = _dependency_record(resolved, package_root)
        except Exception as error:
            initial_error = error

        if db_record is not None:
            db_records.append(db_record)
        if kin_record is not None:
            kin_records.append(kin_record)
        identities.append(
            {
                "case_id": item.case_id,
                "case_snapshot_sha256": file_sha256(snapshot),
                "database_identity": (
                    db_record["identity"] if db_record is not None else None
                ),
                "kinetics_identity": (
                    kin_record["identity"] if kin_record is not None else None
                ),
                "provenance": item.provenance.model_dump(mode="json"),
            }
        )
        prepared.append(
            {
                "item": item,
                "raw": raw,
                "source_snapshot": source_relative.as_posix(),
                "db": db_record,
                "kin": kin_record,
                "initial_error": initial_error,
            }
        )

    resolved_spec = {
        "resolved_spec_schema_version": RESOLVED_SPEC_SCHEMA_VERSION,
        "target_registry_version": TARGET_REGISTRY_VERSION,
        "constraint_schema_version": CONSTRAINT_SCHEMA_VERSION,
        "mode": "existing_cases",
        "name": spec.name,
        "existing_cases": identities,
    }
    spec_hash = design_spec_hash_v1(resolved_spec)
    design_id = f"doe-{spec_hash}"

    ledger: list[dict[str, Any]] = []
    accepted_by_fingerprint: dict[str, str] = {}
    accepted_count = 0
    exclusions = 0
    for number, prepared_case in enumerate(prepared, start=1):
        candidate_id = f"candidate-{number:06d}"
        record = _base_candidate_record(candidate_id, [], [], [])
        initial_error = prepared_case["initial_error"]
        if initial_error is not None:
            record["case_path"] = prepared_case["source_snapshot"]
            record["case_sha256"] = file_sha256(
                package_root / prepared_case["source_snapshot"]
            )
            record["outcome"] = "schema_blocked"
            record["error"] = {
                "stage": "configuration_validation",
                "type": type(initial_error).__name__,
                "message": str(initial_error),
            }
            exclusions += 1
            ledger.append(record)
            continue

        raw = deepcopy(prepared_case["raw"])
        db_record = prepared_case["db"]
        kin_record = prepared_case["kin"]
        assert isinstance(raw, dict)
        assert isinstance(db_record, dict)
        assert isinstance(kin_record, dict)
        _rewrite_dependency_locators(raw, db_record, kin_record)
        path, relative, sha = _write_case(package_root, candidate_id, raw)
        record["case_path"] = relative
        record["case_sha256"] = sha
        try:
            with tempfile.TemporaryDirectory(prefix="doe-existing-schema-") as temp_dir:
                resolved = load_case(
                    path,
                    output_dir_override=Path(temp_dir) / "results",
                    artifact_root=package_root,
                )
        except Exception as error:
            record["outcome"] = "schema_blocked"
            record["error"] = {
                "stage": "configuration_validation",
                "type": type(error).__name__,
                "message": str(error),
            }
            exclusions += 1
            ledger.append(record)
            continue
        fingerprint = design_point_fingerprint_v1(resolved)
        record["design_point_fingerprint_v1"] = fingerprint
        if fingerprint in accepted_by_fingerprint:
            record["outcome"] = "duplicate"
            record["duplicate_of_sample_id"] = accepted_by_fingerprint[fingerprint]
            exclusions += 1
            ledger.append(record)
            continue
        report = preflight_case(resolved)
        preflight_relative = Path("preflight") / f"{candidate_id}.json"
        _write_json(package_root / preflight_relative, report)
        record["preflight_result"] = {
            "path": preflight_relative.as_posix(),
            "ready": bool(report["ready"]),
            "failed_stage": report["failed_stage"],
        }
        if not report["ready"]:
            record["outcome"] = "preflight_blocked"
            record["error"] = {
                "stage": report["failed_stage"],
                "type": report["exception_type"],
                "message": report["error_message"],
            }
            exclusions += 1
            ledger.append(record)
            continue
        accepted_count += 1
        sample_id = f"sample-{accepted_count:06d}"
        record["sample_id"] = sample_id
        record["outcome"] = "accepted"
        accepted_by_fingerprint[fingerprint] = sample_id
        ledger.append(record)

    status = (
        "blocked"
        if accepted_count == 0
        else "ready_with_exclusions"
        if exclusions
        else "ready"
    )
    extra = {
        "dependencies": {
            "databases": _dedupe_dependencies(db_records),
            "kinetics": _dedupe_dependencies(kin_records),
        },
        "existing_case_sources": [
            {
                "case_id": item["item"].case_id,
                "package_path": item["source_snapshot"],
                "sha256": file_sha256(package_root / item["source_snapshot"]),
            }
            for item in prepared
        ],
    }
    return (
        {
            "design_id": design_id,
            "design_spec_hash_v1": spec_hash,
            "generation_status": status,
            "resolved_spec": resolved_spec,
        },
        ledger,
        extra,
    )


def _write_ledger(package_root: Path, ledger: list[dict[str, Any]]) -> str:
    path = package_root / "candidate_ledger.jsonl"
    with path.open("wb") as stream:
        for record in ledger:
            stream.write(canonical_json_bytes(record) + b"\n")
    return file_sha256(path)


def _write_accepted_csv(package_root: Path, ledger: list[dict[str, Any]]) -> str:
    accepted = [record for record in ledger if record["outcome"] == "accepted"]
    parameter_ids: list[str] = []
    if accepted and accepted[0]["canonical_parameter_vector"]:
        parameter_ids = [
            item["parameter_id"] for item in accepted[0]["canonical_parameter_vector"]
        ]
    fields = [
        "sample_id",
        "candidate_id",
        "design_point_fingerprint_v1",
        "case_path",
        "case_sha256",
        "kinetics_path",
        "kinetics_sha256",
    ] + [
        name
        for parameter_id in parameter_ids
        for name in (f"{parameter_id}__value", f"{parameter_id}__unit")
    ]
    path = package_root / "accepted_samples.csv"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for record in accepted:
            row = {field: record.get(field) for field in fields}
            for item in record["canonical_parameter_vector"]:
                row[f"{item['parameter_id']}__value"] = item["value"]
                row[f"{item['parameter_id']}__unit"] = item["unit"]
            writer.writerow(row)
    return file_sha256(path)


def generate_design(spec_path: str | Path, output_dir: str | Path) -> Path:
    """Generate and atomically finalise one DoE package."""
    spec_source = Path(spec_path).resolve()
    destination = Path(output_dir).resolve()
    if destination.exists():
        raise FileExistsError(f"DoE design package already exists: {destination}")
    spec, source_bytes = load_design_spec(spec_source)
    source_spec_sha = __import__("hashlib").sha256(source_bytes).hexdigest()
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=destination.parent)
    )
    try:
        (staging / "doe_spec.source.yaml").write_bytes(source_bytes)
        if isinstance(spec, GeneratedDesignSpec):
            summary, ledger, extra = _process_generated(staging, spec_source, spec)
        else:
            summary, ledger, extra = _process_existing(staging, spec_source, spec)
        resolved_path = staging / "doe_spec.resolved.json"
        _write_json(resolved_path, summary["resolved_spec"])
        ledger_sha = _write_ledger(staging, ledger)
        accepted_sha = _write_accepted_csv(staging, ledger)
        counts = Counter(record["outcome"] for record in ledger)
        manifest = {
            "design_manifest_schema_version": DESIGN_MANIFEST_SCHEMA_VERSION,
            "design_id": summary["design_id"],
            "design_spec_hash_v1": summary["design_spec_hash_v1"],
            "generation_status": summary["generation_status"],
            "source_spec": {
                "package_path": "doe_spec.source.yaml",
                "sha256": source_spec_sha,
            },
            "resolved_spec": {
                "package_path": "doe_spec.resolved.json",
                "sha256": file_sha256(resolved_path),
            },
            "candidate_ledger": {
                "package_path": "candidate_ledger.jsonl",
                "sha256": ledger_sha,
            },
            "accepted_samples": {
                "package_path": "accepted_samples.csv",
                "sha256": accepted_sha,
            },
            "candidate_counts": {"attempted": len(ledger), **dict(sorted(counts.items()))},
            **extra,
        }
        _write_json(staging / "design_manifest.json", manifest)
        os.replace(staging, destination)
        return destination
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _safe_package_artifact(package_root: Path, relative: str) -> Path:
    path = (package_root / relative).resolve()
    if path != package_root and package_root not in path.parents:
        raise ValueError(f"manifest artifact escapes package root: {relative}")
    if not path.is_file():
        raise FileNotFoundError(f"design artifact does not exist: {relative}")
    return path


def load_manifest(path: str | Path) -> tuple[Path, dict[str, Any]]:
    manifest_path = Path(path).resolve()
    if manifest_path.is_dir():
        manifest_path = manifest_path / "design_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"design manifest does not exist: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("design_manifest_schema_version") != DESIGN_MANIFEST_SCHEMA_VERSION:
        raise ValueError("unsupported design manifest schema version")
    package_root = manifest_path.parent
    for key in ("source_spec", "resolved_spec", "candidate_ledger", "accepted_samples"):
        record = manifest[key]
        artifact = _safe_package_artifact(package_root, record["package_path"])
        if file_sha256(artifact) != record["sha256"]:
            raise ValueError(f"design artifact hash mismatch: {record['package_path']}")

    resolved = json.loads(
        _safe_package_artifact(
            package_root, manifest["resolved_spec"]["package_path"]
        ).read_text(encoding="utf-8")
    )
    spec_hash = design_spec_hash_v1(resolved)
    if spec_hash != manifest.get("design_spec_hash_v1"):
        raise ValueError("design_spec_hash_v1 does not match resolved design specification")
    if manifest.get("design_id") != f"doe-{spec_hash}":
        raise ValueError("design_id does not match resolved design specification")

    for optional in ("base_case", "imported_matrix"):
        record = manifest.get(optional)
        if record:
            artifact = _safe_package_artifact(package_root, record["package_path"])
            if file_sha256(artifact) != record["sha256"]:
                raise ValueError(f"design artifact hash mismatch: {record['package_path']}")
    for record in manifest.get("existing_case_sources", []):
        artifact = _safe_package_artifact(package_root, record["package_path"])
        if file_sha256(artifact) != record["sha256"]:
            raise ValueError(
                f"existing-case snapshot hash mismatch: {record['package_path']}"
            )
    for group in ("databases", "kinetics"):
        for record in manifest.get("dependencies", {}).get(group, []):
            relative = record.get("package_path")
            if not relative:
                continue
            artifact = _safe_package_artifact(package_root, relative)
            expected = record.get("identity", {}).get("sha256")
            if expected and file_sha256(artifact) != expected:
                raise ValueError(f"dependency hash mismatch: {relative}")
    return package_root, manifest


def read_ledger(package_root: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    path = package_root / manifest["candidate_ledger"]["package_path"]
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

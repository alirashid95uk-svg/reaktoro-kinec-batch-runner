"""Leakage-safe assembly of AI-ready tables from audited saved runs."""

from __future__ import annotations

import hashlib
import math
import os
import runpy
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

import pandas as pd

from workbench_core.result_readers import ResultPackage
from workbench_core.fingerprints import canonical_sha256, sha256_file
from workbench_core.persistence import atomic_write_json, require_path_outside_roots
from workbench_core.schemas.common import ArtifactIdentity, QuantityDefinition, SoftwareIdentity, utc_now
from workbench_core.schemas.dataset_manifest import (
    DatasetArtifact,
    DatasetManifest,
    DatasetSourceRun,
    ExcludedDatasetRun,
    SplitDefinition,
)


DATASET_TYPES = {
    "final_state",
    "fixed_time",
    "time_dependent_tabular",
    "trajectory",
    "failure",
}


def assemble_dataset(
    packages: Iterable[ResultPackage],
    output_dir: str | Path,
    *,
    dataset_type: str,
    features: list[str],
    targets: list[str],
    auditor_path: str | Path,
    fixed_time_s: float | None = None,
    fixed_time_tolerance_s: float = 0.0,
    group_by: str = "run_id",
    split_proportions: dict[str, float] | None = None,
    seed: int = 0,
    duplicate_policy: str = "error",
    validity_domain_required: bool = False,
    qc_requirements: dict[str, Any] | None = None,
    source_study: str | None = None,
    source_study_manifest: str | Path | None = None,
    explicit_run_set_id: str | None = None,
    software_identity: SoftwareIdentity | dict[str, Any],
) -> dict[str, Path]:
    """Export valid rows and a separate exclusion ledger; never impute or interpolate."""
    _validate_request(
        dataset_type,
        features,
        targets,
        fixed_time_s,
        fixed_time_tolerance_s,
        group_by,
        split_proportions,
        duplicate_policy,
    )
    package_list = list(packages)
    if not package_list:
        raise ValueError("dataset assembly requires at least one source run")
    output = require_path_outside_roots(
        output_dir,
        [package.path for package in package_list],
    )
    study_manifest_path = Path(source_study_manifest).resolve() if source_study_manifest else None
    study_manifest = None
    if study_manifest_path is not None:
        from workbench_core.schemas.study_spec import StudyManifest

        study_manifest = StudyManifest.model_validate_json(study_manifest_path.read_bytes())
        if not study_manifest.ready:
            raise ValueError("source study manifest is not finalised and ready")
        if source_study is not None and source_study != study_manifest.study_id:
            raise ValueError("source study ID disagrees with source study manifest")
        source_study = study_manifest.study_id
    if source_study is not None and explicit_run_set_id is not None:
        raise ValueError("provide source_study or explicit_run_set_id, not both")
    missing_identity = [
        str(package.path)
        for package in package_list
        if package.run_id is None or package.scientific_fingerprint is None
    ]
    if missing_identity:
        raise ValueError(
            "dataset sources require durable run IDs and scientific fingerprints: "
            + ", ".join(missing_identity)
        )
    run_ids = [str(package.run_id) for package in package_list]
    if len(run_ids) != len(set(run_ids)):
        raise ValueError("dataset source run IDs must be unique")
    if source_study is not None:
        mismatched = [
            str(package.run_id)
            for package in package_list
            if package.run_record.get("study_id") != source_study
        ]
        if mismatched:
            raise ValueError(f"source_study does not match source run lineage: {mismatched}")
        if study_manifest is not None:
            admissible_study_runs = {
                sample.run_id
                for sample in study_manifest.samples
                if sample.run_id
                and (
                    dataset_type == "failure"
                    or (
                        sample.completion_state == "completed"
                        and sample.qc_state == "complete"
                    )
                )
            }
            missing = sorted(set(run_ids) - admissible_study_runs)
            if missing:
                raise ValueError(
                    f"source runs are not admissible records in the study manifest: {missing}"
                )
    audit = _load_auditor(Path(auditor_path))
    valid: list[tuple[ResultPackage, pd.DataFrame, dict[str, Any]]] = []
    excluded: list[dict[str, Any]] = []
    for package in package_list:
        if dataset_type == "failure":
            if package.status.interpretation_supported:
                excluded.append(_exclusion(package, ["completed run is not a failure record"]))
                continue
            frame = pd.DataFrame(
                [
                    {
                        "termination_category": package.run_record.get("termination_category")
                        or "indeterminate",
                        "output_completeness": package.status.output_completeness,
                        "failure_reason": package.status.reason,
                    }
                ]
            )
            valid.append((package, frame, _lineage(package)))
            continue
        reasons = _gate_run(
            package,
            features + targets,
            audit,
            validity_domain_required,
            qc_requirements or {},
        )
        if reasons:
            excluded.append(_exclusion(package, reasons))
            continue
        try:
            frame = _select_rows(
                package,
                dataset_type,
                features + targets,
                fixed_time_s,
                fixed_time_tolerance_s,
            )
        except (FileNotFoundError, ValueError, KeyError) as error:
            excluded.append(_exclusion(package, [str(error)]))
            continue
        nonfinite = _nonfinite_columns(frame, features + targets)
        if nonfinite:
            excluded.append(
                _exclusion(package, ["prohibited missing/non-finite values: " + ", ".join(nonfinite)])
            )
            continue
        valid.append((package, frame, _lineage(package)))

    valid = _apply_duplicate_policy(valid, excluded, duplicate_policy)
    if dataset_type != "failure" and not valid:
        raise ValueError("no source run passed dataset completion and QC gates")
    group_map = _split_groups(valid, group_by)
    splits = _assign_splits(
        sorted(set(group_map.values())),
        split_proportions or {"train": 0.7, "validation": 0.15, "test": 0.15},
        seed,
    )
    rows = []
    for package, frame, lineage in valid:
        copied = (
            pd.DataFrame(
                [
                    {
                        column: frame[column].tolist()
                        for column in ["time_s", *features, *targets]
                    }
                ]
            )
            if dataset_type == "trajectory"
            else frame.copy()
        )
        copied.insert(0, "run_id", package.run_id)
        copied.insert(1, "scientific_fingerprint", package.scientific_fingerprint)
        copied.insert(2, "source_study_id", lineage.get("study_id"))
        copied.insert(3, "split_group", group_map[str(package.run_id)])
        copied.insert(4, "split", splits[group_map[str(package.run_id)]])
        rows.append(copied)
    columns = [
        "run_id",
        "scientific_fingerprint",
        "source_study_id",
        "split_group",
        "split",
        *(
            ["time_s"]
            if dataset_type in {"fixed_time", "time_dependent_tabular", "trajectory"}
            else []
        ),
        *(
            ["termination_category", "output_completeness", "failure_reason"]
            if dataset_type == "failure"
            else []
        ),
        *features,
        *targets,
    ]
    data = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=columns)
    failure = pd.DataFrame(excluded, columns=["run_id", "termination_category", "reasons"])

    output.mkdir(parents=True, exist_ok=False)
    csv_path = output / "dataset.csv"
    parquet_path = output / "dataset.parquet"
    failure_path = output / "failure_ledger.csv"
    data.to_csv(csv_path, index=False)
    data.to_parquet(parquet_path, index=False)
    failure.to_csv(failure_path, index=False)
    descriptors = _quantity_definitions(valid, features + targets)
    run_ids_by_split = {
        name: sorted(
            str(package.run_id)
            for package, _, _ in valid
            if splits[group_map[str(package.run_id)]] == name
        )
        for name in (split_proportions or {"train": 0.7, "validation": 0.15, "test": 0.15})
    }
    leakage = _leakage_checks(valid, group_map, splits)
    if not leakage["passed"]:
        raise ValueError("dataset split leakage check failed")
    manifest = DatasetManifest(
        dataset_schema_version="1.0",
        dataset_id=str(uuid4()),
        created_at_utc=utc_now(),
        dataset_type=dataset_type,
        source_study_id=source_study,
        explicit_run_set_id=(
            None
            if source_study is not None
            else explicit_run_set_id
            or f"explicit-{canonical_sha256(sorted(run_ids))[:16]}"
        ),
        source_runs=tuple(
            DatasetSourceRun(
                run_id=str(package.run_id),
                output_schema_version=package.schema_version,
                scientific_fingerprint=str(package.scientific_fingerprint),
            )
            for package in package_list
        ),
        features=tuple(descriptors[name] for name in features),
        targets=tuple(descriptors[name] for name in targets),
        time_semantics=(
            f"native saved accepted states; fixed_time_s={fixed_time_s}; "
            f"tolerance_s={fixed_time_tolerance_s}; interpolation=forbidden"
        ),
        validity_domain={
            "required": validity_domain_required,
            "source_run_domains": {
                str(package.run_id): (
                    package.run_record.get("validity_domain")
                    or package.manifest.get("validity_domain")
                    or package.manifest.get("input_snapshot", {}).get("validity_domain")
                )
                for package, _, _ in valid
            },
        },
        completion_qc_filters=tuple(
            [
                "simulation_completed=true",
                "output_completeness=complete",
                "authoritative_output_audit=passed",
                *(f"{key}={value!r}" for key, value in sorted((qc_requirements or {}).items())),
            ]
            if dataset_type != "failure"
            else ["non-complete run record required"]
        ),
        missing_value_policy="reject run; no imputation",
        duplicate_policy=duplicate_policy,
        split_definition=SplitDefinition(
            group_column=group_by,
            algorithm="deterministic SHA-256 group assignment",
            proportions=split_proportions
            or {"train": 0.7, "validation": 0.15, "test": 0.15},
            seed=seed,
            run_ids_by_split={name: tuple(values) for name, values in run_ids_by_split.items()},
            excluded_groups=tuple(sorted(str(item["run_id"]) for item in excluded)),
            leakage_checks=(
                "run IDs do not cross splits",
                "study/scenario groups do not cross splits",
                "replicate scientific fingerprints do not cross splits",
            ),
        ),
        seed=seed,
        excluded_runs=tuple(
            ExcludedDatasetRun(run_id=str(item["run_id"]), reason=str(item["reasons"]))
            for item in excluded
        ),
        failure_ledger_path=failure_path.name,
        artifacts=tuple(
            DatasetArtifact(format=format_name, path=path.name, sha256=sha256_file(path))
            for format_name, path in (
                ("csv", csv_path),
                ("parquet", parquet_path),
                ("csv", failure_path),
            )
        ),
        software_identity=SoftwareIdentity.model_validate(software_identity),
    )
    manifest_path = output / "dataset_manifest.json"
    atomic_write_json(manifest_path, manifest)
    if study_manifest is not None and study_manifest_path is not None:
        identity = ArtifactIdentity(
            path=Path(os.path.relpath(manifest_path, study_manifest_path.parent)).as_posix(),
            sha256=sha256_file(manifest_path),
        )
        exports = tuple(
            artifact
            for artifact in study_manifest.dataset_exports
            if artifact.sha256 != identity.sha256
        ) + (identity,)
        atomic_write_json(
            study_manifest_path,
            study_manifest.model_copy(
                update={"dataset_exports": exports, "finalised_at_utc": utc_now()}
            ),
        )
    return {
        "manifest": manifest_path,
        "csv": csv_path,
        "parquet": parquet_path,
        "failure_ledger": failure_path,
    }


def _validate_request(
    dataset_type: str,
    features: list[str],
    targets: list[str],
    fixed_time_s: float | None,
    tolerance: float,
    group_by: str,
    splits: dict[str, float] | None,
    duplicate_policy: str,
) -> None:
    if dataset_type not in DATASET_TYPES:
        raise ValueError(f"dataset_type must be one of {sorted(DATASET_TYPES)}")
    if dataset_type == "failure":
        if features or targets:
            raise ValueError("failure datasets use run-level failure fields, not scientific features/targets")
    elif not features or not targets or set(features) & set(targets):
        raise ValueError("features and targets must be non-empty, distinct quantity lists")
    if dataset_type == "fixed_time" and fixed_time_s is None:
        raise ValueError("fixed_time_s is required for a fixed_time dataset")
    if tolerance < 0:
        raise ValueError("fixed_time_tolerance_s must be non-negative")
    if group_by not in {"run_id", "study_id", "scenario_group"}:
        raise ValueError("group_by must be run_id, study_id, or scenario_group")
    if duplicate_policy not in {"error", "exclude", "allow_replicates"}:
        raise ValueError("duplicate_policy must be error, exclude, or allow_replicates")
    values = splits or {"train": 0.7, "validation": 0.15, "test": 0.15}
    if set(values) != {"train", "validation", "test"} or any(value < 0 for value in values.values()):
        raise ValueError("split_proportions require non-negative train, validation, and test values")
    if not math.isclose(sum(values.values()), 1.0, abs_tol=1e-12):
        raise ValueError("split_proportions must sum to 1")


def _load_auditor(path: Path):
    if not path.is_file():
        raise FileNotFoundError(f"authoritative output auditor does not exist: {path}")
    audit = runpy.run_path(str(path)).get("audit")
    if not callable(audit):
        raise ValueError(f"auditor does not expose audit(output_dir): {path}")
    return audit


def _gate_run(
    package: ResultPackage,
    quantities: list[str],
    audit,
    validity_required: bool,
    qc_requirements: dict[str, Any],
) -> list[str]:
    reasons = []
    if package.run_id is None:
        reasons.append("durable run_id is missing")
    if package.scientific_fingerprint is None:
        reasons.append("scientific fingerprint is missing")
    if not package.status.interpretation_supported:
        reasons.append(package.status.reason)
    audit_result = {}
    try:
        audit_result = audit(package.path)
    except Exception as error:  # auditor failure is a conservative dataset blocker
        reasons.append(f"authoritative output audit failed to execute: {error}")
    else:
        if not audit_result.get("ok"):
            reasons.append("authoritative output audit failed: " + "; ".join(audit_result.get("errors", [])))
    try:
        available = package.quantity_descriptors()
    except (FileNotFoundError, ValueError, OSError) as error:
        reasons.append(str(error))
        available = {}
    missing = [name for name in quantities if name not in available]
    if missing:
        reasons.append("required quantities unavailable: " + ", ".join(missing))
    if validity_required and not (
        package.run_record.get("validity_domain")
        or package.manifest.get("validity_domain")
        or package.manifest.get("input_snapshot", {}).get("validity_domain")
    ):
        reasons.append("required validity-domain metadata is missing")
    for dotted_path, expected in qc_requirements.items():
        source, path = (
            (audit_result, dotted_path[6:])
            if dotted_path.startswith("audit.")
            else (package.diagnostics, dotted_path.removeprefix("diagnostics."))
        )
        actual = _get(source, path)
        if not _qc_passes(actual, expected):
            reasons.append(f"QC requirement not met: {dotted_path} with rule {expected!r}; actual={actual!r}")
    return reasons


def _select_rows(
    package: ResultPackage,
    dataset_type: str,
    quantities: list[str],
    fixed_time_s: float | None,
    tolerance: float,
) -> pd.DataFrame:
    columns = ["time_s", *quantities]
    frame = package.read_table("timeseries.csv", columns=columns)
    if dataset_type == "final_state":
        duration = package.manifest.get("time_semantics", {}).get("duration_s")
        if duration is None:
            raise ValueError("requested duration is unavailable for final-state selection")
        matches = frame.loc[(frame["time_s"] - float(duration)).abs() <= 1e-12]
        if matches.empty:
            raise ValueError("saved timeseries does not contain the requested final state")
        return matches.iloc[[-1]][quantities].reset_index(drop=True)
    if dataset_type == "fixed_time":
        distances = (frame["time_s"] - float(fixed_time_s)).abs()
        matches = frame.loc[distances <= tolerance]
        if matches.empty:
            raise ValueError("requested fixed time is not a native saved timestamp within tolerance")
        index = distances.loc[matches.index].idxmin()
        return frame.loc[[index], columns].reset_index(drop=True)
    return frame[columns].reset_index(drop=True)


def _nonfinite_columns(frame: pd.DataFrame, columns: list[str]) -> list[str]:
    invalid = []
    for name in columns:
        values = pd.to_numeric(frame[name], errors="coerce")
        if values.isna().any() or not values.map(math.isfinite).all():
            invalid.append(name)
    return invalid


def _apply_duplicate_policy(valid, excluded, policy):
    by_fingerprint: dict[str, list[Any]] = {}
    for record in valid:
        by_fingerprint.setdefault(record[0].scientific_fingerprint or "missing", []).append(record)
    duplicates = [items for items in by_fingerprint.values() if len(items) > 1]
    if duplicates and policy == "error":
        raise ValueError("duplicate scientific fingerprints require an explicit duplicate policy")
    if policy != "exclude":
        return valid
    keep = []
    for items in by_fingerprint.values():
        keep.append(items[0])
        excluded.extend(
            _exclusion(item[0], [f"duplicate of run {items[0][0].run_id}"]) for item in items[1:]
        )
    return keep


def _split_groups(valid, group_by: str) -> dict[str, str]:
    parent = {str(package.run_id): str(package.run_id) for package, _, _ in valid}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left: str, right: str) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[max(a, b)] = min(a, b)

    buckets: dict[tuple[str, str], str] = {}
    for package, _, lineage in valid:
        run_id = str(package.run_id)
        if group_by != "run_id" and not lineage.get(group_by):
            raise ValueError(f"run {run_id} has no required {group_by} split-group identity")
        requested = run_id if group_by == "run_id" else str(lineage[group_by])
        for key in (("requested", requested), ("fingerprint", package.scientific_fingerprint or run_id)):
            if key in buckets:
                union(run_id, buckets[key])
            else:
                buckets[key] = run_id
    return {run_id: find(run_id) for run_id in parent}


def _assign_splits(groups: list[str], proportions: dict[str, float], seed: int) -> dict[str, str]:
    train = proportions["train"]
    validation = train + proportions["validation"]
    result = {}
    for group in groups:
        digest = hashlib.sha256(f"{seed}:{group}".encode()).digest()
        value = int.from_bytes(digest[:8], "big") / 2**64
        result[group] = "train" if value < train else "validation" if value < validation else "test"
    return result


def _lineage(package: ResultPackage) -> dict[str, Any]:
    return {
        "study_id": package.run_record.get("study_id"),
        "scenario_group": package.run_record.get("scenario_group"),
        "case_name": package.run_record.get("case_name")
        or package.manifest.get("run_identity", {}).get("case_name"),
    }


def _quantity_definitions(valid, quantities: list[str]) -> dict[str, QuantityDefinition]:
    if not quantities:
        return {}
    if not valid:
        raise ValueError("quantity definitions require at least one valid source run")
    descriptors = valid[0][0].quantity_descriptors()
    return {
        name: QuantityDefinition(
            quantity_id=descriptor.quantity_id,
            label=descriptor.label,
            scientific_meaning=descriptor.scientific_meaning,
            unit=descriptor.unit,
            value_type=descriptor.value_type,
            sign_domain=descriptor.sign_domain,
            extent=descriptor.extensive_or_intensive,
            time_semantics=descriptor.time_semantics,
            source_file=descriptor.source_file,
            source_column=descriptor.source_column,
            source_output_schema_version=descriptor.source_output_schema_version,
        )
        for name in quantities
        for descriptor in (descriptors[name],)
    }


def _leakage_checks(valid, group_map, splits) -> dict[str, Any]:
    run_to_splits: dict[str, set[str]] = {}
    fingerprint_to_splits: dict[str, set[str]] = {}
    for package, _, _ in valid:
        split = splits[group_map[str(package.run_id)]]
        run_to_splits.setdefault(str(package.run_id), set()).add(split)
        fingerprint_to_splits.setdefault(package.scientific_fingerprint or str(package.run_id), set()).add(split)
    return {
        "run_cross_split": any(len(value) > 1 for value in run_to_splits.values()),
        "replicate_fingerprint_cross_split": any(
            len(value) > 1 for value in fingerprint_to_splits.values()
        ),
        "passed": all(len(value) == 1 for value in [*run_to_splits.values(), *fingerprint_to_splits.values()]),
    }


def _exclusion(package: ResultPackage, reasons: list[str]) -> dict[str, Any]:
    return {
        "run_id": package.run_id,
        "termination_category": package.run_record.get("termination_category"),
        "reasons": "; ".join(reasons),
    }


def _get(value: dict[str, Any], dotted_path: str) -> Any:
    current: Any = value
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _qc_passes(actual: Any, rule: Any) -> bool:
    if not isinstance(rule, dict):
        return actual == rule
    if set(rule) != {"operator", "value"}:
        raise ValueError("QC threshold rules require exactly operator and value")
    operator, expected = rule["operator"], rule["value"]
    if operator == "eq":
        return actual == expected
    try:
        actual_number, expected_number = float(actual), float(expected)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(actual_number) or not math.isfinite(expected_number):
        return False
    if operator == "le":
        return actual_number <= expected_number
    if operator == "ge":
        return actual_number >= expected_number
    if operator == "abs_le":
        return abs(actual_number) <= expected_number
    raise ValueError(f"unsupported QC threshold operator: {operator}")

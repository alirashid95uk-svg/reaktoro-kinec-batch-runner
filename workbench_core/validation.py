"""Immutable preflight snapshots, receipts, and prelaunch staleness checks."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

from batch_runner.config import load_case
from workbench_core.environment import doctor
from workbench_core.fingerprints import (
    operational_fingerprint,
    scientific_fingerprint,
    sha256_bytes,
    sha256_file,
)
from workbench_core.persistence import atomic_write_bytes, atomic_write_json
from workbench_core.protocol_reader import ProtocolLineStatus, parse_protocol_lines
from workbench_core.schemas.common import (
    ArtifactIdentity,
    CodeIdentity,
    DependencyIdentity,
    EnvironmentIdentity,
    utc_now,
)
from workbench_core.schemas.validation_receipt import (
    KineticMappingResult,
    PreflightStageResult,
    ProcessOutcome,
    ValidationReceipt,
)


CONFIGURATION_SCHEMA_VERSION = "case_config_v1"
RUNNER_VERSION = "objective1_batch_runner_v1"
WORKER_PROTOCOL_VERSION = "1.0"
PREFLIGHT_PREFIX = "PREFLIGHT_RESULT:"
PREFLIGHT_STAGES = (
    "configuration_validation",
    "database_loading",
    "kinetics_loading",
    "mapping",
    "system_construction",
    "state_construction",
    "operational_readiness",
)


def validate_case(
    source_case: str | Path,
    project_root: str | Path,
    solver_prefix: str | Path,
    validation_root: str | Path,
    *,
    conda_executable: str | Path | None = None,
) -> tuple[ValidationReceipt, Path]:
    """Run authoritative construction preflight and persist its immutable evidence."""
    source = Path(source_case).resolve()
    root = Path(project_root).resolve()
    receipt_id = str(uuid4())
    directory = Path(validation_root).resolve() / receipt_id
    directory.mkdir(parents=True, exist_ok=False)
    snapshot = directory / "validation_case.yaml"
    source_bytes = source.read_bytes()
    atomic_write_bytes(snapshot, source_bytes)
    snapshot_sha = sha256_bytes(source_bytes)
    operation = {
        "operation_id": receipt_id,
        "validation_snapshot": snapshot.name,
        "source_case_sha256": snapshot_sha,
    }
    operational_sha = operational_fingerprint(operation)
    environment_report = doctor(root, solver_prefix, conda_executable=conda_executable)
    environment_identity, code_identity = _identities(environment_report)
    environment_path = directory / "solver_environment_evidence.json"
    atomic_write_json(environment_path, environment_report)
    environment_evidence = ArtifactIdentity(
        path=environment_path.name,
        sha256=sha256_file(environment_path),
    )

    resolved = None
    dependency_identities: tuple[DependencyIdentity, ...] = ()
    fingerprint = None
    local_error = None
    try:
        resolved = load_case(snapshot, output_dir_override=directory / "preflight_output")
        _require_output_readiness(resolved.output_dir)
        dependency_identities = _dependencies(
            resolved,
            environment_identity.reaktoro_version,
            _reaktoro_build(environment_report),
            root,
        )
        fingerprint = scientific_fingerprint(
            _resolved_scientific_configuration(resolved),
            dependency_identities=dependency_identities,
            code_identity=code_identity,
            environment_identity=environment_identity,
            configuration_schema_version=CONFIGURATION_SCHEMA_VERSION,
        )
    except Exception as error:
        local_error = error

    command = [
        *environment_report["solver_environment_identity"]["launch_command"],
        str(root / "runner.py"),
        "--preflight",
        str(snapshot),
        "--events-jsonl",
        "--operation-id",
        receipt_id,
    ]
    completed = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    atomic_write_bytes(directory / "preflight_events.jsonl", completed.stdout.encode("utf-8"))
    atomic_write_bytes(directory / "preflight_stderr.log", completed.stderr.encode("utf-8"))
    events = list(parse_protocol_lines(completed.stdout.splitlines()))
    report = _preflight_report(completed.stderr)
    dependency_hash_errors = _dependency_hash_errors(resolved, report)
    protocol_errors = [
        parsed.error or parsed.status.value
        for parsed in events
        if parsed.status is not ProtocolLineStatus.EVENT
    ]
    ready = bool(
        completed.returncode == 0
        and report.get("ready") is True
        and fingerprint is not None
        and environment_report["ready"]
        and not protocol_errors
        and not dependency_hash_errors
    )
    errors = []
    if local_error is not None:
        errors.append(str(local_error))
    errors.extend(protocol_errors)
    errors.extend(dependency_hash_errors)
    if report.get("error_message"):
        errors.append(str(report["error_message"]))
    if not environment_report["ready"]:
        errors.extend(
            item["detail"]
            for item in environment_report["checks"]
            if item["blocking"] and not item["ok"]
        )
    failed_stage = None if ready else str(report.get("failed_stage") or "operational_readiness")
    receipt = ValidationReceipt(
        receipt_schema_version="1.0",
        receipt_id=receipt_id,
        created_at_utc=utc_now(),
        case_name=(resolved.config.case.name if resolved else report.get("case_name") or source.stem),
        validated_snapshot_sha256=snapshot_sha,
        scientific_fingerprint=fingerprint,
        operational_fingerprint=operational_sha,
        configuration_schema_version=CONFIGURATION_SCHEMA_VERSION,
        runner_version=RUNNER_VERSION,
        worker_protocol_version=WORKER_PROTOCOL_VERSION,
        solver_environment_identity=environment_identity,
        environment_evidence=environment_evidence,
        code_identity=code_identity,
        dependency_identities=dependency_identities,
        preflight_stage_results=_stage_results(events, report, ready),
        kinetic_mapping_summary=_mapping_results(events),
        ready=ready,
        failed_stage=failed_stage,
        errors=tuple(dict.fromkeys(errors)),
        warnings=tuple(_warnings(events)),
        process_outcome=ProcessOutcome(
            exit_code=completed.returncode,
            termination_category="preflight_passed" if ready else "blocked_preflight",
            stderr_log_path="preflight_stderr.log",
        ),
    )
    receipt_path = directory / "validation_receipt.json"
    atomic_write_json(receipt_path, receipt)
    return receipt, receipt_path


def verify_prelaunch(
    receipt: ValidationReceipt,
    final_snapshot: str | Path,
    expected_snapshot_sha256: str,
    project_root: str | Path,
    solver_prefix: str | Path,
    *,
    conda_executable: str | Path | None = None,
) -> dict[str, Any]:
    """Recompute every launch identity; return exact mismatches without changing inputs."""
    snapshot = Path(final_snapshot).resolve()
    root = Path(project_root).resolve()
    mismatches = []
    actual_snapshot_sha = sha256_file(snapshot)
    if actual_snapshot_sha != expected_snapshot_sha256:
        mismatches.append("final snapshot SHA-256 changed")
    if actual_snapshot_sha != receipt.validated_snapshot_sha256:
        mismatches.append("final snapshot no longer matches the validation receipt")
    environment_report = doctor(root, solver_prefix, conda_executable=conda_executable)
    if not environment_report["ready"]:
        mismatches.append("solver environment Doctor is no longer ready")
    environment_identity, code_identity = _identities(environment_report)
    try:
        resolved = load_case(snapshot)
        _require_output_readiness(resolved.output_dir)
        dependencies = _dependencies(
            resolved,
            environment_identity.reaktoro_version,
            _reaktoro_build(environment_report),
            root,
        )
        fingerprint = scientific_fingerprint(
            _resolved_scientific_configuration(resolved),
            dependency_identities=dependencies,
            code_identity=code_identity,
            environment_identity=environment_identity,
            configuration_schema_version=CONFIGURATION_SCHEMA_VERSION,
        )
    except Exception as error:
        mismatches.append(f"prelaunch resolution failed: {error}")
        fingerprint = None
        dependencies = ()
    if fingerprint != receipt.scientific_fingerprint:
        mismatches.append("scientific fingerprint changed after validation")
    if code_identity != receipt.code_identity:
        mismatches.append("runner code identity changed after validation")
    if environment_identity != receipt.solver_environment_identity:
        mismatches.append("solver environment identity changed after validation")
    if dependencies != receipt.dependency_identities:
        mismatches.append("scientific dependency identity changed after validation")
    return {
        "ready": receipt.ready and not mismatches,
        "snapshot_sha256": actual_snapshot_sha,
        "scientific_fingerprint": fingerprint,
        "mismatches": mismatches,
    }


def _identities(report: dict[str, Any]) -> tuple[EnvironmentIdentity, CodeIdentity]:
    solver = report["solver_environment_identity"]
    code = report["code_identity"]
    return (
        EnvironmentIdentity(
            python_version=solver["python_version"],
            reaktoro_version=solver["reaktoro_version"],
            platform=report["platform"],
            environment_spec_sha256=solver["environment_export_sha256"],
            package_inventory_sha256=solver["package_inventory_sha256"],
        ),
        CodeIdentity(
            commit=code.get("commit") or "unversioned",
            dirty=code["dirty"],
            relevant_source_sha256=code["relevant_tree_sha256"],
        ),
    )


def _dependencies(
    resolved,
    reaktoro_version: str | None,
    reaktoro_build: str,
    root: Path,
) -> tuple[DependencyIdentity, ...]:
    config = resolved.config
    dependencies = []
    if resolved.database_path is None:
        dependencies.append(
            DependencyIdentity(
                logical_name=f"database:{config.database.name}",
                sha256=None,
                source="reaktoro_package",
                version=reaktoro_version,
                package_build=reaktoro_build,
                hash_unavailable_reason="embedded database has no standalone project file",
            )
        )
    else:
        dependencies.append(
            DependencyIdentity(
                logical_name=_logical_path(resolved.database_path, root),
                sha256=sha256_file(resolved.database_path),
                source="local PHREEQC-style thermodynamic database",
                version=None,
                hash_unavailable_reason=None,
            )
        )
    if resolved.kinetics_path is not None:
        dependencies.append(
            DependencyIdentity(
                logical_name=_logical_path(resolved.kinetics_path, root),
                sha256=sha256_file(resolved.kinetics_path),
                source=f"{config.kinetics.model} kinetic parameter file",
                version=None,
                hash_unavailable_reason=None,
            )
        )
    return tuple(dependencies)


def _dependency_hash_errors(resolved, report: dict[str, Any]) -> list[str]:
    if resolved is None:
        return []
    expected_database = sha256_file(resolved.database_path) if resolved.database_path else None
    expected_kinetics = sha256_file(resolved.kinetics_path) if resolved.kinetics_path else None
    errors = []
    for label, expected, actual in (
        ("database", expected_database, report.get("database_sha256")),
        ("kinetic parameter", expected_kinetics, report.get("kinetic_parameter_sha256")),
    ):
        if expected != actual:
            errors.append(
                f"{label} SHA-256 from worker preflight disagrees with validated dependency identity"
            )
    return errors


def _require_output_readiness(output_dir: Path) -> None:
    if output_dir.exists():
        raise FileExistsError(f"fresh output directory already exists: {output_dir}")
    ancestor = output_dir.parent
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    if not ancestor.is_dir() or not os.access(ancestor, os.W_OK):
        raise PermissionError(f"output parent is not writable: {ancestor}")
    if os.name == "nt" and len(str(output_dir)) >= 260:
        raise ValueError(f"output path exceeds the conservative Windows path limit: {output_dir}")


def _resolved_scientific_configuration(resolved) -> dict[str, Any]:
    data = resolved.config.model_dump(mode="json")
    _canonicalise_amount_units(data)
    timestep = data["solver"]["timestep"]
    timestep["time"] = {"duration_s": resolved.duration_s}
    timestep["step_size"] = {
        "dt_s": resolved.dt_s,
        "dt_initial_s": resolved.dt_initial_s,
        "dt_min_s": resolved.dt_min_s,
        "dt_max_s": resolved.dt_max_s,
    }
    timestep["output_schedule"] = resolved.output_schedule_summary()
    timestep["checkpoint_schedule"] = resolved.checkpoint_schedule_summary()
    if data["database"].get("path"):
        data["database"]["path"] = Path(data["database"]["path"]).as_posix()
    if data["kinetics"].get("path"):
        data["kinetics"]["path"] = Path(data["kinetics"]["path"]).as_posix()
    return data


def _canonicalise_amount_units(value: Any) -> None:
    conversions = {
        "kg": (1.0, "kg"),
        "g": (1e-3, "kg"),
        "mg": (1e-6, "kg"),
        "ug": (1e-9, "kg"),
        "mol": (1.0, "mol"),
        "mmol": (1e-3, "mol"),
        "umol": (1e-6, "mol"),
        "kmol": (1e3, "mol"),
        "L": (1.0, "L"),
        "mL": (1e-3, "L"),
    }
    if isinstance(value, dict):
        if set(value) >= {"value", "unit"} and value.get("unit") in conversions:
            scale, unit = conversions[value["unit"]]
            value["value"] = float(value["value"]) * scale
            value["unit"] = unit
        for child in value.values():
            _canonicalise_amount_units(child)
    elif isinstance(value, list):
        for child in value:
            _canonicalise_amount_units(child)


def _preflight_report(stderr: str) -> dict[str, Any]:
    for line in reversed(stderr.splitlines()):
        if line.startswith(PREFLIGHT_PREFIX):
            try:
                value = json.loads(line[len(PREFLIGHT_PREFIX) :])
            except ValueError:
                return {"ready": False, "failed_stage": "protocol", "error_message": "invalid preflight result"}
            return value if isinstance(value, dict) else {}
    return {"ready": False, "failed_stage": "protocol", "error_message": "preflight result was not emitted"}


def _stage_results(events, report, ready) -> tuple[PreflightStageResult, ...]:
    completed = set()
    failures: dict[str, list[str]] = {}
    for parsed in events:
        if parsed.event is None:
            continue
        event_type = parsed.event.event_type.value
        payload = parsed.event.payload
        stage = str(payload.get("stage", ""))
        if event_type == "stage_completed" and stage:
            completed.add(stage)
        elif event_type == "validation_issue" and stage:
            failures.setdefault(stage, []).append(str(payload.get("error_message", "validation failed")))
    failed_stage = str(report.get("failed_stage") or "")
    if failed_stage and report.get("error_message"):
        failures.setdefault(failed_stage, []).append(str(report["error_message"]))
    results = []
    for stage in PREFLIGHT_STAGES:
        if stage in failures:
            results.append(PreflightStageResult(stage=stage, status="failed", errors=tuple(failures[stage])))
        elif stage in completed or (stage == "operational_readiness" and ready):
            results.append(PreflightStageResult(stage=stage, status="passed"))
        else:
            results.append(PreflightStageResult(stage=stage, status="not_run"))
    return tuple(results)


def _mapping_results(events) -> tuple[KineticMappingResult, ...]:
    rows = []
    for parsed in events:
        if parsed.event is not None and parsed.event.event_type.value == "mapping_result":
            rows = parsed.event.payload.get("mapping", [])
    results = []
    for row in rows:
        mapped = row.get("status") != "failed"
        kinetic = row.get("role") == "kinetic"
        results.append(
            KineticMappingResult(
                mineral_name=str(row["mineral_name"]),
                kinetic_model=str(row.get("kinetic_model") or "not_applicable"),
                parameter_record=str(row["mineral_name"]) if kinetic and row.get("kinetic_parameter_record_found") else ("not_required" if not kinetic else None),
                surface_area_present=bool(row.get("surface_area_present") or not kinetic),
                mapped=mapped,
                reason=None if mapped else str(row.get("reason") or "mapping failed"),
            )
        )
    return tuple(results)


def _warnings(events) -> list[str]:
    return [
        str(parsed.event.payload.get("message"))
        for parsed in events
        if parsed.event is not None and parsed.event.event_type.value == "warning"
    ]


def _logical_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return f"external:{path.name}:{sha256_file(path)}"


def _reaktoro_build(report: dict[str, Any]) -> str:
    for package in report["solver_environment_identity"].get("package_inventory", []):
        if str(package.get("name", "")).lower() == "reaktoro":
            return str(package.get("build_string") or package.get("build") or package.get("version"))
    return report["solver_environment_identity"]["reaktoro_version"]

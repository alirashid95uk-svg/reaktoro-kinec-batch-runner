"""Launch accepted DoE samples through the existing batch runner."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np

from batch_runner.config import load_case
from batch_runner.config._base import PROJECT_ROOT
from batch_runner.run_directories import prepare_fresh_run_config
from batch_runner.simulator import preflight_case

from .identity import batch_runner_source_sha256, design_point_fingerprint_v1, file_sha256
from .package import load_manifest, read_ledger


def _sample_record(
    package_root: Path, manifest: dict[str, Any], sample_id: str
) -> dict[str, Any]:
    matches = [
        record
        for record in read_ledger(package_root, manifest)
        if record.get("sample_id") == sample_id and record.get("outcome") == "accepted"
    ]
    if len(matches) != 1:
        raise ValueError(f"accepted sample not found exactly once: {sample_id}")
    return matches[0]


def _git_identity() -> tuple[str | None, bool]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--", "runner.py", "batch_runner"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        return commit or None, bool(status)
    except (OSError, subprocess.CalledProcessError):
        return None, False


def _software_versions() -> dict[str, Any]:
    import reaktoro as rkt
    import scipy

    return {
        "python": sys.version.split()[0],
        "reaktoro": rkt.__version__,
        "numpy": np.__version__,
        "scipy": scipy.__version__,
    }


def launch_sample(
    manifest_path: str | Path,
    sample_id: str,
    *,
    preflight_only: bool = False,
    events_jsonl: bool = False,
) -> dict[str, Any]:
    """Verify, freshly preflight, and optionally execute one accepted sample."""
    package_root, manifest = load_manifest(manifest_path)
    if manifest["generation_status"] not in {"ready", "ready_with_exclusions"}:
        raise ValueError(
            f"design status {manifest['generation_status']!r} prohibits execution"
        )
    sample = _sample_record(package_root, manifest, sample_id)
    case_path = (package_root / sample["case_path"]).resolve()
    if package_root not in case_path.parents or file_sha256(case_path) != sample["case_sha256"]:
        raise ValueError(f"sample case hash mismatch: {sample_id}")
    if sample.get("kinetics_path"):
        kinetics_path = (package_root / sample["kinetics_path"]).resolve()
        if package_root not in kinetics_path.parents:
            raise ValueError("sample kinetics artifact escapes package")
        if file_sha256(kinetics_path) != sample["kinetics_sha256"]:
            raise ValueError(f"sample generated kinetics hash mismatch: {sample_id}")

    with tempfile.TemporaryDirectory(prefix="doe-launch-verify-") as temp_dir:
        design_case = load_case(
            case_path,
            output_dir_override=Path(temp_dir) / "results",
            artifact_root=package_root,
        )
    fingerprint = design_point_fingerprint_v1(design_case)
    if fingerprint != sample["design_point_fingerprint_v1"]:
        raise ValueError(
            f"sample fingerprint no longer matches finalized design: {sample_id}"
        )

    run_id = str(uuid4())
    snapshot = prepare_fresh_run_config(case_path, artifact_root=package_root)
    run_case = load_case(snapshot, artifact_root=package_root)
    launch_fingerprint = design_point_fingerprint_v1(run_case)
    if launch_fingerprint != fingerprint:
        raise RuntimeError(
            "fresh run snapshot changed the accepted design-point fingerprint"
        )

    preflight = preflight_case(run_case)
    if not preflight["ready"]:
        raise RuntimeError(
            f"fresh preflight failed at {preflight['failed_stage']}: "
            f"{preflight['error_message']}"
        )

    git_commit, dirty = _git_identity()
    lineage = {
        "schema_version": "1.0",
        "design_id": manifest["design_id"],
        "design_spec_hash_v1": manifest["design_spec_hash_v1"],
        "sample_id": sample_id,
        "design_point_fingerprint_v1": fingerprint,
        "run_id": run_id,
        "run_snapshot_sha256": file_sha256(snapshot),
        "batch_runner_source_sha256": batch_runner_source_sha256(PROJECT_ROOT),
        "code": {"git_commit": git_commit, "dirty": dirty},
        "software": _software_versions(),
    }
    lineage_path = snapshot.parent / "doe_lineage.json"
    lineage_path.write_text(
        json.dumps(lineage, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )

    if preflight_only:
        return {
            "run_id": run_id,
            "sample_id": sample_id,
            "run_snapshot": str(snapshot),
            "preflight": preflight,
            "lineage": lineage,
            "executed": False,
        }

    command = [
        sys.executable,
        str(PROJECT_ROOT / "runner.py"),
        str(snapshot),
        "--run-id",
        run_id,
        "--case-id",
        sample_id,
    ]
    if events_jsonl:
        command.append("--events-jsonl")
    environment = os.environ.copy()
    environment["BATCH_RUNNER_ARTIFACT_ROOT"] = str(package_root)
    environment["BATCH_RUNNER_DOE_LINEAGE_FILE"] = str(lineage_path)
    completed = subprocess.run(
        command, cwd=PROJECT_ROOT, env=environment, check=False
    )
    if completed.returncode:
        raise RuntimeError(
            f"batch runner failed for {sample_id} with exit code {completed.returncode}"
        )
    return {
        "run_id": run_id,
        "sample_id": sample_id,
        "run_snapshot": str(snapshot),
        "preflight": preflight,
        "lineage": lineage,
        "executed": True,
        "returncode": completed.returncode,
    }


def launch_all(
    manifest_path: str | Path,
    *,
    preflight_only: bool = False,
    events_jsonl: bool = False,
) -> list[dict[str, Any]]:
    package_root, manifest = load_manifest(manifest_path)
    samples = [
        record["sample_id"]
        for record in read_ledger(package_root, manifest)
        if record.get("outcome") == "accepted"
    ]
    return [
        launch_sample(
            manifest_path,
            sample_id,
            preflight_only=preflight_only,
            events_jsonl=events_jsonl,
        )
        for sample_id in samples
    ]

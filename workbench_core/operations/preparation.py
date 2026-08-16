"""Validated run preparation and study lineage projection."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from uuid import uuid4

from workbench_core.documents import CaseDocument
from workbench_core.fingerprints import operational_fingerprint, sha256_file
from workbench_core.run_records import save_run_record, transition_run
from workbench_core.schemas.common import utc_now
from workbench_core.schemas.run_record import (
    OutputCompleteness,
    RunRecord,
    RunState,
    SourceCaseIdentity,
)
from workbench_core.validation import validate_case


def prepare_run(
    source_case: str | Path,
    project_root: str | Path,
    solver_prefix: str | Path,
    *,
    conda_executable: str | Path | None = None,
    study_id: str | None = None,
    scenario_group: str | None = None,
    sample_id: str | None = None,
    replicate_of_run_id: str | None = None,
) -> RunRecord:
    """Create the final snapshot, preflight it, and persist a ready or blocked run."""
    source = Path(source_case).resolve()
    root = Path(project_root).resolve()
    run_id = str(uuid4())
    case_id = source.stem
    run_dir = root / "runs" / _slug(case_id) / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    snapshot = run_dir / "run_case.yaml"
    document = CaseDocument.load(source)
    document.assert_runnable()
    document.patch(("paths", "output_dir"), str((run_dir / "results").resolve()))
    revision = document.save(snapshot)
    now = utc_now()
    record = RunRecord(
        run_schema_version="1.0",
        run_id=run_id,
        case_id=case_id,
        source_case=SourceCaseIdentity(path=_logical_path(source, root), sha256=sha256_file(source)),
        snapshot_path=str(snapshot),
        snapshot_sha256=revision.sha256,
        scientific_fingerprint=None,
        operational_fingerprint=operational_fingerprint(
            {
                "run_id": run_id,
                "run_directory": str(run_dir),
                "output_dir": str(run_dir / "results"),
                "study_id": study_id,
                "scenario_group": scenario_group,
            }
        ),
        state=RunState.CREATED,
        created_at_utc=now,
        updated_at_utc=now,
        study_id=study_id,
        scenario_group=scenario_group,
        sample_id=sample_id,
        replicate_of_run_id=replicate_of_run_id,
        result_package_path=str(run_dir / "results"),
        output_completeness=OutputCompleteness(status="not_written"),
    )
    record_path = run_dir / "run_record.json"
    save_run_record(record_path, record)
    record = transition_run(record, RunState.VALIDATING)
    save_run_record(record_path, record)
    try:
        receipt, receipt_path = validate_case(
            snapshot,
            root,
            solver_prefix,
            root / ".workbench" / "validations",
            conda_executable=conda_executable,
        )
        local_receipt = _copy_validation_evidence(receipt_path, run_dir)
        if receipt.validated_snapshot_sha256 != record.snapshot_sha256:
            raise ValueError("validation receipt snapshot hash does not match the final run snapshot")
        target = RunState.READY if receipt.ready else RunState.BLOCKED_PREFLIGHT
        record = transition_run(
            record,
            target,
            status_reason=None if receipt.ready else "; ".join(receipt.errors),
            updates={
                "scientific_fingerprint": receipt.scientific_fingerprint,
                "validation_receipt_path": str(local_receipt),
            },
        )
    except Exception as error:
        record = transition_run(
            record,
            RunState.CONTROLLER_FAILURE,
            status_reason=f"preflight controller failure: {error}",
        )
    save_run_record(record_path, record)
    return record


def prepare_study_sample(
    manifest_path: str | Path,
    sample_id: str,
    project_root: str | Path,
    solver_prefix: str | Path,
    *,
    conda_executable: str | Path | None = None,
    expected_case: str | Path | None = None,
    scenario_group: str | None = None,
) -> RunRecord:
    """Prepare one generated study sample with durable study/run lineage."""
    from workbench_core.schemas.study_spec import StudyManifest
    from workbench_core.studies import update_sample_status

    path = Path(manifest_path).resolve()
    manifest = StudyManifest.model_validate_json(path.read_bytes())
    sample = next((item for item in manifest.samples if item.sample_id == sample_id), None)
    if sample is None:
        raise KeyError(f"unknown study sample: {sample_id}")
    if sample.generation_outcome != "generated" or sample.validation_status != "ready":
        raise ValueError("only generated, preflight-ready study samples can be prepared")
    if sample.run_id is not None:
        raise ValueError(f"study sample already has run_id {sample.run_id}")
    if sample.case_path is None:
        raise ValueError("study sample has no generated case path")
    source_case = Path(sample.case_path)
    if not source_case.is_absolute():
        source_case = path.parent / source_case
    source_case = source_case.resolve()
    if expected_case is not None and source_case != Path(expected_case).resolve():
        raise ValueError("selected case does not match the study sample record")
    record = prepare_run(
        source_case,
        project_root,
        solver_prefix,
        conda_executable=conda_executable,
        study_id=manifest.study_id,
        scenario_group=scenario_group,
        sample_id=sample.sample_id,
    )
    update_sample_status(
        path,
        sample.sample_id,
        run_id=record.run_id,
        completion_state=record.state.value,
        qc_state="preflight_ready" if record.state is RunState.READY else "preflight_blocked",
    )
    return record


def synchronise_study_sample(project_root: str | Path, record: RunRecord) -> Path | None:
    """Project one terminal run classification back into its unique study manifest."""
    if not record.study_id or not record.sample_id:
        return None
    from workbench_core.schemas.study_spec import StudyManifest
    from workbench_core.studies import update_sample_status

    matches = []
    for path in Path(project_root).resolve().rglob("study_manifest.json"):
        try:
            manifest = StudyManifest.model_validate_json(path.read_bytes())
        except (OSError, ValueError):
            continue
        sample = next(
            (item for item in manifest.samples if item.sample_id == record.sample_id), None
        )
        if manifest.study_id == record.study_id and sample and sample.run_id == record.run_id:
            matches.append(path)
    if len(matches) != 1:
        raise ValueError(
            f"expected one study manifest for {record.study_id}/{record.sample_id}; found {len(matches)}"
        )
    complete = (
        record.state is RunState.COMPLETED
        and record.output_completeness.status == "complete"
    )
    update_sample_status(
        matches[0],
        record.sample_id,
        run_id=record.run_id,
        completion_state=record.state.value,
        qc_state="complete" if complete else "excluded_from_valid_dataset",
    )
    return matches[0]


def _copy_validation_evidence(receipt_path: Path, run_dir: Path) -> Path:
    destination = run_dir / "validation_receipt.json"
    for source in receipt_path.parent.iterdir():
        if source.is_file():
            shutil.copy2(source, destination if source == receipt_path else run_dir / source.name)
    return destination


def _logical_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.") or "case"

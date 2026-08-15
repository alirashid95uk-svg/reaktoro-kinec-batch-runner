from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from ruamel.yaml import YAML

from workbench_core.comparison import (
    compare_native,
    compatibility_gate,
    reproduce_comparison,
    write_comparison,
)
from workbench_core.datasets import assemble_dataset
from workbench_core.fingerprints import sha256_file
from workbench_core.operations import (
    ProjectControlLock,
    create_queue,
    finalise_external_run,
    mark_external_run_running,
    recover_orphaned_runs,
    synchronise_study_sample,
)
from workbench_core.result_readers import ResultPackage
from workbench_core.reports import generate_report, reproduce_report
from workbench_core.run_index import rebuild_index, search_runs
from workbench_core.run_records import load_run_record, save_run_record, transition_run
from workbench_core.schemas.common import (
    ArtifactIdentity,
    CodeIdentity,
    EnvironmentIdentity,
    SoftwareIdentity,
)
from workbench_core.schemas.run_record import (
    OutputCompleteness,
    RunRecord,
    RunState,
    RunTerminationCategory,
    SourceCaseIdentity,
)
from workbench_core.schemas.study_spec import (
    ConstraintDefinition,
    GeneratedSampleRecord,
    ParameterDefinition,
    ProvenanceRecord,
    StudyExecutionPolicy,
    StudyManifest,
    StudySpec,
)
from workbench_core.schemas.validation_receipt import ValidationReceipt
from workbench_core.studies import (
    _check_constraints,
    generate_study,
    save_study_spec_text,
    validate_study_spec_text,
)
import workbench_core.validation as validation_module
import workbench_core.operations as operations_module


H0 = "0" * 64
H1 = "1" * 64
H2 = "2" * 64
NOW = datetime(2026, 8, 5, tzinfo=timezone.utc)
CODE = CodeIdentity(commit="test", dirty=False, relevant_source_sha256=H0)
SOFTWARE = SoftwareIdentity(
    workbench_version="1.0", python_version="3.11", code_identity=CODE
)


def test_validation_uses_fresh_temporary_output_not_historical_case_output(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "case.yaml"
    source.write_text("case: test\n", encoding="utf-8")
    observed = []

    def reject_after_load(_path, *, output_dir_override=None):
        observed.append(Path(output_dir_override))
        raise ValueError("synthetic schema stop")

    monkeypatch.setattr(validation_module, "load_case", reject_after_load)
    monkeypatch.setattr(
        validation_module,
        "doctor",
        lambda *_args, **_kwargs: {
            "ready": True,
            "checks": [],
            "platform": "nt",
            "solver_environment_identity": {
                "launch_command": ["python"],
                "python_version": "3.11",
                "reaktoro_version": "2.13.0",
                "environment_export_sha256": H0,
                "package_inventory_sha256": H1,
            },
            "code_identity": {
                "commit": "test",
                "dirty": False,
                "relevant_tree_sha256": H2,
            },
        },
    )
    monkeypatch.setattr(
        validation_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr='PREFLIGHT_RESULT:{"ready":false,"failed_stage":"configuration_validation","error_message":"synthetic schema stop"}\n',
        ),
    )
    validation_root = tmp_path / "validations"
    receipt, _ = validation_module.validate_case(
        source, tmp_path, tmp_path / "solver", validation_root
    )
    assert not receipt.ready
    assert len(observed) == 1
    assert observed[0].name == "preflight_output"
    assert observed[0].parent.parent == validation_root


def test_study_composition_and_dependency_constraints_reject_without_silent_repair() -> None:
    fraction_a = ParameterDefinition(
        parameter_id="fraction_a",
        yaml_path=("composition", "a"),
        data_type="number",
        scientific_meaning="synthetic closure member A",
        entered_unit=None,
        canonical_unit=None,
        values=(0.4,),
        sampling_distribution="explicit test vector",
        transform="identity",
        provenance_requirement="test fixture",
        constraint_group_membership=("closure",),
    )
    fraction_b = ParameterDefinition(
        **{
            **fraction_a.model_dump(mode="python"),
            "parameter_id": "fraction_b",
            "yaml_path": ("composition", "b"),
            "scientific_meaning": "synthetic closure member B",
        }
    )
    mode = ParameterDefinition(
        parameter_id="mode",
        yaml_path=("co2", "mode"),
        data_type="string",
        scientific_meaning="synthetic conditional selector",
        entered_unit=None,
        canonical_unit=None,
        categories=("fixed_fugacity",),
        sampling_distribution="explicit test category",
        transform="identity",
        provenance_requirement="test fixture",
        constraint_group_membership=("dependency",),
    )
    fugacity = ParameterDefinition(
        parameter_id="fugacity",
        yaml_path=("co2", "fugacity_bar"),
        data_type="number",
        scientific_meaning="synthetic conditional field",
        entered_unit="bar",
        canonical_unit="bar",
        values=(1.0,),
        sampling_distribution="explicit test vector",
        transform="identity",
        provenance_requirement="test fixture",
        constraint_group_membership=("dependency",),
    )
    specification = SimpleNamespace(
        parameters=(fraction_a, fraction_b, mode, fugacity),
        constraint_groups=(
            ConstraintDefinition(
                constraint_id="closure",
                constraint_type="composition_closure",
                parameter_ids=("fraction_a", "fraction_b"),
                settings={"closure_total": 1.0, "tolerance": 0.0, "repair_policy": "reject"},
            ),
            ConstraintDefinition(
                constraint_id="dependency",
                constraint_type="dependency",
                parameter_ids=("mode", "fugacity"),
                settings={
                    "if_parameter": "mode",
                    "equals": "fixed_fugacity",
                    "require_parameters": ["fugacity"],
                },
            ),
        ),
        cross_parameter_constraints=(),
    )
    document = {
        "composition": {"a": 0.4, "b": 0.4},
        "co2": {"mode": "fixed_fugacity"},
    }
    outcomes = _check_constraints(document, specification)
    assert [outcome.passed for outcome in outcomes] == [False, False]
    assert document["composition"] == {"a": 0.4, "b": 0.4}


def _package(
    root: Path,
    run_id: str,
    fingerprint: str,
    *,
    state: str = "completed",
    study_id: str | None = None,
) -> ResultPackage:
    output = root / run_id / "results"
    output.mkdir(parents=True)
    (output / "timeseries.csv").write_text(
        "time_s,pH,ionic_strength_molal,unknown_future_column\n"
        "0,7.0,0.1,99\n10,6.5,0.2,100\n",
        encoding="utf-8",
    )
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "output_schema_version": "objective1_audit_v4",
                "output_files": ["timeseries.csv", "manifest.json", "diagnostics.json"],
                "time_semantics": {"duration_s": 10.0},
                "run_identity": {"run_id": run_id, "simulation_completed": True},
                "scientific_fingerprint": fingerprint,
                "input_snapshot": {
                    "physical_conditions": {
                        "pressure_bar": 100.0 if fingerprint == H0 else 110.0
                    }
                },
                "traceability": {"database_sha256": fingerprint},
            }
        ),
        encoding="utf-8",
    )
    (output / "diagnostics.json").write_text(
        json.dumps(
            {
                "output_schema_version": "objective1_audit_v4",
                "simulation_completed": True,
                "output_completeness": {"status": "complete"},
                "quality": {"mass_error": 1e-9},
            }
        ),
        encoding="utf-8",
    )
    (root / run_id / "run_record.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "scientific_fingerprint": fingerprint,
                "state": state,
                "termination_category": state,
                "study_id": study_id,
                "output_completeness": {
                    "status": "complete" if state == "completed" else "partial"
                },
            }
        ),
        encoding="utf-8",
    )
    return ResultPackage(output)


def test_result_authority_comparison_and_sqlite_projection(tmp_path: Path) -> None:
    first = _package(tmp_path / "runs", "run-a", H0)
    second = _package(tmp_path / "runs", "run-b", H1)
    assert first.status.interpretation_supported
    assert "unknown_future_column" not in first.quantity_descriptors()
    gate = compatibility_gate([first, second], "pH")
    assert gate["compatible"]
    assert "physical_conditions.pressure_bar" in gate["scientific_input_differences"]
    final = compare_native([first, second], "pH", mode="final_state")
    assert final["time_s"].tolist() == [10, 10]
    assert final["absolute_difference_from_reference"].tolist() == [0.0, 0.0]
    assert final["relative_difference_from_reference"].tolist() == [0.0, 0.0]

    interrupted = _package(
        tmp_path / "runs", "run-interrupted", H2, state="interrupted_by_host"
    )
    assert not interrupted.status.interpretation_supported
    assert not compatibility_gate([first, interrupted], "pH")["compatible"]

    index = tmp_path / "index.sqlite"
    assert rebuild_index(index, tmp_path / "runs") == 3
    assert search_runs(index, status="completed")[0]["run_id"] in {"run-a", "run-b"}
    assert search_runs(index, status="interrupted_by_host")[0]["run_id"] == "run-interrupted"


def test_result_reader_rejects_conflicting_completion_evidence(tmp_path: Path) -> None:
    package = _package(tmp_path / "runs", "run-conflict", H0)
    diagnostics = json.loads((package.path / "diagnostics.json").read_text(encoding="utf-8"))
    diagnostics["simulation_completed"] = False
    (package.path / "diagnostics.json").write_text(json.dumps(diagnostics), encoding="utf-8")

    status = ResultPackage(package.path).status
    assert not status.interpretation_supported
    assert status.reason == "conflicting simulation-completion evidence"


def test_report_reproduces_from_hash_identified_sources(tmp_path: Path) -> None:
    source = tmp_path / "sources" / "run_record.json"
    source.parent.mkdir()
    source.write_text(json.dumps({"run_id": "report-run", "termination_category": "completed"}), encoding="utf-8")
    original = generate_report(
        "run", [source], tmp_path / "original", software_identity=SOFTWARE.model_dump(mode="json")
    )
    reproduced = reproduce_report(original["spec"], [source.parent], tmp_path / "reproduced")

    for name in ("markdown", "html"):
        assert sha256_file(original[name]) == sha256_file(reproduced[name]), name
    assert reproduced["pdf"].stat().st_size > 0


def test_derived_writers_reject_source_evidence_directories(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    first = _package(runs, "run-a", H0)
    second = _package(runs, "run-b", H1)
    auditor = tmp_path / "auditor.py"
    auditor.write_text("def audit(path):\n    return {'ok': True}\n", encoding="utf-8")

    for output in (first.path, first.path / "derived-comparison"):
        with pytest.raises(ValueError, match="outside immutable source directory"):
            write_comparison(
                output,
                [first, second],
                "pH",
                software_identity=SOFTWARE,
            )

    specification, _data = write_comparison(
        tmp_path / "safe-comparison",
        [first, second],
        "pH",
        software_identity=SOFTWARE,
    )
    with pytest.raises(ValueError, match="outside immutable source directory"):
        reproduce_comparison(
            specification,
            runs,
            output_path=first.path / "reproduced-comparison.csv",
        )

    dataset_arguments = {
        "dataset_type": "final_state",
        "features": ["pH"],
        "targets": ["ionic_strength_molal"],
        "auditor_path": auditor,
        "software_identity": SOFTWARE,
    }
    for output in (first.path, first.path / "derived-dataset"):
        with pytest.raises(ValueError, match="outside immutable source directory"):
            assemble_dataset([first], output, **dataset_arguments)

    source = first.path / "manifest.json"
    for output in (first.path, first.path / "derived-report"):
        with pytest.raises(ValueError, match="outside immutable source directory"):
            generate_report(
                "run",
                [source],
                output,
                software_identity=SOFTWARE.model_dump(mode="json"),
            )

    assert not (first.path / "derived-comparison").exists()
    assert not (first.path / "reproduced-comparison.csv").exists()
    assert not (first.path / "derived-dataset").exists()
    assert not (first.path / "derived-report").exists()


def test_exact_common_timestamp_comparison_rejects_empty_alignment(tmp_path: Path) -> None:
    first = _package(tmp_path / "runs", "run-a", H0)
    second = _package(tmp_path / "runs", "run-b", H1)
    (second.path / "timeseries.csv").write_text(
        "time_s,pH,ionic_strength_molal,unknown_future_column\n1,7.0,0.1,x\n9,6.0,0.2,y\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="no exact common timestamps"):
        compare_native([first, ResultPackage(second.path)], "pH", mode="exact_common_timestamps")

    manifest = json.loads((second.path / "manifest.json").read_text(encoding="utf-8"))
    manifest["time_semantics"]["duration_s"] = 9.0
    (second.path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="common requested endpoint"):
        compare_native([first, ResultPackage(second.path)], "pH", mode="final_state")


def test_dataset_requires_audit_qc_and_keeps_replicates_in_one_split(tmp_path: Path) -> None:
    first = _package(tmp_path / "runs", "run-a", H0)
    replicate = _package(tmp_path / "runs", "run-b", H0)
    auditor = tmp_path / "auditor.py"
    auditor.write_text("def audit(path):\n    return {'ok': True, 'mass_error': 1e-9}\n", encoding="utf-8")
    outputs = assemble_dataset(
        [first, replicate],
        tmp_path / "dataset",
        dataset_type="final_state",
        features=["pH"],
        targets=["ionic_strength_molal"],
        auditor_path=auditor,
        duplicate_policy="allow_replicates",
        qc_requirements={"audit.mass_error": {"operator": "abs_le", "value": 1e-8}},
        software_identity=SOFTWARE,
    )
    rows = pd.read_csv(outputs["csv"])
    assert len(rows) == 2
    assert rows["split"].nunique() == 1
    manifest = json.loads(outputs["manifest"].read_text(encoding="utf-8"))
    assert manifest["split_definition"]["proportions"] == {
        "test": 0.15,
        "train": 0.7,
        "validation": 0.15,
    }

    bad_auditor = tmp_path / "bad_auditor.py"
    bad_auditor.write_text("def audit(path):\n    return {'ok': False, 'errors': ['bad package']}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no source run passed"):
        assemble_dataset(
            [first],
            tmp_path / "rejected",
            dataset_type="final_state",
            features=["pH"],
            targets=["ionic_strength_molal"],
            auditor_path=bad_auditor,
            software_identity=SOFTWARE,
        )


def test_study_run_status_and_dataset_export_are_linked_to_manifest(
    tmp_path: Path, monkeypatch
) -> None:
    package = _package(tmp_path / "runs", "run-a", H0, study_id="study-1")
    sample = GeneratedSampleRecord(
        study_id="study-1",
        sample_id="sample-1",
        baseline_case_sha256=H1,
        input_parameter_vector={},
        canonical_parameter_vector={},
        constraint_outcomes=(),
        generation_outcome="generated",
        case_path="case.yaml",
        case_sha256=H2,
        scientific_fingerprint=H0,
        deliberate_replicate=False,
        validation_status="ready",
        validation_receipt_path="receipt.json",
        run_id="run-a",
        completion_state="ready",
        qc_state="preflight_ready",
    )
    manifest_path = tmp_path / "study_manifest.json"
    manifest = StudyManifest(
        study_manifest_schema_version="1.0",
        study_id="study-1",
        study_name="Linked study",
        created_at_utc=NOW,
        finalised_at_utc=NOW,
        specification_sha256=H0,
        generator_version="1.0",
        sampling_method="existing_cases",
        seed=0,
        samples=(sample,),
        required_outputs=("timeseries.csv",),
        validity_domain={"purpose": "test"},
        dataset_exports=(),
        ready=True,
    )
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
    record = RunRecord.model_validate(
        {
            **json.loads((package.path.parent / "run_record.json").read_text(encoding="utf-8")),
            "run_schema_version": "1.0",
            "case_id": "case",
            "source_case": {"path": "case.yaml", "sha256": H1},
            "snapshot_path": str(tmp_path / "snapshot.yaml"),
            "snapshot_sha256": H2,
            "operational_fingerprint": H1,
            "created_at_utc": NOW,
            "updated_at_utc": NOW,
            "state": RunState.COMPLETED,
            "termination_category": RunTerminationCategory.COMPLETED,
            "finished_at_utc": NOW,
            "sample_id": "sample-1",
            "result_package_path": str(package.path),
        }
    )
    assert synchronise_study_sample(tmp_path, record) == manifest_path
    updated = StudyManifest.model_validate_json(manifest_path.read_bytes())
    assert updated.samples[0].completion_state == "completed"
    assert updated.samples[0].qc_state == "complete"

    auditor = tmp_path / "auditor.py"
    auditor.write_text("def audit(path):\n    return {'ok': True}\n", encoding="utf-8")
    outputs = assemble_dataset(
        [package],
        tmp_path / "study-dataset",
        dataset_type="final_state",
        features=["pH"],
        targets=["ionic_strength_molal"],
        auditor_path=auditor,
        source_study_manifest=manifest_path,
        software_identity=SOFTWARE,
    )
    linked = StudyManifest.model_validate_json(manifest_path.read_bytes())
    assert linked.dataset_exports[0].sha256 == sha256_file(outputs["manifest"])

    unprepared_path = tmp_path / "unprepared_manifest.json"
    unprepared_path.write_text(
        manifest.model_copy(
            update={
                "samples": (
                    sample.model_copy(
                        update={"run_id": None, "completion_state": None, "qc_state": None}
                    ),
                )
            }
        ).model_dump_json(),
        encoding="utf-8",
    )
    ready_path = _ready_record(tmp_path / "prepared", "prepared-run", H0)
    ready = load_run_record(ready_path).model_copy(
        update={"study_id": "study-1", "sample_id": "sample-1"}
    )
    calls = []

    def fake_prepare(case, root, solver, **kwargs):
        calls.append((Path(case), kwargs["study_id"], kwargs["sample_id"]))
        return ready

    monkeypatch.setattr(operations_module, "prepare_run", fake_prepare)
    operations_module.prepare_study_sample(
        unprepared_path,
        "sample-1",
        tmp_path,
        tmp_path / "solver",
        expected_case=tmp_path / "case.yaml",
    )
    prepared_manifest = StudyManifest.model_validate_json(unprepared_path.read_bytes())
    assert calls[0][1:] == ("study-1", "sample-1")
    assert prepared_manifest.samples[0].run_id == "prepared-run"


def _ready_record(path: Path, run_id: str, fingerprint: str) -> Path:
    path.mkdir(parents=True)
    snapshot = path / "run_case.yaml"
    snapshot.write_text("case: test\n", encoding="utf-8")
    evidence = path / "solver_environment_evidence.json"
    evidence.write_text("{}", encoding="utf-8")
    receipt_path = path / "validation_receipt.json"
    receipt = ValidationReceipt.model_validate(
        {
            "receipt_schema_version": "1.0",
            "receipt_id": f"receipt-{run_id}",
            "created_at_utc": NOW,
            "case_name": "case",
            "validated_snapshot_sha256": H0,
            "scientific_fingerprint": fingerprint,
            "operational_fingerprint": H1,
            "configuration_schema_version": "case-v1",
            "runner_version": "runner-v1",
            "worker_protocol_version": "1.0",
            "solver_environment_identity": {
                "python_version": "3.11",
                "reaktoro_version": "2.13.0",
                "platform": "windows",
                "environment_spec_sha256": H0,
                "package_inventory_sha256": H0,
            },
            "environment_evidence": {"path": evidence.name, "sha256": sha256_file(evidence)},
            "code_identity": CODE.model_dump(mode="python"),
            "dependency_identities": (),
            "preflight_stage_results": ({"stage": "schema", "status": "passed"},),
            "kinetic_mapping_summary": (),
            "ready": True,
        }
    )
    receipt_path.write_text(receipt.model_dump_json(), encoding="utf-8")
    record = RunRecord(
        run_schema_version="1.0",
        run_id=run_id,
        case_id="case",
        source_case=SourceCaseIdentity(path="case.yaml", sha256=H0),
        snapshot_path=str(snapshot),
        snapshot_sha256=H0,
        scientific_fingerprint=fingerprint,
        operational_fingerprint=H1,
        state=RunState.READY,
        created_at_utc=NOW,
        updated_at_utc=NOW,
        validation_receipt_path=str(receipt_path),
        result_package_path=str(path / "results"),
        output_completeness=OutputCompleteness(status="not_written"),
    )
    record_path = path / "run_record.json"
    save_run_record(record_path, record)
    return record_path


def test_queue_warns_for_completed_and_study_duplicates(tmp_path: Path) -> None:
    current = _ready_record(tmp_path / "runs" / "current", "current", H0)
    completed_path = tmp_path / "runs" / "completed"
    completed_record_path = _ready_record(completed_path, "completed", H0)
    completed = RunRecord.model_validate_json(completed_record_path.read_bytes()).model_copy(
        update={
            "state": RunState.COMPLETED,
            "finished_at_utc": NOW,
            "termination_category": RunTerminationCategory.COMPLETED,
            "output_completeness": OutputCompleteness(status="complete"),
        }
    )
    save_run_record(completed_record_path, completed)
    queue = create_queue(
        [current], tmp_path / "queue.json", project_root=tmp_path
    )
    assert "duplicate scientific fingerprint of completed" in str(queue.entries[0].status_reason)


def test_recovery_accepts_complete_manifest_when_diagnostics_are_disabled(tmp_path: Path) -> None:
    record_path = _ready_record(tmp_path / "runs" / "orphan", "orphan", H0)
    record = RunRecord.model_validate_json(record_path.read_bytes()).model_copy(
        update={"state": RunState.RUNNING, "started_at_utc": NOW}
    )
    results = Path(record.result_package_path)
    results.mkdir()
    (results / "timeseries.csv").write_text("time_s,pH\n0,7\n", encoding="utf-8")
    (results / "manifest.json").write_text(
        json.dumps(
            {
                "run_identity": {"simulation_completed": True},
                "output_files": ["manifest.json", "timeseries.csv"],
            }
        ),
        encoding="utf-8",
    )
    save_run_record(record_path, record)
    recovered = recover_orphaned_runs(tmp_path / "runs")
    assert recovered[0].state is RunState.COMPLETED
    assert recovered[0].output_completeness.status == "complete"


def test_recovery_rejects_disagreeing_output_and_event_completion(tmp_path: Path) -> None:
    record_path = _ready_record(tmp_path / "runs" / "orphan-conflict", "orphan-conflict", H0)
    record = RunRecord.model_validate_json(record_path.read_bytes()).model_copy(
        update={"state": RunState.RUNNING, "started_at_utc": NOW}
    )
    results = Path(record.result_package_path)
    results.mkdir()
    (results / "manifest.json").write_text(
        json.dumps({"run_identity": {"simulation_completed": True}, "output_files": ["manifest.json"]}),
        encoding="utf-8",
    )
    (record_path.parent / "events.jsonl").write_text(
        json.dumps(
            {
                "protocol_version": "1.0",
                "event_type": "simulation_finished",
                "timestamp_utc": NOW.isoformat(),
                "run_id": record.run_id,
                "case_id": record.case_id,
                "sequence_number": 1,
                "producer": "worker",
                "payload": {"simulation_completed": False},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    save_run_record(record_path, record)

    recovered = recover_orphaned_runs(tmp_path / "runs")
    assert recovered[0].state is RunState.INDETERMINATE
    assert "disagrees" in str(recovered[0].status_reason)


def test_control_lock_ignores_stale_file_but_rejects_a_live_owner(tmp_path: Path) -> None:
    stale = tmp_path / ".workbench" / "control.lock"
    stale.parent.mkdir()
    stale.write_text('{"pid": 999999, "token": "stale"}', encoding="utf-8")
    with ProjectControlLock(tmp_path):
        with pytest.raises(RuntimeError, match="another workbench controller"):
            with ProjectControlLock(tmp_path):
                pass
    with ProjectControlLock(tmp_path):
        pass


def test_control_lock_allows_only_the_owner_token_to_delegate(tmp_path: Path, monkeypatch) -> None:
    with ProjectControlLock(tmp_path) as owner:
        monkeypatch.setenv("REAKTORO_PROJECT_CONTROL_TOKEN", owner.token)
        with ProjectControlLock(tmp_path):
            pass
        monkeypatch.setenv("REAKTORO_PROJECT_CONTROL_TOKEN", "wrong-token")
        with pytest.raises(RuntimeError, match="another workbench controller"):
            with ProjectControlLock(tmp_path):
                pass


@pytest.mark.parametrize(
    ("event_type", "expected_state"),
    [("kill_confirmed", RunState.FORCE_TERMINATED), ("kill_failed", RunState.CONTROLLER_FAILURE)],
)
def test_gui_owned_force_termination_requires_durable_confirmation(
    tmp_path: Path, event_type: str, expected_state: RunState
) -> None:
    record_path = _ready_record(tmp_path / "runs" / "forced", "forced", H0)
    record = RunRecord.model_validate_json(record_path.read_bytes())
    record = transition_run(record, RunState.STARTING)
    save_run_record(record_path, record)
    record = mark_external_run_running(
        record_path,
        child_pid=12345,
        executable="conda",
        command=("conda", "run", "python", "runner.py"),
    )
    event_path = record_path.parent / "events.jsonl"
    event_path.write_text(
        json.dumps(
            {
                "protocol_version": "1.0",
                "event_type": event_type,
                "timestamp_utc": NOW.isoformat(),
                "run_id": record.run_id,
                "case_id": record.case_id,
                "sequence_number": 1,
                "producer": "controller",
                "payload": {"pid": 12345},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    final = finalise_external_run(
        record_path, event_path, return_code=1, force_requested=True
    )
    assert final.state is expected_state
    assert final.output_completeness.status == "not_written"


def test_study_grid_generation_is_deterministic_and_preflight_gated(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[2]
    baseline = tmp_path / "baseline.yaml"
    baseline.write_bytes(
        (repository / "cases" / "calcite_quartz_illite_fixed_fugacity_legacy.yaml").read_bytes()
    )
    spec = StudySpec(
        study_schema_version="1.0",
        study_id="study-1",
        study_name="Temperature study",
        baseline_case_path=baseline.name,
        baseline_case_sha256=sha256_file(baseline),
        baseline_scientific_fingerprint=H0,
        sampling_method="grid",
        seed=17,
        sample_count=1,
        parameters=(
            ParameterDefinition(
                parameter_id="temperature",
                yaml_path=("physical", "temperature_c"),
                data_type="number",
                scientific_meaning="batch temperature",
                entered_unit="degC",
                canonical_unit="degC",
                values=(50.0,),
                sampling_distribution="explicit grid",
                transform="identity",
                provenance_requirement="user supplied",
                constraint_group_membership=(),
            ),
        ),
        constraint_groups=(),
        cross_parameter_constraints=(),
        generated_case_directory="generated",
        execution_policy=StudyExecutionPolicy(
            max_workers=1, failure_policy="stop_after_failure", allow_replicates=False
        ),
        required_outputs=("manifest.json", "diagnostics.json", "timeseries.csv"),
        validity_domain={"temperature_c": [50.0, 50.0]},
        provenance=(
            ProvenanceRecord(
                subject="temperature",
                origin="user_decision",
                reference="test fixture",
            ),
        ),
    )
    spec_path = tmp_path / "study.yaml"
    yaml = YAML()
    with spec_path.open("w", encoding="utf-8") as stream:
        yaml.dump(spec.model_dump(mode="json"), stream)

    calls: list[Path] = []

    def preflight(case: Path) -> dict[str, object]:
        calls.append(case)
        return {
            "ready": True,
            "receipt_path": str(tmp_path / "receipt.json"),
            "scientific_fingerprint": H0,
        }

    manifest_path = generate_study(spec_path, preflight=preflight)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(calls) == 2
    assert manifest["ready"] is True
    assert manifest["samples"][0]["validation_status"] == "ready"
    generated = manifest_path.parent / manifest["samples"][0]["case_path"]
    assert generated.is_file()

    text = spec_path.read_text(encoding="utf-8")
    assert validate_study_spec_text(text).study_id == "study-1"
    original_hash = sha256_file(spec_path)
    save_study_spec_text(spec_path, text, expected_sha256=original_hash)
    spec_path.write_text(text + "\n# external change\n", encoding="utf-8")
    with pytest.raises(ValueError, match="changed outside"):
        save_study_spec_text(spec_path, text, expected_sha256=original_hash)

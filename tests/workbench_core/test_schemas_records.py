from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from workbench_core.persistence import append_jsonl
from workbench_core.queue_records import (
    InvalidQueueTransition,
    transition_queue,
    transition_queue_entry,
)
from workbench_core.run_records import (
    InvalidRunTransition,
    load_run_record,
    save_run_record,
    transition_run,
)
from workbench_core.schemas.common import (
    ArtifactIdentity,
    CodeIdentity,
    DependencyIdentity,
    EnvironmentIdentity,
    QuantityDefinition,
    SoftwareIdentity,
)
from workbench_core.schemas.comparison_spec import ComparisonSpec
from workbench_core.schemas.dataset_manifest import (
    DatasetArtifact,
    DatasetManifest,
    DatasetSourceRun,
    SplitDefinition,
)
from workbench_core.schemas.queue_record import (
    QueueEntry,
    QueueEntryState,
    QueueRecord,
    QueueState,
    WorkerPolicy,
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
    NumericRange,
    ParameterDefinition,
    ProvenanceRecord,
    StudyExecutionPolicy,
    StudyManifest,
    StudySpec,
)
from workbench_core.schemas.validation_receipt import (
    KineticMappingResult,
    PreflightStageResult,
    ValidationReceipt,
)


H0 = "0" * 64
H1 = "1" * 64
NOW = datetime(2026, 8, 5, tzinfo=timezone.utc)
CODE = CodeIdentity(commit="abc123", dirty=False, relevant_source_sha256=H0)
SOFTWARE = SoftwareIdentity(
    workbench_version="1.0",
    python_version="3.11.15",
    code_identity=CODE,
)
ENVIRONMENT = EnvironmentIdentity(
    python_version="3.11.15",
    reaktoro_version="2.13.0",
    platform="windows",
    environment_spec_sha256=H0,
    package_inventory_sha256=H0,
)


def test_validation_receipt_is_strict_versioned_and_coherent() -> None:
    receipt = ValidationReceipt(
        receipt_schema_version="1.0",
        receipt_id="receipt-1",
        created_at_utc=NOW,
        case_name="case",
        validated_snapshot_sha256=H0,
        scientific_fingerprint=H0,
        operational_fingerprint=H1,
        configuration_schema_version="case-v1",
        runner_version="runner-v1",
        worker_protocol_version="1.0",
        solver_environment_identity=ENVIRONMENT,
        environment_evidence=ArtifactIdentity(path="solver_environment_evidence.json", sha256=H0),
        code_identity=CODE,
        dependency_identities=(
            DependencyIdentity(logical_name="database", sha256=H0, source="local"),
            DependencyIdentity(
                logical_name="embedded_phreeqc_database",
                sha256=None,
                source="reaktoro_package",
                version="2.13.0",
                package_build="conda-forge package build",
                hash_unavailable_reason="embedded resource has no standalone file hash",
            ),
        ),
        preflight_stage_results=(PreflightStageResult(stage="schema", status="passed"),),
        kinetic_mapping_summary=(
            KineticMappingResult(
                mineral_name="Calcite",
                kinetic_model="palandri_kharaka",
                parameter_record="Calcite",
                surface_area_present=True,
                mapped=True,
            ),
        ),
        ready=True,
    )
    assert receipt.validated_snapshot_sha256 == H0
    failed = ValidationReceipt(
        receipt_schema_version="1.0",
        receipt_id="receipt-failed",
        created_at_utc=NOW,
        case_name=None,
        validated_snapshot_sha256=H0,
        scientific_fingerprint=None,
        operational_fingerprint=H1,
        configuration_schema_version="case-v1",
        runner_version="runner-v1",
        worker_protocol_version="1.0",
        solver_environment_identity=ENVIRONMENT,
        environment_evidence=ArtifactIdentity(path="solver_environment_evidence.json", sha256=H0),
        code_identity=CODE,
        dependency_identities=(),
        preflight_stage_results=(
            PreflightStageResult(
                stage="schema",
                status="failed",
                errors=("unknown field",),
            ),
        ),
        kinetic_mapping_summary=(),
        ready=False,
        failed_stage="schema",
        errors=("unknown field",),
    )
    assert failed.scientific_fingerprint is None
    with pytest.raises(ValidationError):
        ValidationReceipt.model_validate({**receipt.model_dump(mode="python"), "unexpected": True})
    with pytest.raises(ValidationError):
        ValidationReceipt.model_validate(
            {**receipt.model_dump(mode="python"), "receipt_schema_version": "2.0"}
        )


def _run_record() -> RunRecord:
    return RunRecord(
        run_schema_version="1.0",
        run_id="run-1",
        case_id="case-1",
        source_case=SourceCaseIdentity(path="cases/case.yaml", sha256=H0),
        snapshot_path="runs/run-1/run_case.yaml",
        snapshot_sha256=H0,
        scientific_fingerprint=H0,
        operational_fingerprint=H1,
        state=RunState.CREATED,
        created_at_utc=NOW,
        updated_at_utc=NOW,
        scenario_group="caprock-family-a",
        result_package_path="runs/run-1/results",
        output_completeness=OutputCompleteness(status="not_written"),
    )


def test_run_and_queue_state_transitions_and_atomic_persistence(tmp_path: Path) -> None:
    assert {state.value for state in QueueEntryState} == {
        "planned",
        "queued",
        "starting",
        "running",
        "pause_after_current_requested",
        "cancel_after_current_requested",
        "cancelled_before_start",
        "finished",
    }
    assert {
        RunState.SOLVER_FAILURE_AT_START,
        RunState.CONTROLLER_FAILURE,
        RunState.INDETERMINATE,
    }.issubset(set(RunState))
    run = _run_record()
    assert run.scenario_group == "caprock-family-a"
    with pytest.raises(InvalidRunTransition):
        transition_run(run, RunState.COMPLETED)
    run = transition_run(run, RunState.VALIDATING, at_utc=NOW + timedelta(seconds=1))
    run = transition_run(run, RunState.READY, at_utc=NOW + timedelta(seconds=2))
    run = transition_run(run, RunState.STARTING, at_utc=NOW + timedelta(seconds=3))
    run = transition_run(run, RunState.RUNNING, at_utc=NOW + timedelta(seconds=4))
    interrupted_output = transition_run(
        run,
        RunState.CHEMISTRY_COMPLETED_OUTPUT_INCOMPLETE,
        at_utc=NOW + timedelta(seconds=5),
        updates={
            "output_completeness": OutputCompleteness(status="partial"),
            "termination_category": RunTerminationCategory.INTERRUPTED_DURING_OUTPUT,
        },
    )
    assert (
        interrupted_output.termination_category
        is RunTerminationCategory.INTERRUPTED_DURING_OUTPUT
    )
    run = transition_run(
        run,
        RunState.COMPLETED,
        at_utc=NOW + timedelta(seconds=5),
        updates={"output_completeness": OutputCompleteness(status="complete")},
    )
    assert run.finished_at_utc == NOW + timedelta(seconds=5)
    assert run.termination_category.value == "completed"
    path = tmp_path / "run_record.json"
    save_run_record(path, run)
    assert load_run_record(path) == run
    assert not list(tmp_path.glob(".run_record.json.*.tmp"))

    entry = QueueEntry(
        entry_id="entry-1",
        order=0,
        run_id="run-1",
        snapshot_path="runs/run-1/run_case.yaml",
        snapshot_sha256=H0,
        scientific_fingerprint=H0,
        validation_receipt_id="receipt-1",
        entry_state=QueueEntryState.PLANNED,
    )
    queue = QueueRecord(
        queue_schema_version="1.0",
        queue_id="queue-1",
        created_at_utc=NOW,
        updated_at_utc=NOW,
        failure_policy="stop_after_failure",
        worker_policy=WorkerPolicy(max_workers=1),
        queue_state=QueueState.CREATED,
        entries=(entry,),
    )
    queue = transition_queue_entry(queue, "entry-1", QueueEntryState.QUEUED)
    queue = transition_queue(queue, QueueState.READY)
    assert queue.entries[0].entry_state is QueueEntryState.QUEUED
    with pytest.raises(InvalidQueueTransition):
        transition_queue(queue, QueueState.COMPLETED)

    events = tmp_path / "events.jsonl"
    append_jsonl(events, {"sequence": 1})
    append_jsonl(events, {"sequence": 2})
    assert events.read_text(encoding="utf-8").splitlines() == [
        '{"sequence":1}',
        '{"sequence":2}',
    ]


def test_comparison_study_and_dataset_contracts_reject_unsafe_shapes() -> None:
    ComparisonSpec(
        comparison_schema_version="1.0",
        comparison_id="comparison-1",
        created_at_utc=NOW,
        source_run_ids=("run-1",),
        source_schema_versions={"run-1": "objective1_audit_v4"},
        selected_quantities=("pH",),
        unit_conversions=(),
        completion_filters=("complete_outputs_only",),
        time_alignment_mode="native_accepted_grids",
        common_time_tolerance=None,
        interpolation_policy=(),
        extrapolation_policy="forbidden",
        excluded_runs=(),
        created_artifacts=(ArtifactIdentity(path="comparison.csv", sha256=H0),),
        software_identity=SOFTWARE,
    )
    with pytest.raises(ValidationError):
        ComparisonSpec(
            comparison_schema_version="1.0",
            comparison_id="bad",
            created_at_utc=NOW,
            source_run_ids=("run-1",),
            source_schema_versions={"run-1": "objective1_audit_v4"},
            selected_quantities=("pH",),
            unit_conversions=(),
            completion_filters=(),
            time_alignment_mode="interpolation",
            common_time_tolerance=None,
            interpolation_policy=(),
            extrapolation_policy="forbidden",
            excluded_runs=(),
            created_artifacts=(),
            software_identity=SOFTWARE,
        )

    parameter = ParameterDefinition(
        parameter_id="pressure",
        yaml_path=("physical", "pressure_bar"),
        data_type="number",
        scientific_meaning="system pressure",
        entered_unit="bar",
        canonical_unit="bar",
        range=NumericRange(minimum=90.0, maximum=110.0),
        sampling_distribution="uniform",
        transform="identity",
        provenance_requirement="user-approved range",
        constraint_group_membership=(),
    )
    study = StudySpec(
        study_schema_version="1.0",
        study_id="study-1",
        study_name="pressure study",
        baseline_case_path="cases/baseline.yaml",
        baseline_case_sha256=H0,
        baseline_scientific_fingerprint=H0,
        sampling_method="random",
        seed=1,
        sample_count=1,
        parameters=(parameter,),
        constraint_groups=(),
        cross_parameter_constraints=(),
        generated_case_directory="studies/study-1/cases",
        execution_policy=StudyExecutionPolicy(
            max_workers=1,
            failure_policy="stop_after_failure",
            allow_replicates=False,
        ),
        required_outputs=("timeseries.csv",),
        validity_domain={"source": "approved"},
        provenance=(
            ProvenanceRecord(subject="pressure range", origin="user_decision", reference="study spec"),
        ),
    )
    with pytest.raises(ValidationError, match="imported_matrix requires"):
        StudySpec.model_validate(
            {**study.model_dump(mode="python"), "sampling_method": "imported_matrix"}
        )
    with pytest.raises(ValidationError, match="constraint_group_membership"):
        StudySpec.model_validate(
            {
                **study.model_dump(mode="python"),
                "constraint_groups": (
                    ConstraintDefinition(
                        constraint_id="pressure_bounds",
                        constraint_type="bounds",
                        parameter_ids=("pressure",),
                        settings={"minimum": 90.0, "maximum": 110.0},
                    ),
                ),
            }
        )
    StudyManifest(
        study_manifest_schema_version="1.0",
        study_id="study-1",
        study_name="pressure study",
        created_at_utc=NOW,
        finalised_at_utc=NOW,
        specification_sha256=H0,
        generator_version="1.0",
        sampling_method="random",
        seed=1,
        samples=(
            GeneratedSampleRecord(
                study_id="study-1",
                sample_id="sample-1",
                baseline_case_sha256=H0,
                input_parameter_vector={"pressure": 100.0},
                canonical_parameter_vector={"pressure": 100.0},
                constraint_outcomes=(),
                generation_outcome="generated",
                case_path="studies/study-1/cases/sample-1.yaml",
                case_sha256=H0,
                scientific_fingerprint=H0,
                deliberate_replicate=False,
                validation_status="ready",
                validation_receipt_path="study-1/receipts/sample-1.json",
            ),
        ),
        required_outputs=("timeseries.csv",),
        validity_domain={"source": "study-1"},
        dataset_exports=(),
        ready=True,
    )

    quantity = QuantityDefinition(
        quantity_id="pH",
        label="pH",
        scientific_meaning="aqueous pH",
        unit="dimensionless",
        value_type="float",
        sign_domain="nonnegative",
        extent="intensive",
        time_semantics="accepted state",
        source_file="timeseries.csv",
        source_column="pH",
        source_output_schema_version="objective1_audit_v4",
    )
    source = DatasetSourceRun(
        run_id="run-1",
        output_schema_version="objective1_audit_v4",
        scientific_fingerprint=H0,
    )
    split = SplitDefinition(
        group_column="run_id",
        algorithm="preassigned",
        proportions={"train": 1.0, "validation": 0.0, "test": 0.0},
        seed=1,
        run_ids_by_split={"train": ("run-1",), "validation": (), "test": ()},
        excluded_groups=(),
        leakage_checks=("run_id_disjoint",),
    )
    DatasetManifest(
        dataset_schema_version="1.0",
        dataset_id="dataset-1",
        created_at_utc=NOW,
        dataset_type="final_state",
        source_study_id="study-1",
        explicit_run_set_id=None,
        source_runs=(source,),
        features=(quantity,),
        targets=(),
        time_semantics="final native saved state",
        validity_domain={"source": "study-1"},
        completion_qc_filters=("complete",),
        missing_value_policy="reject",
        duplicate_policy="exclude",
        split_definition=split,
        seed=1,
        excluded_runs=(),
        failure_ledger_path="failure.csv",
        artifacts=(DatasetArtifact(format="csv", path="dataset.csv", sha256=H0),),
        software_identity=SOFTWARE,
    )
    with pytest.raises(ValidationError, match="cross dataset splits"):
        SplitDefinition(
            group_column="run_id",
            algorithm="preassigned",
            proportions={"train": 1.0, "validation": 0.0, "test": 0.0},
            seed=1,
            run_ids_by_split={
                "train": ("run-1",),
                "validation": ("run-1",),
                "test": (),
            },
            excluded_groups=(),
            leakage_checks=("run_id_disjoint",),
        )

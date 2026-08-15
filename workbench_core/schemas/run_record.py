"""Strict run record and explicit lifecycle state machine."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from .common import FrozenStrictModel, NonEmptyStr, Sha256, StrictModel


RUN_RECORD_SCHEMA_VERSION = "1.0"


class RunState(str, Enum):
    CREATED = "created"
    VALIDATING = "validating"
    BLOCKED_PREFLIGHT = "blocked_preflight"
    READY = "ready"
    STARTING = "starting"
    RUNNING = "running"
    PARTIAL_NUMERICAL_FAILURE = "partial_numerical_failure"
    SOLVER_FAILURE_AT_START = "solver_failure_at_start"
    CANCELLED_CLEANLY = "cancelled_cleanly"
    CANCEL_REQUESTED_SOLVER_UNRESPONSIVE = "cancel_requested_solver_unresponsive"
    FORCE_TERMINATED = "force_terminated"
    NATIVE_CRASH = "native_crash"
    CONTROLLER_FAILURE = "controller_failure"
    CHEMISTRY_COMPLETED_OUTPUT_INCOMPLETE = "chemistry_completed_output_incomplete"
    INTERRUPTED_BY_HOST = "interrupted_by_host"
    INDETERMINATE = "indeterminate"
    COMPLETED = "completed"


class RunTerminationCategory(str, Enum):
    BLOCKED_PREFLIGHT = "blocked_preflight"
    COMPLETED = "completed"
    CHEMISTRY_COMPLETED_OUTPUT_INCOMPLETE = "chemistry_completed_output_incomplete"
    INTERRUPTED_DURING_OUTPUT = "interrupted_during_output"
    PARTIAL_NUMERICAL_FAILURE = "partial_numerical_failure"
    SOLVER_FAILURE_AT_START = "solver_failure_at_start"
    CANCELLED_CLEANLY = "cancelled_cleanly"
    FORCE_TERMINATED = "force_terminated"
    NATIVE_CRASH = "native_crash"
    CONTROLLER_FAILURE = "controller_failure"
    INTERRUPTED_BY_HOST = "interrupted_by_host"
    INDETERMINATE = "indeterminate"


RUN_STATE_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.CREATED: frozenset(
        {
            RunState.VALIDATING,
            RunState.CONTROLLER_FAILURE,
            RunState.INTERRUPTED_BY_HOST,
            RunState.INDETERMINATE,
        }
    ),
    RunState.VALIDATING: frozenset(
        {
            RunState.BLOCKED_PREFLIGHT,
            RunState.READY,
            RunState.CONTROLLER_FAILURE,
            RunState.INTERRUPTED_BY_HOST,
            RunState.INDETERMINATE,
        }
    ),
    RunState.READY: frozenset(
        {
            RunState.STARTING,
            RunState.CONTROLLER_FAILURE,
            RunState.INTERRUPTED_BY_HOST,
            RunState.INDETERMINATE,
        }
    ),
    RunState.STARTING: frozenset(
        {
            RunState.RUNNING,
            RunState.COMPLETED,
            RunState.PARTIAL_NUMERICAL_FAILURE,
            RunState.CANCELLED_CLEANLY,
            RunState.CHEMISTRY_COMPLETED_OUTPUT_INCOMPLETE,
            RunState.SOLVER_FAILURE_AT_START,
            RunState.FORCE_TERMINATED,
            RunState.NATIVE_CRASH,
            RunState.CONTROLLER_FAILURE,
            RunState.INTERRUPTED_BY_HOST,
            RunState.INDETERMINATE,
        }
    ),
    RunState.RUNNING: frozenset(
        {
            RunState.COMPLETED,
            RunState.PARTIAL_NUMERICAL_FAILURE,
            RunState.SOLVER_FAILURE_AT_START,
            RunState.CANCELLED_CLEANLY,
            RunState.CANCEL_REQUESTED_SOLVER_UNRESPONSIVE,
            RunState.FORCE_TERMINATED,
            RunState.NATIVE_CRASH,
            RunState.CONTROLLER_FAILURE,
            RunState.CHEMISTRY_COMPLETED_OUTPUT_INCOMPLETE,
            RunState.INTERRUPTED_BY_HOST,
            RunState.INDETERMINATE,
        }
    ),
    RunState.CANCEL_REQUESTED_SOLVER_UNRESPONSIVE: frozenset(
        {
            RunState.COMPLETED,
            RunState.PARTIAL_NUMERICAL_FAILURE,
            RunState.CHEMISTRY_COMPLETED_OUTPUT_INCOMPLETE,
            RunState.CANCELLED_CLEANLY,
            RunState.FORCE_TERMINATED,
            RunState.NATIVE_CRASH,
            RunState.CONTROLLER_FAILURE,
            RunState.INTERRUPTED_BY_HOST,
            RunState.INDETERMINATE,
        }
    ),
    RunState.BLOCKED_PREFLIGHT: frozenset(),
    RunState.PARTIAL_NUMERICAL_FAILURE: frozenset(),
    RunState.SOLVER_FAILURE_AT_START: frozenset(),
    RunState.CANCELLED_CLEANLY: frozenset(),
    RunState.FORCE_TERMINATED: frozenset(),
    RunState.NATIVE_CRASH: frozenset(),
    RunState.CONTROLLER_FAILURE: frozenset(),
    RunState.CHEMISTRY_COMPLETED_OUTPUT_INCOMPLETE: frozenset(),
    RunState.INTERRUPTED_BY_HOST: frozenset(),
    RunState.INDETERMINATE: frozenset(),
    RunState.COMPLETED: frozenset(),
}

TERMINAL_RUN_STATES = frozenset(
    state
    for state, permitted in RUN_STATE_TRANSITIONS.items()
    if not permitted and state is not RunState.CANCEL_REQUESTED_SOLVER_UNRESPONSIVE
)


class SourceCaseIdentity(FrozenStrictModel):
    path: NonEmptyStr
    sha256: Sha256


class ProcessMetadata(FrozenStrictModel):
    pid: int = Field(ge=1)
    created_at_utc: AwareDatetime
    executable: NonEmptyStr
    command: tuple[str, ...]


class OutputCompleteness(FrozenStrictModel):
    status: Literal["not_written", "partial", "complete"]
    files_written: tuple[str, ...] = ()
    missing_files: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_coherent_file_lists(self) -> "OutputCompleteness":
        if self.status == "complete" and self.missing_files:
            raise ValueError("complete output cannot list missing files")
        return self


class RunRecord(StrictModel):
    run_schema_version: Literal["1.0"]
    run_id: NonEmptyStr
    case_id: NonEmptyStr
    source_case: SourceCaseIdentity
    snapshot_path: NonEmptyStr
    snapshot_sha256: Sha256
    scientific_fingerprint: Sha256 | None
    operational_fingerprint: Sha256
    state: RunState
    created_at_utc: AwareDatetime
    updated_at_utc: AwareDatetime
    started_at_utc: AwareDatetime | None = None
    finished_at_utc: AwareDatetime | None = None
    queue_id: str | None = None
    study_id: str | None = None
    scenario_group: str | None = None
    sample_id: str | None = None
    replicate_of_run_id: str | None = None
    controller_process: ProcessMetadata | None = None
    child_process: ProcessMetadata | None = None
    validation_receipt_path: str | None = None
    result_package_path: NonEmptyStr
    termination_category: RunTerminationCategory | None = None
    output_completeness: OutputCompleteness
    status_reason: str | None = None

    @model_validator(mode="after")
    def require_state_evidence(self) -> "RunRecord":
        if self.state not in {
            RunState.CREATED,
            RunState.VALIDATING,
            RunState.BLOCKED_PREFLIGHT,
            RunState.CONTROLLER_FAILURE,
            RunState.INTERRUPTED_BY_HOST,
            RunState.INDETERMINATE,
        } and self.scientific_fingerprint is None:
            raise ValueError("ready and executing run states require a scientific fingerprint")
        if self.updated_at_utc < self.created_at_utc:
            raise ValueError("updated_at_utc cannot precede created_at_utc")
        if self.started_at_utc is not None and self.started_at_utc < self.created_at_utc:
            raise ValueError("started_at_utc cannot precede created_at_utc")
        if self.finished_at_utc is not None and self.started_at_utc is not None:
            if self.finished_at_utc < self.started_at_utc:
                raise ValueError("finished_at_utc cannot precede started_at_utc")
        if self.state in TERMINAL_RUN_STATES and self.finished_at_utc is None:
            raise ValueError("terminal run state requires finished_at_utc")
        if self.state not in TERMINAL_RUN_STATES and self.finished_at_utc is not None:
            raise ValueError("non-terminal run state forbids finished_at_utc")
        if self.state in TERMINAL_RUN_STATES and self.termination_category is None:
            raise ValueError("terminal run state requires termination_category")
        if self.state not in TERMINAL_RUN_STATES and self.termination_category is not None:
            raise ValueError("non-terminal run state forbids termination_category")
        if self.termination_category is not None:
            allowed_categories = {self.state.value}
            if self.state is RunState.CHEMISTRY_COMPLETED_OUTPUT_INCOMPLETE:
                allowed_categories.add(RunTerminationCategory.INTERRUPTED_DURING_OUTPUT.value)
            if self.termination_category.value not in allowed_categories:
                raise ValueError("termination_category does not match terminal run state")
        if self.state is RunState.COMPLETED and self.output_completeness.status != "complete":
            raise ValueError("completed run requires complete output")
        if (
            self.state is RunState.CHEMISTRY_COMPLETED_OUTPUT_INCOMPLETE
            and self.output_completeness.status == "complete"
        ):
            raise ValueError("output-incomplete state cannot report complete output")
        return self

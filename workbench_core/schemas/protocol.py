"""Versioned JSONL event envelope shared by workers and controllers."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from .common import NonEmptyStr, StrictModel


PROTOCOL_VERSION = "1.0"


class WorkerEventType(str, Enum):
    WORKER_READY = "worker_ready"
    ENVIRONMENT_VERIFIED = "environment_verified"
    STAGE_STARTED = "stage_started"
    STAGE_COMPLETED = "stage_completed"
    VALIDATION_ISSUE = "validation_issue"
    MAPPING_RESULT = "mapping_result"
    SIMULATION_STARTED = "simulation_started"
    PROGRESS_SUMMARY = "progress_summary"
    CHECKPOINT_WRITTEN = "checkpoint_written"
    WARNING = "warning"
    OUTPUT_WRITTEN = "output_written"
    SIMULATION_FINISHED = "simulation_finished"
    WORKER_FAILURE_REPORTED = "worker_failure_reported"


class ControllerEventType(str, Enum):
    PROCESS_CREATED = "process_created"
    PROCESS_STARTED = "process_started"
    CANCEL_SIGNAL_SENT = "cancel_signal_sent"
    CANCEL_UNRESPONSIVE = "cancel_unresponsive"
    FORCE_REQUESTED = "force_requested"
    TERMINATE_SENT = "terminate_sent"
    KILL_SENT = "kill_sent"
    KILL_CONFIRMED = "kill_confirmed"
    KILL_FAILED = "kill_failed"
    PROCESS_EXITED = "process_exited"
    PROTOCOL_ERROR = "protocol_error"
    CONTROLLER_ERROR = "controller_error"


KNOWN_EVENT_TYPES = frozenset(
    event.value for event in (*WorkerEventType, *ControllerEventType)
)


class ProtocolEvent(StrictModel):
    protocol_version: Literal["1.0"]
    event_type: WorkerEventType | ControllerEventType
    timestamp_utc: AwareDatetime
    run_id: NonEmptyStr
    case_id: NonEmptyStr
    sequence_number: int = Field(ge=1)
    producer: Literal["worker", "controller"]
    payload: dict[str, JsonValue]

    @model_validator(mode="after")
    def enforce_event_owner(self) -> "ProtocolEvent":
        if isinstance(self.event_type, WorkerEventType) != (self.producer == "worker"):
            raise ValueError(f"{self.event_type.value} is not owned by {self.producer}")
        return self


class WorkerEvent(ProtocolEvent):
    event_type: WorkerEventType
    producer: Literal["worker"] = "worker"


class ControllerEvent(ProtocolEvent):
    event_type: ControllerEventType
    producer: Literal["controller"] = "controller"

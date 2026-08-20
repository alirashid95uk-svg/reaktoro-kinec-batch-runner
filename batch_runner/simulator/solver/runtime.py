"""Mutable execution state shared by the explicit solver stages."""

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from batch_runner.config import ResolvedCase


@dataclass
class SolverRun:
    case: ResolvedCase
    system: Any
    state: Any
    emit_row: Callable[[dict[str, Any]], None]
    emit_record: Callable[[dict[str, Any]], None]
    emit_boundary: Callable[[str, dict[str, Any]], None]
    emit_checkpoint: Callable[[dict[str, Any], Any], None]
    is_cancelled: Callable[[], bool]
    collect_row: Callable[[ResolvedCase, Any, dict[str, Any], Any], dict[str, Any]]
    snapshot_state: Callable[[Any], Any]
    timestep: Any = field(init=False)
    output_times: Iterator[float] = field(init=False)
    checkpoint_times: Iterator[float] = field(init=False)
    next_output_time: float | None = field(init=False)
    next_checkpoint_time: float | None = field(init=False)
    output_every_accepted_step: bool = field(init=False)
    kinetic_solver: Any | None = None
    kinetic_specs: Any | None = None
    conditions: Any | None = None
    initial_state: Any | None = None
    last_record: dict[str, Any] | None = None
    step_index: int = 0
    time_s: float = 0.0
    accepted_steps: int = 0
    failed_steps: int = 0
    dt_min_s: float | None = None
    dt_max_s: float | None = None
    dt_total_s: float = 0.0
    checkpoint_count: int = 0
    kinetic_attempts: int = 0
    solver_failed_attempts: int = 0
    reaktoro_solve_calls: int = 0
    temporal_error_rejections: int = 0
    event_localizations: int = 0
    retries_at_current_time: int = 0
    rejection_reason_counts: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.timestep = self.case.config.solver.timestep
        self.output_times = iter(self.case.output_times_s())
        self.next_output_time = next(self.output_times, None)
        self.checkpoint_times = iter(self.case.checkpoint_times_s)
        self.next_checkpoint_time = next(self.checkpoint_times, None)
        self.output_every_accepted_step = (
            self.timestep.output_schedule.mode == "every_internal_step"
        )

    def output_due(self, target_time_s: float) -> bool:
        if self.output_every_accepted_step:
            if target_time_s == 0.0:
                return self.timestep.output_schedule.include_initial
            return (
                target_time_s != self.case.duration_s
                or self.timestep.output_schedule.include_final
            )
        if self.next_output_time != target_time_s:
            return False
        self.next_output_time = next(self.output_times, None)
        return True

    def checkpoint_due(self, target_time_s: float) -> bool:
        if self.next_checkpoint_time != target_time_s:
            return False
        self.next_checkpoint_time = next(self.checkpoint_times, None)
        return True

    def progress(
        self,
        *,
        completed: bool,
        termination_reason: str | None = None,
        failed_stage: str | None = None,
        error_message: str | None = None,
        exception_type: str | None = None,
        failed_attempt_target_time_s: float | None = None,
        failed_attempt_dt_s: float | None = None,
        accepted_state_restored: bool | None = None,
        cancellation_requested: bool = False,
        cancellation_boundary: str | None = None,
    ) -> dict[str, Any]:
        return {
            "simulation_completed": completed,
            "failed_stage": failed_stage,
            "error_message": error_message,
            "exception_type": exception_type,
            "termination_reason": termination_reason or (
                "completed" if completed else "solver_failure"
            ),
            "final_time_reached_s": self.time_s,
            "number_of_accepted_steps": self.accepted_steps,
            "number_of_rejected_steps": self.failed_steps,
            "number_of_failed_steps": self.failed_steps,
            "smallest_dt_s": self.dt_min_s,
            "largest_dt_s": self.dt_max_s,
            "average_dt_s": (
                self.dt_total_s / self.accepted_steps if self.accepted_steps else None
            ),
            "failed_attempt_target_time_s": failed_attempt_target_time_s,
            "failed_attempt_dt_s": failed_attempt_dt_s,
            "accepted_state_restored": accepted_state_restored,
            "checkpoint_count": self.checkpoint_count,
            "number_of_internal_attempts": self.kinetic_attempts,
            "number_of_solver_failed_attempts": self.solver_failed_attempts,
            "number_of_reaktoro_solve_calls": (
                self.reaktoro_solve_calls or self.kinetic_attempts
            ),
            "number_of_temporal_error_rejections": self.temporal_error_rejections,
            "number_of_event_localizations": self.event_localizations,
            "retries_at_final_accepted_time": self.retries_at_current_time,
            "rejection_reason_counts": self.rejection_reason_counts,
            "cancellation_requested": cancellation_requested,
            "cancellation_boundary": cancellation_boundary,
        }

    def cancelled(self, boundary: str, *, restored: bool | None = True) -> dict[str, Any]:
        return self.progress(
            completed=False,
            termination_reason="cancelled_cleanly",
            accepted_state_restored=restored,
            cancellation_requested=True,
            cancellation_boundary=boundary,
        )

    def accept_step(self, dt_s: float, target_time_s: float) -> None:
        self.time_s = target_time_s
        self.accepted_steps += 1
        self.dt_min_s = dt_s if self.dt_min_s is None else min(self.dt_min_s, dt_s)
        self.dt_max_s = dt_s if self.dt_max_s is None else max(self.dt_max_s, dt_s)
        self.dt_total_s += dt_s
        self.step_index += 1

"""Adaptive timestep execution for feasibility-only and error-controlled modes."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .adaptive_control import (
    controller_dt,
    error_rejection_dt,
    event_overshoot_correction,
    event_snapshot,
    predict_event_limit,
    richardson_estimate,
)
from .calls import failure_reason, timed_solve
from .records import solver_record
from .runtime import SolverRun


class _AggregateResult:
    """Minimal result facade for a Richardson trial containing several solves."""

    def __init__(self, results: list[Any | None], succeeded: bool):
        self._succeeded = succeeded
        self._iterations = 0
        for result in results:
            if result is None:
                continue
            try:
                self._iterations += int(result.iterations())
            except Exception:
                pass

    def succeeded(self) -> bool:
        return self._succeeded

    def iterations(self) -> int:
        return self._iterations


def next_forced_target(
    current_time_s: float,
    duration_s: float,
    next_output_time_s: float | None,
    next_checkpoint_time_s: float | None,
) -> float:
    candidates = [duration_s]
    candidates.extend(
        target
        for target in (next_output_time_s, next_checkpoint_time_s)
        if target is not None and target > current_time_s
    )
    return min(candidates)


def adaptive_target(
    current_time_s: float, controller_dt_s: float, forced_target_s: float
) -> float:
    proposed = Decimal(str(current_time_s)) + Decimal(str(controller_dt_s))
    forced = Decimal(str(forced_target_s))
    return forced_target_s if proposed >= forced else float(proposed)


def run_adaptive_timesteps(run: SolverRun) -> tuple[Any, dict[str, Any]]:
    if run.timestep.error_control.enabled:
        return _run_error_controlled_adaptive(run)
    return _run_feasibility_adaptive(run)


def _run_error_controlled_adaptive(run: SolverRun) -> tuple[Any, dict[str, Any]]:
    """Richardson step doubling + I/PI control + geochemical event limiting."""
    initial_state = run.initial_state
    error_cfg = run.timestep.error_control
    event_cfg = run.timestep.event_control
    if run.richardson_half_solver is None:
        raise RuntimeError(
            "error-controlled adaptive stepping requires an independent half-step solver"
        )

    controller_dt_s = run.case.dt_initial_s
    previous_error_norm: float | None = None
    previous_event = None
    current_event = event_snapshot(run.case, run.state, run.time_s)

    while run.time_s < run.case.duration_s:
        if run.is_cancelled():
            return initial_state, run.cancelled("before_adaptive_richardson_trial")
        if run.kinetic_attempts >= run.timestep.max_internal_steps:
            return initial_state, run.progress(
                completed=False,
                termination_reason="max_internal_steps_exceeded",
                failed_stage="timestep_controller",
                error_message=(
                    "adaptive controller reached "
                    f"max_internal_steps={run.timestep.max_internal_steps}"
                ),
                accepted_state_restored=True,
            )

        event_limit = predict_event_limit(previous_event, current_event, event_cfg)
        event_limiter_reason = None
        proposed_dt_s = controller_dt_s
        if event_limit is not None and event_limit.dt_s < proposed_dt_s:
            if event_limit.dt_s < run.case.dt_min_s:
                return initial_state, run.progress(
                    completed=False,
                    termination_reason="event_resolution_below_minimum_timestep",
                    failed_stage="adaptive_event_controller",
                    error_message=(
                        f"predicted event requires dt={event_limit.dt_s} s below "
                        f"dt_min={run.case.dt_min_s} s ({event_limit.reason})"
                    ),
                    accepted_state_restored=True,
                )
            proposed_dt_s = event_limit.dt_s
            event_limiter_reason = event_limit.reason

        forced_target_s = next_forced_target(
            run.time_s,
            run.case.duration_s,
            run.next_output_time,
            run.next_checkpoint_time,
        )
        target_time_s = adaptive_target(run.time_s, proposed_dt_s, forced_target_s)
        dt_s = float(Decimal(str(target_time_s)) - Decimal(str(run.time_s)))
        start_s = run.time_s
        run.kinetic_attempts += 1

        full_state = run.snapshot_state(run.state)
        half_state = run.snapshot_state(run.state)
        results: list[Any | None] = []
        wall_time_s = 0.0

        full_result, elapsed, full_error = timed_solve(
            run.kinetic_solver,
            full_state,
            dt_s=dt_s,
            conditions=run.conditions,
        )
        run.kinetic_solve_calls += 1
        results.append(full_result)
        wall_time_s += elapsed
        solver_reason = failure_reason(
            full_result,
            full_error,
            f"Richardson full-step trial ending at {target_time_s} s",
        )
        if run.is_cancelled():
            return _cancel_error_controlled_trial(
                run,
                initial_state,
                start_s,
                dt_s,
                results,
                wall_time_s,
                solver_reason,
                "after_richardson_full_step",
            )

        half_error: Exception | None = None
        if solver_reason is None:
            half_result_1, elapsed, half_error_1 = timed_solve(
                run.richardson_half_solver,
                half_state,
                dt_s=dt_s / 2.0,
                conditions=run.conditions,
            )
            run.kinetic_solve_calls += 1
            results.append(half_result_1)
            wall_time_s += elapsed
            solver_reason = failure_reason(
                half_result_1,
                half_error_1,
                f"Richardson first half-step trial ending at {start_s + dt_s / 2.0} s",
            )
            half_error = half_error_1
            if run.is_cancelled():
                return _cancel_error_controlled_trial(
                    run,
                    initial_state,
                    start_s,
                    dt_s,
                    results,
                    wall_time_s,
                    solver_reason,
                    "after_richardson_first_half_step",
                )

        if solver_reason is None:
            half_result_2, elapsed, half_error_2 = timed_solve(
                run.richardson_half_solver,
                half_state,
                dt_s=dt_s / 2.0,
                conditions=run.conditions,
            )
            run.kinetic_solve_calls += 1
            results.append(half_result_2)
            wall_time_s += elapsed
            solver_reason = failure_reason(
                half_result_2,
                half_error_2,
                f"Richardson second half-step trial ending at {target_time_s} s",
            )
            half_error = half_error_2
            if run.is_cancelled():
                return _cancel_error_controlled_trial(
                    run,
                    initial_state,
                    start_s,
                    dt_s,
                    results,
                    wall_time_s,
                    solver_reason,
                    "after_richardson_second_half_step",
                )

        if solver_reason is not None:
            run.failed_steps += 1
            run.retries_at_current_time += 1
            run.solver_failed_attempts += 1
            run.rejection_reason_counts["solver_failure"] = (
                run.rejection_reason_counts.get("solver_failure", 0) + 1
            )
            previous_error_norm = None
            next_dt_s = max(run.case.dt_min_s, dt_s * error_cfg.restart_factor)
            run.emit_record(
                solver_record(
                    step_index=run.step_index,
                    attempt_index=run.kinetic_attempts,
                    stage="adaptive_richardson_trial",
                    time_start_s=start_s,
                    time_end_s=start_s,
                    dt_s=dt_s,
                    result=_AggregateResult(results, False),
                    wall_time_s=wall_time_s,
                    accepted=False,
                    failure_reason=solver_reason,
                    next_dt_s=next_dt_s,
                )
            )
            retry_limit_hit = (
                run.retries_at_current_time
                > run.timestep.step_size.max_retries_per_step
            )
            minimum_hit = dt_s <= run.case.dt_min_s
            if retry_limit_hit or minimum_hit:
                error = full_error or half_error
                return initial_state, run.progress(
                    completed=False,
                    termination_reason=(
                        "retry_limit_exceeded"
                        if retry_limit_hit
                        else "minimum_timestep_rejected"
                    ),
                    failed_stage="adaptive_richardson_trial",
                    error_message=solver_reason,
                    exception_type=type(error).__name__ if error is not None else None,
                    failed_attempt_target_time_s=target_time_s,
                    failed_attempt_dt_s=dt_s,
                    accepted_state_restored=True,
                )
            controller_dt_s = next_dt_s
            continue

        try:
            estimate = richardson_estimate(run.case, full_state, half_state)
        except Exception as error:
            return initial_state, run.progress(
                completed=False,
                termination_reason="error_estimator_failure",
                failed_stage="adaptive_error_estimator",
                error_message=str(error),
                exception_type=type(error).__name__,
                failed_attempt_target_time_s=target_time_s,
                failed_attempt_dt_s=dt_s,
                accepted_state_restored=True,
            )

        run.note_temporal_error(estimate.norm)
        if estimate.norm > 1.0:
            run.failed_steps += 1
            run.retries_at_current_time += 1
            run.temporal_error_rejections += 1
            run.rejection_reason_counts["temporal_error"] = (
                run.rejection_reason_counts.get("temporal_error", 0) + 1
            )
            previous_error_norm = None
            next_dt_s = max(
                run.case.dt_min_s,
                error_rejection_dt(dt_s, estimate.norm, error_cfg),
            )
            reason = (
                f"temporal_error_exceeded:E={estimate.norm:.8g};"
                f"worst={estimate.worst_variable};ratio={estimate.worst_ratio:.8g}"
            )
            run.emit_record(
                solver_record(
                    step_index=run.step_index,
                    attempt_index=run.kinetic_attempts,
                    stage="adaptive_richardson_trial",
                    time_start_s=start_s,
                    time_end_s=start_s,
                    dt_s=dt_s,
                    result=_AggregateResult(results, True),
                    wall_time_s=wall_time_s,
                    accepted=False,
                    failure_reason=reason,
                    next_dt_s=next_dt_s,
                )
            )
            retry_limit_hit = (
                run.retries_at_current_time
                > run.timestep.step_size.max_retries_per_step
            )
            minimum_hit = dt_s <= run.case.dt_min_s
            if retry_limit_hit or minimum_hit:
                return initial_state, run.progress(
                    completed=False,
                    termination_reason=(
                        "retry_limit_exceeded"
                        if retry_limit_hit
                        else "minimum_timestep_rejected"
                    ),
                    failed_stage="adaptive_error_estimator",
                    error_message=reason,
                    failed_attempt_target_time_s=target_time_s,
                    failed_attempt_dt_s=dt_s,
                    accepted_state_restored=True,
                )
            controller_dt_s = next_dt_s
            continue

        trial_event = event_snapshot(run.case, half_state, target_time_s)
        correction = event_overshoot_correction(
            current_event, trial_event, dt_s, event_cfg
        )
        if correction is not None:
            run.failed_steps += 1
            run.retries_at_current_time += 1
            run.event_corrections += 1
            run.rejection_reason_counts["event_overshoot"] = (
                run.rejection_reason_counts.get("event_overshoot", 0) + 1
            )
            previous_error_norm = None
            if correction.dt_s < run.case.dt_min_s:
                return initial_state, run.progress(
                    completed=False,
                    termination_reason="event_resolution_below_minimum_timestep",
                    failed_stage="adaptive_event_controller",
                    error_message=(
                        f"event correction requires dt={correction.dt_s} s below "
                        f"dt_min={run.case.dt_min_s} s ({correction.reason})"
                    ),
                    failed_attempt_target_time_s=target_time_s,
                    failed_attempt_dt_s=dt_s,
                    accepted_state_restored=True,
                )
            next_dt_s = correction.dt_s
            run.emit_record(
                solver_record(
                    step_index=run.step_index,
                    attempt_index=run.kinetic_attempts,
                    stage="adaptive_richardson_trial",
                    time_start_s=start_s,
                    time_end_s=start_s,
                    dt_s=dt_s,
                    result=_AggregateResult(results, True),
                    wall_time_s=wall_time_s,
                    accepted=False,
                    failure_reason=correction.reason,
                    next_dt_s=next_dt_s,
                )
            )
            if (
                run.retries_at_current_time
                > run.timestep.step_size.max_retries_per_step
            ):
                return initial_state, run.progress(
                    completed=False,
                    termination_reason="retry_limit_exceeded",
                    failed_stage="adaptive_event_controller",
                    error_message=correction.reason,
                    failed_attempt_target_time_s=target_time_s,
                    failed_attempt_dt_s=dt_s,
                    accepted_state_restored=True,
                )
            controller_dt_s = next_dt_s
            continue

        # Accept only the state produced by two genuine half-step Reaktoro solves.
        run.state.assign(half_state)
        next_unbounded_dt_s, _controller_kind = controller_dt(
            dt_s,
            estimate.norm,
            None if event_limiter_reason is not None else previous_error_norm,
            error_cfg,
        )
        next_dt_s = min(
            run.case.dt_max_s,
            max(run.case.dt_min_s, next_unbounded_dt_s),
        )
        record = solver_record(
            step_index=run.step_index,
            attempt_index=run.kinetic_attempts,
            stage="adaptive_richardson_trial",
            time_start_s=start_s,
            time_end_s=target_time_s,
            dt_s=dt_s,
            result=_AggregateResult(results, True),
            wall_time_s=wall_time_s,
            next_dt_s=next_dt_s,
        )
        run.accept_step(dt_s, target_time_s)
        run.retries_at_current_time = 0
        if event_limiter_reason is not None:
            run.event_limited_steps += 1
        run.emit_record(record)

        row = None
        if run.output_due(run.time_s):
            if run.is_cancelled():
                return initial_state, run.cancelled(
                    "before_adaptive_output_extraction", restored=False
                )
            row = run.collect_row(run.case, run.state, record, initial_state)
            run.emit_row(row)
        if run.checkpoint_due(run.time_s):
            if run.is_cancelled():
                return initial_state, run.cancelled(
                    "before_adaptive_checkpoint", restored=False
                )
            run.checkpoint_count += 1
            run.emit_checkpoint(record, run.state)
        if run.time_s == run.case.duration_s:
            if row is None and run.is_cancelled():
                return initial_state, run.cancelled(
                    "before_adaptive_final_output_extraction", restored=False
                )
            run.emit_boundary(
                "final",
                row
                or run.collect_row(run.case, run.state, record, initial_state),
            )

        previous_event, current_event = current_event, trial_event
        previous_error_norm = estimate.norm
        controller_dt_s = next_dt_s

    return initial_state, run.progress(completed=True)


def _cancel_error_controlled_trial(
    run: SolverRun,
    initial_state: Any,
    start_s: float,
    dt_s: float,
    results: list[Any | None],
    wall_time_s: float,
    solver_reason: str | None,
    boundary: str,
) -> tuple[Any, dict[str, Any]]:
    run.failed_steps += 1
    if solver_reason is not None:
        run.solver_failed_attempts += 1
        run.rejection_reason_counts["solver_failure"] = (
            run.rejection_reason_counts.get("solver_failure", 0) + 1
        )
    run.emit_record(
        solver_record(
            step_index=run.step_index,
            attempt_index=run.kinetic_attempts,
            stage="adaptive_richardson_trial",
            time_start_s=start_s,
            time_end_s=start_s,
            dt_s=dt_s,
            result=_AggregateResult(results, solver_reason is None),
            wall_time_s=wall_time_s,
            accepted=False,
            failure_reason=(
                solver_reason or "cooperative cancellation requested before step commit"
            ),
        )
    )
    if solver_reason is not None:
        return initial_state, run.progress(
            completed=False,
            failed_stage="adaptive_richardson_trial",
            error_message=solver_reason,
            accepted_state_restored=True,
            cancellation_requested=True,
            cancellation_boundary=boundary,
        )
    return initial_state, run.cancelled(boundary)


def _run_feasibility_adaptive(run: SolverRun) -> tuple[Any, dict[str, Any]]:
    """Legacy adaptive mode: grow on success and shrink on Reaktoro failure."""
    initial_state = run.initial_state
    controller_dt_s = run.case.dt_initial_s
    while run.time_s < run.case.duration_s:
        if run.is_cancelled():
            return initial_state, run.cancelled("before_adaptive_solver_attempt")
        if run.kinetic_attempts >= run.timestep.max_internal_steps:
            return initial_state, run.progress(
                completed=False,
                termination_reason="max_internal_steps_exceeded",
                failed_stage="timestep_controller",
                error_message=(
                    "adaptive controller reached "
                    f"max_internal_steps={run.timestep.max_internal_steps}"
                ),
                accepted_state_restored=True,
            )

        forced_target_s = next_forced_target(
            run.time_s,
            run.case.duration_s,
            run.next_output_time,
            run.next_checkpoint_time,
        )
        target_time_s = adaptive_target(
            run.time_s, controller_dt_s, forced_target_s
        )
        dt_s = float(Decimal(str(target_time_s)) - Decimal(str(run.time_s)))
        start_s = run.time_s
        accepted_state = run.snapshot_state(run.state)
        result, wall_time_s, error = timed_solve(
            run.kinetic_solver,
            run.state,
            dt_s=dt_s,
            conditions=run.conditions,
        )
        run.kinetic_solve_calls += 1
        cancel_after_attempt = run.is_cancelled()
        run.kinetic_attempts += 1
        solver_reason = failure_reason(
            result,
            error,
            f"adaptive kinetic attempt ending at {target_time_s} s",
        )
        if cancel_after_attempt:
            run.state.assign(accepted_state)
            run.failed_steps += 1
            if solver_reason is not None:
                run.solver_failed_attempts += 1
            run.emit_record(
                solver_record(
                    step_index=run.step_index,
                    attempt_index=run.kinetic_attempts,
                    stage="adaptive_kinetic_attempt",
                    time_start_s=start_s,
                    time_end_s=start_s,
                    dt_s=dt_s,
                    result=result,
                    wall_time_s=wall_time_s,
                    accepted=False,
                    failure_reason=(
                        solver_reason
                        or "cooperative cancellation requested before step commit"
                    ),
                )
            )
            if solver_reason is not None:
                run.rejection_reason_counts["solver_failure"] = (
                    run.rejection_reason_counts.get("solver_failure", 0) + 1
                )
                return initial_state, run.progress(
                    completed=False,
                    failed_stage="adaptive_kinetic_attempt",
                    error_message=solver_reason,
                    exception_type=type(error).__name__ if error is not None else None,
                    failed_attempt_target_time_s=target_time_s,
                    failed_attempt_dt_s=dt_s,
                    accepted_state_restored=True,
                    cancellation_requested=True,
                    cancellation_boundary="after_adaptive_solver_attempt",
                )
            return initial_state, run.cancelled("after_adaptive_solver_attempt")

        if solver_reason is not None:
            run.state.assign(accepted_state)
            run.failed_steps += 1
            run.retries_at_current_time += 1
            run.solver_failed_attempts += 1
            run.rejection_reason_counts["solver_failure"] = (
                run.rejection_reason_counts.get("solver_failure", 0) + 1
            )
            next_dt_s = max(
                run.case.dt_min_s,
                dt_s * run.timestep.step_size.shrink_factor,
            )
            run.emit_record(
                solver_record(
                    step_index=run.step_index,
                    attempt_index=run.kinetic_attempts,
                    stage="adaptive_kinetic_attempt",
                    time_start_s=start_s,
                    time_end_s=start_s,
                    dt_s=dt_s,
                    result=result,
                    wall_time_s=wall_time_s,
                    accepted=False,
                    failure_reason=solver_reason,
                    next_dt_s=next_dt_s,
                )
            )
            retry_limit_hit = (
                run.retries_at_current_time
                > run.timestep.step_size.max_retries_per_step
            )
            minimum_hit = dt_s <= run.case.dt_min_s
            if retry_limit_hit or minimum_hit:
                return initial_state, run.progress(
                    completed=False,
                    termination_reason=(
                        "retry_limit_exceeded"
                        if retry_limit_hit
                        else "minimum_timestep_rejected"
                    ),
                    failed_stage="adaptive_kinetic_attempt",
                    error_message=solver_reason,
                    exception_type=type(error).__name__ if error is not None else None,
                    failed_attempt_target_time_s=target_time_s,
                    failed_attempt_dt_s=dt_s,
                    accepted_state_restored=True,
                )
            controller_dt_s = next_dt_s
            continue

        next_dt_s = min(
            run.case.dt_max_s,
            max(
                run.case.dt_min_s,
                controller_dt_s * run.timestep.step_size.growth_factor,
            ),
        )
        record = solver_record(
            step_index=run.step_index,
            attempt_index=run.kinetic_attempts,
            stage="adaptive_kinetic_attempt",
            time_start_s=start_s,
            time_end_s=target_time_s,
            dt_s=dt_s,
            result=result,
            wall_time_s=wall_time_s,
            next_dt_s=next_dt_s,
        )
        run.accept_step(dt_s, target_time_s)
        run.retries_at_current_time = 0
        run.emit_record(record)
        row = None
        if run.output_due(run.time_s):
            if run.is_cancelled():
                return initial_state, run.cancelled(
                    "before_adaptive_output_extraction", restored=False
                )
            row = run.collect_row(run.case, run.state, record, initial_state)
            run.emit_row(row)
        if run.checkpoint_due(run.time_s):
            if run.is_cancelled():
                return initial_state, run.cancelled(
                    "before_adaptive_checkpoint", restored=False
                )
            run.checkpoint_count += 1
            run.emit_checkpoint(record, run.state)
        if run.time_s == run.case.duration_s:
            if row is None and run.is_cancelled():
                return initial_state, run.cancelled(
                    "before_adaptive_final_output_extraction", restored=False
                )
            run.emit_boundary(
                "final",
                row
                or run.collect_row(run.case, run.state, record, initial_state),
            )
        controller_dt_s = next_dt_s

    return initial_state, run.progress(completed=True)

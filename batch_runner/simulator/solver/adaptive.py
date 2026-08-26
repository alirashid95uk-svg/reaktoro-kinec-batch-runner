"""Legacy adaptive stepping driven only by Reaktoro solve feasibility.

Successful solves grow the controller timestep and failed solves shrink it,
subject to configured bounds and retry limits.  This mode performs no temporal
error estimate and must not be described as accuracy-controlled.  Proposed
steps are capped to exact output, checkpoint, and final-time targets.
"""

from decimal import Decimal
from typing import Any

from .calls import failure_reason, timed_solve
from .records import solver_record
from .runtime import SolverRun


def next_forced_target(
    current_time_s: float,
    duration_s: float,
    next_output_time_s: float | None,
    next_checkpoint_time_s: float | None,
) -> float:
    """Return the next mandatory accepted time among output, checkpoint, and end."""
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
    """Return the proposed accepted time without overshooting a forced target.

    Decimal arithmetic prevents binary-float addition from stepping past a
    resolved absolute target; the returned value remains a float for Reaktoro.
    """
    proposed = Decimal(str(current_time_s)) + Decimal(str(controller_dt_s))
    forced = Decimal(str(forced_target_s))
    return forced_target_s if proposed >= forced else float(proposed)


def run_adaptive_timesteps(run: SolverRun) -> tuple[Any, dict[str, Any]]:
    """Execute legacy feasibility-adaptive kinetic steps.

    Reaktoro exceptions and unsuccessful results reject the attempt, restore
    the accepted state, and shrink ``dt``.  Successful attempts alone advance
    accepted time and are eligible for observations and checkpoints.

    Returns:
        tuple[Any, dict[str, Any]]: The initial-state reference and lifecycle
            progress.  Retry exhaustion, minimum-timestep rejection,
            cancellation, and step-limit termination are reported rather than
            raised.
    """
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
        dt_s = float(
            Decimal(str(target_time_s)) - Decimal(str(run.time_s))
        )
        start_s = run.time_s
        accepted_state = run.snapshot_state(run.state)
        result, wall_time_s, error = timed_solve(
            run.kinetic_solver,
            run.state,
            dt_s=dt_s,
            conditions=run.conditions,
        )
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
            row = run.collect_row(
                run.case, run.state, record, initial_state
            )
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

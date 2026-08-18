"""Fixed-timestep execution with absolute configured targets."""

from typing import Any

from .calls import failure_reason, timed_solve
from .records import solver_record
from .runtime import SolverRun


def run_fixed_timesteps(run: SolverRun) -> tuple[Any, dict[str, Any]]:
    initial_state = run.initial_state
    for dt_s, target_time_s in run.case.fixed_steps_s():
        if run.is_cancelled():
            return initial_state, run.cancelled("before_fixed_solver_attempt")
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
        reason = failure_reason(
            result,
            error,
            f"kinetic step ending at {target_time_s} s",
        )
        if reason:
            run.state.assign(accepted_state)
            run.failed_steps += 1
            run.solver_failed_attempts += 1
            run.rejection_reason_counts["solver_failure"] = 1
            run.emit_record(
                solver_record(
                    step_index=run.step_index,
                    attempt_index=run.kinetic_attempts,
                    stage="kinetic_step",
                    time_start_s=start_s,
                    time_end_s=start_s,
                    dt_s=dt_s,
                    result=result,
                    wall_time_s=wall_time_s,
                    accepted=False,
                    failure_reason=reason,
                )
            )
            return initial_state, run.progress(
                completed=False,
                failed_stage="kinetic_step",
                error_message=reason,
                exception_type=type(error).__name__ if error is not None else None,
                failed_attempt_target_time_s=target_time_s,
                failed_attempt_dt_s=dt_s,
                accepted_state_restored=True,
                cancellation_requested=cancel_after_attempt,
                cancellation_boundary=(
                    "after_fixed_solver_attempt" if cancel_after_attempt else None
                ),
            )

        record = solver_record(
            step_index=run.step_index,
            attempt_index=run.kinetic_attempts,
            stage="kinetic_step",
            time_start_s=start_s,
            time_end_s=target_time_s,
            dt_s=dt_s,
            result=result,
            wall_time_s=wall_time_s,
        )
        run.accept_step(dt_s, target_time_s)
        run.emit_record(record)
        if cancel_after_attempt:
            return initial_state, run.cancelled(
                "after_fixed_solver_attempt", restored=False
            )
        row = None
        if run.output_due(run.time_s):
            if run.is_cancelled():
                return initial_state, run.cancelled(
                    "before_fixed_output_extraction", restored=False
                )
            row = run.collect_row(
                run.case, run.state, record, initial_state
            )
            run.emit_row(row)
        if run.checkpoint_due(run.time_s):
            if run.is_cancelled():
                return initial_state, run.cancelled(
                    "before_fixed_checkpoint", restored=False
                )
            run.checkpoint_count += 1
            run.emit_checkpoint(record, run.state)
        if run.time_s == run.case.duration_s:
            if row is None and run.is_cancelled():
                return initial_state, run.cancelled(
                    "before_fixed_final_output_extraction", restored=False
                )
            run.emit_boundary(
                "final",
                row
                or run.collect_row(run.case, run.state, record, initial_state),
            )
    return initial_state, run.progress(completed=True)

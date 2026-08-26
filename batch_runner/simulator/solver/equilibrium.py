"""Run the optional time-zero equilibrium stage and equilibrium-only output.

This stage applies only the constraints selected by the workflow rules.  It
snapshots before the mutating Reaktoro solve, restores on failure, and records
the attempt at time zero.  Kinetic advancement is owned by the timestep
modules.
"""

from typing import Any

from batch_runner.simulator.chemistry.conditions import build_conditions

from .calls import equilibrium_solver, failure_reason, timed_solve
from batch_runner.simulator.chemistry.conditions import requires_initial_equilibrium
from .records import solver_record
from .runtime import SolverRun


def run_initial_equilibrium(
    run: SolverRun,
) -> tuple[Any, dict[str, Any]] | None:
    """Run configured initial equilibrium, returning only when execution stops.

    Returns:
        tuple[Any, dict[str, Any]] | None: ``None`` when no initial solve is
            needed or it succeeds.  On failure or cancellation, returns the
            accepted state snapshot and lifecycle progress expected by
            :func:`execute_solver`.

    The Reaktoro solve mutates ``run.state``.  Failed attempts are rolled back
    before the failure record and progress summary are returned.
    """
    if not requires_initial_equilibrium(run.case):
        return None
    if run.is_cancelled():
        return run.snapshot_state(run.state), run.cancelled("before_initial_equilibrium")

    specs, conditions = build_conditions(
        run.case, run.system, run.state, "initial_equilibrium"
    )
    solver = equilibrium_solver(run.system, specs)
    accepted_state = run.snapshot_state(run.state)
    result, wall_time_s, error = timed_solve(
        solver, run.state, conditions=conditions
    )
    cancel_after_attempt = run.is_cancelled()
    reason = failure_reason(result, error, "initial equilibrium")
    if reason:
        run.state.assign(accepted_state)
    record = solver_record(
        step_index=run.step_index,
        stage="initial_equilibrium",
        time_start_s=0.0,
        time_end_s=0.0,
        dt_s=0.0,
        result=result,
        wall_time_s=wall_time_s,
        accepted=reason is None,
        failure_reason=reason or "",
    )
    run.last_record = record
    run.emit_record(record)
    if reason:
        run.failed_steps += 1
        return run.snapshot_state(run.state), run.progress(
            completed=False,
            failed_stage="initial_equilibrium",
            error_message=reason,
            exception_type=type(error).__name__ if error is not None else None,
            failed_attempt_target_time_s=0.0,
            failed_attempt_dt_s=0.0,
            accepted_state_restored=True,
            cancellation_requested=cancel_after_attempt,
            cancellation_boundary=(
                "after_initial_equilibrium" if cancel_after_attempt else None
            ),
        )
    if cancel_after_attempt:
        return run.snapshot_state(run.state), run.cancelled(
            "after_initial_equilibrium", restored=False
        )
    run.step_index += 1
    return None


def finish_equilibrium_only(run: SolverRun) -> tuple[Any, dict[str, Any]]:
    """Emit the time-zero accepted state as both boundaries and finish.

    Raises:
        AssertionError: Initial equilibrium did not leave a solver record.
        Exception: Configured result extraction fails.
    """
    initial_state = run.snapshot_state(run.state)
    run.initial_state = initial_state
    if run.is_cancelled():
        return initial_state, run.cancelled("before_equilibrium_output_extraction")
    assert run.last_record is not None
    row = run.collect_row(run.case, run.state, run.last_record, initial_state)
    run.emit_boundary("initial", row)
    run.emit_boundary("final", row)
    if run.output_due(0.0):
        run.emit_row(row)
    return initial_state, run.progress(completed=True)

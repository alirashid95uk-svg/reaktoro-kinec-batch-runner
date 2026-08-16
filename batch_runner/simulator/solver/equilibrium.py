"""Initial equilibrium, equilibrium-only completion, and preconditioning."""

from typing import Any

from batch_runner.simulator.chemistry.conditions import build_conditions

from .calls import equilibrium_solver, failure_reason, timed_precondition, timed_solve
from batch_runner.simulator.chemistry.conditions import requires_initial_equilibrium
from .records import solver_record
from .runtime import SolverRun


def run_initial_equilibrium(
    run: SolverRun,
) -> tuple[Any, dict[str, Any]] | None:
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


def precondition_kinetics(run: SolverRun) -> tuple[Any, dict[str, Any]] | None:
    staged_closed_workflow = (
        run.case.config.solver.workflow.mode
        == "fixed_fugacity_initial_equilibrium_then_closed_kinetics"
    )
    if (
        not run.case.config.solver.workflow.precondition_kinetics
        or staged_closed_workflow
    ):
        return None
    if run.is_cancelled():
        return run.snapshot_state(run.state), run.cancelled(
            "before_kinetics_precondition"
        )

    accepted_state = run.snapshot_state(run.state)
    result, wall_time_s, error = timed_precondition(
        run.kinetic_solver, run.state, run.conditions
    )
    cancel_after_attempt = run.is_cancelled()
    reason = failure_reason(result, error, "kinetics precondition")
    if reason:
        run.state.assign(accepted_state)
    run.emit_record(
        solver_record(
            step_index=run.step_index,
            stage="kinetics_precondition",
            time_start_s=0.0,
            time_end_s=0.0,
            dt_s=0.0,
            result=result,
            wall_time_s=wall_time_s,
            accepted=reason is None,
            failure_reason=reason or "",
        )
    )
    if reason:
        run.failed_steps += 1
        return run.snapshot_state(run.state), run.progress(
            completed=False,
            failed_stage="kinetics_precondition",
            error_message=reason,
            exception_type=type(error).__name__ if error is not None else None,
            failed_attempt_target_time_s=0.0,
            failed_attempt_dt_s=0.0,
            accepted_state_restored=True,
            cancellation_requested=cancel_after_attempt,
            cancellation_boundary=(
                "after_kinetics_precondition" if cancel_after_attempt else None
            ),
        )
    if cancel_after_attempt:
        return run.snapshot_state(run.state), run.cancelled(
            "after_kinetics_precondition", restored=False
        )
    run.precondition_applied = True
    run.step_index += 1
    return None

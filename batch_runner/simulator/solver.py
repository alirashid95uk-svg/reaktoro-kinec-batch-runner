"""Explicit equilibrium and kinetic workflow execution."""

from __future__ import annotations

from decimal import Decimal
from time import perf_counter
from typing import Any, Callable

import reaktoro as rkt

from batch_runner.config import AdaptiveTimestepConfig, ResolvedCase
from batch_runner.simulator.acceptance import evaluate_trial
from batch_runner.simulator.extract import collect_row
from batch_runner.simulator.state_builder import build_conditions
from batch_runner.simulator.state_snapshot import snapshot_state
from batch_runner.simulator.workflows import requires_initial_equilibrium


def execute_solver(
    case: ResolvedCase,
    system: Any,
    state: Any,
    row_ready: Callable[[dict[str, Any]], None] | None = None,
    solver_record_ready: Callable[[dict[str, Any]], None] | None = None,
    boundary_row_ready: Callable[[str, dict[str, Any]], None] | None = None,
    checkpoint_ready: Callable[[dict[str, Any], Any], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> tuple[Any, dict[str, Any]]:
    timestep = case.config.solver.timestep
    emit_row = row_ready or (lambda _row: None)
    emit_record = solver_record_ready or (lambda _record: None)
    emit_boundary = boundary_row_ready or (lambda _which, _row: None)
    emit_checkpoint = checkpoint_ready or (lambda _record, _state: None)
    is_cancelled = cancel_requested or (lambda: False)
    output_times = iter(case.output_times_s())
    next_output_time = next(output_times, None)
    checkpoint_times = iter(case.checkpoint_times_s)
    next_checkpoint_time = next(checkpoint_times, None)
    output_every_accepted_step = (
        timestep.output_schedule.mode == "every_internal_step"
    )
    step_index = 0
    time_s = 0.0
    accepted_steps = 0
    failed_steps = 0
    dt_min_s = None
    dt_max_s = None
    dt_total_s = 0.0
    precondition_applied = False
    checkpoint_count = 0
    kinetic_attempts = 0
    solver_failed_attempts = 0
    retries_at_current_time = 0
    rejection_reason_counts: dict[str, int] = {}

    def output_due(target_time_s: float) -> bool:
        nonlocal next_output_time
        if output_every_accepted_step:
            if target_time_s == 0.0:
                return timestep.output_schedule.include_initial
            return target_time_s != case.duration_s or timestep.output_schedule.include_final
        if next_output_time != target_time_s:
            return False
        next_output_time = next(output_times, None)
        return True

    def checkpoint_due(target_time_s: float) -> bool:
        nonlocal next_checkpoint_time
        if next_checkpoint_time != target_time_s:
            return False
        next_checkpoint_time = next(checkpoint_times, None)
        return True

    def progress(
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
            "termination_reason": termination_reason or ("completed" if completed else "solver_failure"),
            "final_time_reached_s": time_s,
            "number_of_accepted_steps": accepted_steps,
            "number_of_rejected_steps": failed_steps,
            "number_of_failed_steps": failed_steps,
            "smallest_dt_s": dt_min_s,
            "largest_dt_s": dt_max_s,
            "average_dt_s": dt_total_s / accepted_steps if accepted_steps else None,
            "kinetic_precondition_applied": precondition_applied,
            "failed_attempt_target_time_s": failed_attempt_target_time_s,
            "failed_attempt_dt_s": failed_attempt_dt_s,
            "accepted_state_restored": accepted_state_restored,
            "checkpoint_count": checkpoint_count,
            "number_of_internal_attempts": kinetic_attempts,
            "number_of_solver_failed_attempts": solver_failed_attempts,
            "retries_at_final_accepted_time": retries_at_current_time,
            "rejection_reason_counts": rejection_reason_counts,
            "cancellation_requested": cancellation_requested,
            "cancellation_boundary": cancellation_boundary,
        }

    def cancelled(boundary: str, *, restored: bool | None = True) -> dict[str, Any]:
        return progress(
            completed=False,
            termination_reason="cancelled_cleanly",
            accepted_state_restored=restored,
            cancellation_requested=True,
            cancellation_boundary=boundary,
        )

    if requires_initial_equilibrium(case):
        if is_cancelled():
            return snapshot_state(state), cancelled("before_initial_equilibrium")
        specs, conditions = build_conditions(case, system, state, "initial_equilibrium")
        solver = _equilibrium_solver(system, specs)
        accepted_state = snapshot_state(state)
        result, wall_time_s, error = _timed_solve(solver, state, conditions=conditions)
        cancel_after_attempt = is_cancelled()
        failure_reason = _failure_reason(result, error, "initial equilibrium")
        if failure_reason:
            state.assign(accepted_state)
        record = _solver_record(
            step_index=step_index,
            stage="initial_equilibrium",
            time_start_s=0.0,
            time_end_s=0.0,
            dt_s=0.0,
            result=result,
            wall_time_s=wall_time_s,
            accepted=failure_reason is None,
            failure_reason=failure_reason or "",
        )
        emit_record(record)
        if failure_reason:
            failed_steps += 1
        if failure_reason:
            return snapshot_state(state), progress(
                completed=False,
                failed_stage="initial_equilibrium",
                error_message=failure_reason,
                exception_type=type(error).__name__ if error is not None else None,
                failed_attempt_target_time_s=0.0,
                failed_attempt_dt_s=0.0,
                accepted_state_restored=True,
                cancellation_requested=cancel_after_attempt,
                cancellation_boundary=("after_initial_equilibrium" if cancel_after_attempt else None),
            )
        if cancel_after_attempt:
            return snapshot_state(state), cancelled("after_initial_equilibrium", restored=False)
        step_index += 1

    if case.config.solver.workflow.mode == "equilibrium_only":
        initial_state = snapshot_state(state)
        if is_cancelled():
            return initial_state, cancelled("before_equilibrium_output_extraction")
        row = collect_row(case, state, record, initial_state)
        emit_boundary("initial", row)
        emit_boundary("final", row)
        if output_due(0.0):
            emit_row(row)
        return initial_state, progress(completed=True)

    specs, conditions = build_conditions(case, system, state, "kinetic_steps")
    kinetic_solver = _kinetics_solver(system, specs)
    staged_closed_workflow = (
        case.config.solver.workflow.mode
        == "fixed_fugacity_initial_equilibrium_then_closed_kinetics"
    )
    if case.config.solver.workflow.precondition_kinetics and not staged_closed_workflow:
        if is_cancelled():
            return snapshot_state(state), cancelled("before_kinetics_precondition")
        accepted_state = snapshot_state(state)
        result, wall_time_s, error = _timed_precondition(kinetic_solver, state, conditions)
        cancel_after_attempt = is_cancelled()
        failure_reason = _failure_reason(result, error, "kinetics precondition")
        if failure_reason:
            state.assign(accepted_state)
        emit_record(
            _solver_record(
                step_index=step_index,
                stage="kinetics_precondition",
                time_start_s=0.0,
                time_end_s=0.0,
                dt_s=0.0,
                result=result,
                wall_time_s=wall_time_s,
                accepted=failure_reason is None,
                failure_reason=failure_reason or "",
            )
        )
        if failure_reason:
            failed_steps += 1
        if failure_reason:
            return snapshot_state(state), progress(
                completed=False,
                failed_stage="kinetics_precondition",
                error_message=failure_reason,
                exception_type=type(error).__name__ if error is not None else None,
                failed_attempt_target_time_s=0.0,
                failed_attempt_dt_s=0.0,
                accepted_state_restored=True,
                cancellation_requested=cancel_after_attempt,
                cancellation_boundary=("after_kinetics_precondition" if cancel_after_attempt else None),
            )
        if cancel_after_attempt:
            return snapshot_state(state), cancelled("after_kinetics_precondition", restored=False)
        precondition_applied = True
        step_index += 1

    initial_state = snapshot_state(state)
    initial_record = _unsolved_record(step_index, "initial_state", 0.0)
    if is_cancelled():
        return initial_state, cancelled("before_initial_output_extraction")
    initial_row = collect_row(case, state, initial_record, initial_state)
    emit_boundary("initial", initial_row)
    if output_due(0.0):
        emit_row(initial_row)

    def _run_fixed_timesteps() -> tuple[Any, dict[str, Any]]:
        nonlocal accepted_steps, checkpoint_count, dt_max_s, dt_min_s, dt_total_s
        nonlocal failed_steps, kinetic_attempts, solver_failed_attempts, step_index, time_s

        for dt_s, target_time_s in case.fixed_steps_s():
            if is_cancelled():
                return initial_state, cancelled("before_fixed_solver_attempt")
            start_s = time_s
            accepted_state = snapshot_state(state)
            result, wall_time_s, error = _timed_solve(
                kinetic_solver,
                state,
                dt_s=dt_s,
                conditions=conditions,
            )
            cancel_after_attempt = is_cancelled()
            kinetic_attempts += 1
            failure_reason = _failure_reason(
                result,
                error,
                f"kinetic step ending at {target_time_s} s",
            )
            if failure_reason:
                state.assign(accepted_state)
                failed_steps += 1
                solver_failed_attempts += 1
                rejection_reason_counts["solver_failure"] = 1
                emit_record(
                    _solver_record(
                        step_index=step_index,
                        attempt_index=kinetic_attempts,
                        stage="kinetic_step",
                        time_start_s=start_s,
                        time_end_s=start_s,
                        dt_s=dt_s,
                        result=result,
                        wall_time_s=wall_time_s,
                        accepted=False,
                        failure_reason=failure_reason,
                        acceptance_reason="solver_failure",
                    )
                )
                return initial_state, progress(
                    completed=False,
                    failed_stage="kinetic_step",
                    error_message=failure_reason,
                    exception_type=type(error).__name__ if error is not None else None,
                    failed_attempt_target_time_s=target_time_s,
                    failed_attempt_dt_s=dt_s,
                    accepted_state_restored=True,
                    cancellation_requested=cancel_after_attempt,
                    cancellation_boundary=("after_fixed_solver_attempt" if cancel_after_attempt else None),
                )
            time_s = target_time_s
            record = _solver_record(
                step_index=step_index,
                attempt_index=kinetic_attempts,
                stage="kinetic_step",
                time_start_s=start_s,
                time_end_s=time_s,
                dt_s=dt_s,
                result=result,
                wall_time_s=wall_time_s,
                acceptance_reason="fixed_solver_success",
            )
            accepted_steps += 1
            dt_min_s = dt_s if dt_min_s is None else min(dt_min_s, dt_s)
            dt_max_s = dt_s if dt_max_s is None else max(dt_max_s, dt_s)
            dt_total_s += dt_s
            step_index += 1
            emit_record(record)
            if cancel_after_attempt:
                return initial_state, cancelled(
                    "after_fixed_solver_attempt",
                    restored=False,
                )
            row = None
            if output_due(time_s):
                if is_cancelled():
                    return initial_state, cancelled(
                        "before_fixed_output_extraction",
                        restored=False,
                    )
                row = collect_row(case, state, record, initial_state)
                emit_row(row)
            if checkpoint_due(time_s):
                if is_cancelled():
                    return initial_state, cancelled(
                        "before_fixed_checkpoint",
                        restored=False,
                    )
                checkpoint_count += 1
                emit_checkpoint(record, state)
            if time_s == case.duration_s:
                if row is None and is_cancelled():
                    return initial_state, cancelled(
                        "before_fixed_final_output_extraction",
                        restored=False,
                    )
                emit_boundary(
                    "final",
                    row or collect_row(case, state, record, initial_state),
                )
        return initial_state, progress(completed=True)

    def _run_adaptive_timesteps() -> tuple[Any, dict[str, Any]]:
        nonlocal accepted_steps, checkpoint_count, dt_max_s, dt_min_s, dt_total_s
        nonlocal failed_steps, kinetic_attempts, retries_at_current_time
        nonlocal solver_failed_attempts, step_index, time_s

        controller_dt_s = case.dt_initial_s
        while time_s < case.duration_s:
            if is_cancelled():
                return initial_state, cancelled("before_adaptive_solver_attempt")
            if kinetic_attempts >= timestep.max_internal_steps:
                return initial_state, progress(
                    completed=False,
                    termination_reason="max_internal_steps_exceeded",
                    failed_stage="timestep_controller",
                    error_message=(
                        f"adaptive controller reached max_internal_steps={timestep.max_internal_steps}"
                    ),
                    accepted_state_restored=True,
                )

            forced_target_s = _next_forced_target(
                time_s,
                case.duration_s,
                next_output_time,
                next_checkpoint_time,
            )
            target_time_s = _adaptive_target(time_s, controller_dt_s, forced_target_s)
            dt_s = float(Decimal(str(target_time_s)) - Decimal(str(time_s)))
            start_s = time_s
            accepted_state = snapshot_state(state)
            result, wall_time_s, error = _timed_solve(
                kinetic_solver,
                state,
                dt_s=dt_s,
                conditions=conditions,
            )
            cancel_after_attempt = is_cancelled()
            kinetic_attempts += 1
            solver_reason = _failure_reason(
                result,
                error,
                f"adaptive kinetic attempt ending at {target_time_s} s",
            )
            if cancel_after_attempt:
                state.assign(accepted_state)
                failed_steps += 1
                if solver_reason is not None:
                    solver_failed_attempts += 1
                emit_record(
                    _solver_record(
                        step_index=step_index,
                        attempt_index=kinetic_attempts,
                        stage="adaptive_kinetic_attempt",
                        time_start_s=start_s,
                        time_end_s=start_s,
                        dt_s=dt_s,
                        result=result,
                        wall_time_s=wall_time_s,
                        accepted=False,
                        failure_reason=(
                            solver_reason
                            or "cooperative cancellation requested before trial acceptance"
                        ),
                        acceptance_reason=("solver_failure" if solver_reason else "cancelled_before_acceptance"),
                    )
                )
                if solver_reason is not None:
                    rejection_reason_counts["solver_failure"] = rejection_reason_counts.get("solver_failure", 0) + 1
                    return initial_state, progress(
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
                return initial_state, cancelled("after_adaptive_solver_attempt")
            acceptance = _empty_acceptance("solver_failure" if solver_reason else "accepted")
            acceptance_error = None
            if solver_reason is None:
                try:
                    acceptance = evaluate_trial(case, system, accepted_state, state)
                except Exception as acceptance_exception:
                    acceptance_error = acceptance_exception
                    acceptance = _empty_acceptance(
                        "acceptance_evaluation_error:"
                        f"{type(acceptance_exception).__name__}:{acceptance_exception}"
                    )

            rejection_reason = solver_reason or (
                None if acceptance["accepted"] else acceptance["acceptance_reason"]
            )
            if rejection_reason is not None:
                state.assign(accepted_state)
                failed_steps += 1
                retries_at_current_time += 1
                if solver_reason is not None:
                    solver_failed_attempts += 1
                reason_key = "solver_failure" if solver_reason else acceptance["acceptance_reason"]
                for reason in reason_key.split(";"):
                    rejection_reason_counts[reason] = rejection_reason_counts.get(reason, 0) + 1
                next_dt_s = max(case.dt_min_s, dt_s * timestep.step_size.shrink_factor)
                emit_record(
                    _solver_record(
                        step_index=step_index,
                        attempt_index=kinetic_attempts,
                        stage="adaptive_kinetic_attempt",
                        time_start_s=start_s,
                        time_end_s=start_s,
                        dt_s=dt_s,
                        result=result,
                        wall_time_s=wall_time_s,
                        accepted=False,
                        failure_reason=rejection_reason,
                        next_dt_s=next_dt_s,
                        **_record_acceptance(acceptance),
                    )
                )
                retry_limit_hit = retries_at_current_time > timestep.step_size.max_retries_per_step
                minimum_hit = dt_s <= case.dt_min_s
                if retry_limit_hit or minimum_hit:
                    return initial_state, progress(
                        completed=False,
                        termination_reason=(
                            "retry_limit_exceeded" if retry_limit_hit else "minimum_timestep_rejected"
                        ),
                        failed_stage="adaptive_kinetic_attempt",
                        error_message=rejection_reason,
                        exception_type=(
                            type(error).__name__
                            if error is not None
                            else type(acceptance_error).__name__ if acceptance_error is not None else None
                        ),
                        failed_attempt_target_time_s=target_time_s,
                        failed_attempt_dt_s=dt_s,
                        accepted_state_restored=True,
                    )
                controller_dt_s = next_dt_s
                continue

            time_s = target_time_s
            next_dt_s = min(
                case.dt_max_s,
                max(case.dt_min_s, controller_dt_s * timestep.step_size.growth_factor),
            )
            record = _solver_record(
                step_index=step_index,
                attempt_index=kinetic_attempts,
                stage="adaptive_kinetic_attempt",
                time_start_s=start_s,
                time_end_s=time_s,
                dt_s=dt_s,
                result=result,
                wall_time_s=wall_time_s,
                next_dt_s=next_dt_s,
                **_record_acceptance(acceptance),
            )
            accepted_steps += 1
            retries_at_current_time = 0
            dt_min_s = dt_s if dt_min_s is None else min(dt_min_s, dt_s)
            dt_max_s = dt_s if dt_max_s is None else max(dt_max_s, dt_s)
            dt_total_s += dt_s
            step_index += 1
            emit_record(record)
            row = None
            if output_due(time_s):
                if is_cancelled():
                    return initial_state, cancelled(
                        "before_adaptive_output_extraction",
                        restored=False,
                    )
                row = collect_row(case, state, record, initial_state)
                emit_row(row)
            if checkpoint_due(time_s):
                if is_cancelled():
                    return initial_state, cancelled(
                        "before_adaptive_checkpoint",
                        restored=False,
                    )
                checkpoint_count += 1
                emit_checkpoint(record, state)
            if time_s == case.duration_s:
                if row is None and is_cancelled():
                    return initial_state, cancelled(
                        "before_adaptive_final_output_extraction",
                        restored=False,
                    )
                emit_boundary(
                    "final",
                    row or collect_row(case, state, record, initial_state),
                )
            controller_dt_s = next_dt_s

        return initial_state, progress(completed=True)

    if timestep.mode == "fixed":
        return _run_fixed_timesteps()
    if not isinstance(timestep, AdaptiveTimestepConfig):
        raise TypeError(f"unsupported timestep config: {type(timestep).__name__}")
    return _run_adaptive_timesteps()


def _next_forced_target(
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


def _adaptive_target(current_time_s: float, controller_dt_s: float, forced_target_s: float) -> float:
    proposed = Decimal(str(current_time_s)) + Decimal(str(controller_dt_s))
    forced = Decimal(str(forced_target_s))
    return forced_target_s if proposed >= forced else float(proposed)


def _empty_acceptance(reason: str) -> dict[str, Any]:
    return {
        "accepted": reason == "accepted",
        "acceptance_reason": reason,
        "delta_pH": None,
        "max_delta_saturation_index": None,
        "max_selected_species_change_mol": None,
        "max_selected_species_tolerance_ratio": None,
        "worst_selected_species": None,
        "max_mineral_change_mol": None,
        "max_mineral_tolerance_ratio": None,
        "worst_mineral": None,
        "minimum_species_amount_mol": None,
        "tolerated_negative_species_count": None,
        "most_negative_tolerated_amount_mol": None,
        "max_element_balance_error_mol": None,
        "max_element_balance_error_ratio": None,
        "worst_element": None,
        "trial_charge_mol": None,
    }


def _record_acceptance(acceptance: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in acceptance.items() if key != "accepted"}


def _equilibrium_solver(system: Any, specs: Any | None) -> Any:
    return rkt.EquilibriumSolver(specs) if specs is not None else rkt.EquilibriumSolver(system)


def _kinetics_solver(system: Any, specs: Any | None) -> Any:
    return rkt.KineticsSolver(specs) if specs is not None else rkt.KineticsSolver(system)


def _timed_precondition(
    solver: Any,
    state: Any,
    conditions: Any | None,
) -> tuple[Any | None, float, Exception | None]:
    start = perf_counter()
    try:
        result = solver.precondition(state, conditions) if conditions is not None else solver.precondition(state)
        return result, perf_counter() - start, None
    except Exception as error:
        return None, perf_counter() - start, error


def _timed_solve(
    solver: Any,
    state: Any,
    *,
    dt_s: float | None = None,
    conditions: Any | None = None,
) -> tuple[Any | None, float, Exception | None]:
    start = perf_counter()
    try:
        if dt_s is None:
            result = solver.solve(state, conditions) if conditions is not None else solver.solve(state)
        else:
            result = solver.solve(state, dt_s, conditions) if conditions is not None else solver.solve(state, dt_s)
        return result, perf_counter() - start, None
    except Exception as error:
        return None, perf_counter() - start, error


def _failure_reason(result: Any | None, error: Exception | None, stage: str) -> str | None:
    if error is not None:
        return f"{type(error).__name__} during {stage}: {error}"
    if result is None or not result.succeeded():
        return f"Reaktoro solver failed during {stage}"
    return None


def _solver_record(
    *,
    step_index: int,
    stage: str,
    time_start_s: float,
    time_end_s: float,
    dt_s: float,
    result: Any,
    wall_time_s: float,
    accepted: bool = True,
    failure_reason: str = "",
    attempt_index: int | None = None,
    next_dt_s: float | None = None,
    acceptance_reason: str = "",
    delta_pH: float | None = None,
    max_delta_saturation_index: float | None = None,
    max_selected_species_change_mol: float | None = None,
    max_selected_species_tolerance_ratio: float | None = None,
    worst_selected_species: str | None = None,
    max_mineral_change_mol: float | None = None,
    max_mineral_tolerance_ratio: float | None = None,
    worst_mineral: str | None = None,
    minimum_species_amount_mol: float | None = None,
    tolerated_negative_species_count: int | None = None,
    most_negative_tolerated_amount_mol: float | None = None,
    max_element_balance_error_mol: float | None = None,
    max_element_balance_error_ratio: float | None = None,
    worst_element: str | None = None,
    trial_charge_mol: float | None = None,
) -> dict[str, Any]:
    return {
        "step_index": step_index,
        "attempt_index": attempt_index,
        "time_start_s": float(time_start_s),
        "time_end_s": float(time_end_s),
        "dt_s": float(dt_s),
        "stage": stage,
        "accepted": accepted,
        "solver_succeeded": bool(result.succeeded()) if result is not None else False,
        "iterations": int(result.iterations()) if result is not None else None,
        "wall_time_s": float(wall_time_s),
        "failure_reason": failure_reason,
        "acceptance_reason": acceptance_reason,
        "next_dt_s": next_dt_s,
        "delta_pH": delta_pH,
        "max_delta_saturation_index": max_delta_saturation_index,
        "max_selected_species_change_mol": max_selected_species_change_mol,
        "max_selected_species_tolerance_ratio": max_selected_species_tolerance_ratio,
        "worst_selected_species": worst_selected_species,
        "max_mineral_change_mol": max_mineral_change_mol,
        "max_mineral_tolerance_ratio": max_mineral_tolerance_ratio,
        "worst_mineral": worst_mineral,
        "minimum_species_amount_mol": minimum_species_amount_mol,
        "tolerated_negative_species_count": tolerated_negative_species_count,
        "most_negative_tolerated_amount_mol": most_negative_tolerated_amount_mol,
        "max_element_balance_error_mol": max_element_balance_error_mol,
        "max_element_balance_error_ratio": max_element_balance_error_ratio,
        "worst_element": worst_element,
        "trial_charge_mol": trial_charge_mol,
    }


def _unsolved_record(step_index: int, stage: str, time_s: float) -> dict[str, Any]:
    return {
        "step_index": step_index,
        "attempt_index": None,
        "time_start_s": float(time_s),
        "time_end_s": float(time_s),
        "dt_s": 0.0,
        "stage": stage,
        "accepted": True,
        "solver_succeeded": None,
        "iterations": None,
        "wall_time_s": 0.0,
        "failure_reason": "",
        "acceptance_reason": "not_evaluated",
        "next_dt_s": None,
        "delta_pH": None,
        "max_delta_saturation_index": None,
        "max_selected_species_change_mol": None,
        "max_selected_species_tolerance_ratio": None,
        "worst_selected_species": None,
        "max_mineral_change_mol": None,
        "max_mineral_tolerance_ratio": None,
        "worst_mineral": None,
        "minimum_species_amount_mol": None,
        "tolerated_negative_species_count": None,
        "most_negative_tolerated_amount_mol": None,
        "max_element_balance_error_mol": None,
        "max_element_balance_error_ratio": None,
        "worst_element": None,
        "trial_charge_mol": None,
    }

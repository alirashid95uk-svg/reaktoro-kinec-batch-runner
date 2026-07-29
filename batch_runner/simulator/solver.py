"""Explicit equilibrium and kinetic workflow execution."""

from __future__ import annotations

from time import perf_counter
from typing import Any

import reaktoro as rkt

from batch_runner.config import ResolvedCase
from batch_runner.simulator.extract import collect_row
from batch_runner.simulator.state_builder import build_conditions
from batch_runner.simulator.state_snapshot import snapshot_state
from batch_runner.simulator.workflows import requires_initial_equilibrium


def execute_solver(
    case: ResolvedCase,
    system: Any,
    state: Any,
    kinec_params: Any | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Any]:
    if case.config.solver.timestep.mode != "fixed":
        raise NotImplementedError(
            f"timestep execution is not implemented: {case.config.solver.timestep.mode}"
        )

    solver_records: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    step_index = 0

    if requires_initial_equilibrium(case):
        specs, conditions = build_conditions(case, system, state, "initial_equilibrium")
        solver = _equilibrium_solver(system, specs)
        result, wall_time_s = _timed_solve(solver, state, conditions=conditions)
        _require_solver_success(result, "initial equilibrium")
        solver_records.append(
            _solver_record(
                step_index=step_index,
                stage="initial_equilibrium",
                time_start_s=0.0,
                time_end_s=0.0,
                dt_s=0.0,
                result=result,
                wall_time_s=wall_time_s,
            )
        )
        step_index += 1

    if case.config.solver.workflow.mode == "equilibrium_only":
        record = solver_records[-1]
        initial_state = snapshot_state(state)
        rows.append(collect_row(case, state, record, initial_state, kinec_params))
        return rows, solver_records, initial_state

    specs, conditions = build_conditions(case, system, state, "kinetic_steps")
    kinetic_solver = _kinetics_solver(system, specs)
    staged_closed_workflow = (
        case.config.solver.workflow.mode
        == "fixed_fugacity_initial_equilibrium_then_closed_kinetics"
    )
    if case.config.solver.workflow.precondition_kinetics and not staged_closed_workflow:
        result, wall_time_s = _timed_precondition(kinetic_solver, state, conditions)
        _require_solver_success(result, "kinetics precondition")
        solver_records.append(
            _solver_record(
                step_index=step_index,
                stage="kinetics_precondition",
                time_start_s=0.0,
                time_end_s=0.0,
                dt_s=0.0,
                result=result,
                wall_time_s=wall_time_s,
            )
        )
        step_index += 1

    initial_state = snapshot_state(state)
    initial_record = _unsolved_record(step_index, "initial_state", 0.0)
    rows.append(collect_row(case, state, initial_record, initial_state, kinec_params))

    time_s = 0.0
    for dt_s in case.step_sizes_s():
        start_s = time_s
        result, wall_time_s = _timed_solve(kinetic_solver, state, dt_s=dt_s, conditions=conditions)
        time_s += dt_s
        _require_solver_success(result, f"kinetic step at {time_s} s")
        record = _solver_record(
            step_index=step_index,
            stage="kinetic_step",
            time_start_s=start_s,
            time_end_s=time_s,
            dt_s=dt_s,
            result=result,
            wall_time_s=wall_time_s,
        )
        step_index += 1
        solver_records.append(record)
        rows.append(collect_row(case, state, record, initial_state, kinec_params))

    return rows, solver_records, initial_state


def _equilibrium_solver(system: Any, specs: Any | None) -> Any:
    return rkt.EquilibriumSolver(specs) if specs is not None else rkt.EquilibriumSolver(system)


def _kinetics_solver(system: Any, specs: Any | None) -> Any:
    return rkt.KineticsSolver(specs) if specs is not None else rkt.KineticsSolver(system)


def _timed_precondition(solver: Any, state: Any, conditions: Any | None) -> tuple[Any, float]:
    start = perf_counter()
    result = solver.precondition(state, conditions) if conditions is not None else solver.precondition(state)
    return result, perf_counter() - start


def _timed_solve(
    solver: Any,
    state: Any,
    *,
    dt_s: float | None = None,
    conditions: Any | None = None,
) -> tuple[Any, float]:
    start = perf_counter()
    if dt_s is None:
        result = solver.solve(state, conditions) if conditions is not None else solver.solve(state)
    else:
        result = solver.solve(state, dt_s, conditions) if conditions is not None else solver.solve(state, dt_s)
    return result, perf_counter() - start


def _require_solver_success(result: Any, stage: str) -> None:
    if not result.succeeded():
        raise RuntimeError(f"Reaktoro solver failed during {stage}")


def _solver_record(
    *,
    step_index: int,
    stage: str,
    time_start_s: float,
    time_end_s: float,
    dt_s: float,
    result: Any,
    wall_time_s: float,
) -> dict[str, Any]:
    return {
        "step_index": step_index,
        "time_start_s": float(time_start_s),
        "time_end_s": float(time_end_s),
        "dt_s": float(dt_s),
        "stage": stage,
        "accepted": True,
        "solver_succeeded": bool(result.succeeded()),
        "iterations": int(result.iterations()),
        "wall_time_s": float(wall_time_s),
        "failure_reason": "",
    }


def _unsolved_record(step_index: int, stage: str, time_s: float) -> dict[str, Any]:
    return {
        "step_index": step_index,
        "time_start_s": float(time_s),
        "time_end_s": float(time_s),
        "dt_s": 0.0,
        "stage": stage,
        "accepted": True,
        "solver_succeeded": None,
        "iterations": None,
        "wall_time_s": 0.0,
        "failure_reason": "",
    }

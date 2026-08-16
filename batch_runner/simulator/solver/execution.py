"""High-level equilibrium and kinetic solver orchestration."""

from typing import Any, Callable

from batch_runner.config import AdaptiveTimestepConfig, ResolvedCase
from batch_runner.simulator.chemistry.observations import collect_row
from batch_runner.simulator.chemistry.conditions import build_conditions

from .acceptance import evaluate_trial
from .adaptive import run_adaptive_timesteps
from .calls import kinetics_solver
from .equilibrium import (
    finish_equilibrium_only,
    precondition_kinetics,
    run_initial_equilibrium,
)
from .fixed import run_fixed_timesteps
from .records import unsolved_record
from .runtime import SolverRun
from .state import snapshot_state


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
    run = SolverRun(
        case=case,
        system=system,
        state=state,
        emit_row=row_ready or (lambda _row: None),
        emit_record=solver_record_ready or (lambda _record: None),
        emit_boundary=boundary_row_ready or (lambda _which, _row: None),
        emit_checkpoint=checkpoint_ready or (lambda _record, _state: None),
        is_cancelled=cancel_requested or (lambda: False),
        collect_row=collect_row,
        snapshot_state=snapshot_state,
        evaluate_trial=evaluate_trial,
    )

    stopped = run_initial_equilibrium(run)
    if stopped is not None:
        return stopped

    if case.config.solver.workflow.mode == "equilibrium_only":
        return finish_equilibrium_only(run)

    specs, run.conditions = build_conditions(
        case, system, state, "kinetic_steps"
    )
    run.kinetic_solver = kinetics_solver(system, specs)
    stopped = precondition_kinetics(run)
    if stopped is not None:
        return stopped

    run.initial_state = snapshot_state(state)
    initial_record = unsolved_record(run.step_index, "initial_state", 0.0)
    if run.is_cancelled():
        return run.initial_state, run.cancelled("before_initial_output_extraction")
    initial_row = collect_row(case, state, initial_record, run.initial_state)
    run.emit_boundary("initial", initial_row)
    if run.output_due(0.0):
        run.emit_row(initial_row)

    if run.timestep.mode == "fixed":
        return run_fixed_timesteps(run)
    if not isinstance(run.timestep, AdaptiveTimestepConfig):
        raise TypeError(f"unsupported timestep config: {type(run.timestep).__name__}")
    return run_adaptive_timesteps(run)

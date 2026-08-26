"""Connect prepared chemistry to the selected explicit solver workflow.

This is the stable solver entry point used by simulation orchestration.  It
creates shared run state, performs optional initial equilibrium, emits the
accepted initial observation, and dispatches by the validated timestep model.
Callbacks expose records and accepted states but do not participate in solver
acceptance or timestep decisions.
"""

from typing import Any, Callable

from batch_runner.config import (
    AdaptiveErrorControlledTimestepConfig,
    AdaptiveTimestepConfig,
    ResolvedCase,
)
from batch_runner.simulator.chemistry.observations import collect_row
from batch_runner.simulator.chemistry.conditions import build_conditions

from .adaptive import run_adaptive_timesteps
from .calls import kinetics_solver
from .equilibrium import (
    finish_equilibrium_only,
    run_initial_equilibrium,
)
from .error_controlled import run_error_controlled_timesteps
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
    initial_reaction_rate_fields: Callable[[Any], dict[str, Any]] | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Execute the configured equilibrium or kinetic workflow.

    Args:
        case: Fully resolved configuration and exact schedules.
        system: Constructed Reaktoro ``ChemicalSystem``.
        state: Mutable initial ``ChemicalState``; accepted solves update it.
        row_ready: Receives scheduled accepted-state observations.
        solver_record_ready: Receives every solver attempt record.
        boundary_row_ready: Receives accepted initial and final observations.
        checkpoint_ready: Receives due accepted states and their records.
        cancel_requested: Cooperative cancellation predicate.
        initial_reaction_rate_fields: Optional isolated evaluator used when the
            live custom callback must not be disturbed at time zero.

    Returns:
        tuple[Any, dict[str, Any]]: ``(initial_state, progress)``.  ``progress``
            records completion or controlled solver/cancellation termination;
            unexpected callback or observation exceptions propagate to
            simulation lifecycle handling.

    Raises:
        TypeError: The validated timestep object has no supported executor.
    """
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
    )

    stopped = run_initial_equilibrium(run)
    if stopped is not None:
        return stopped

    if case.config.solver.workflow.mode == "equilibrium_only":
        return finish_equilibrium_only(run)

    run.kinetic_specs, run.conditions = build_conditions(
        case, system, state, "kinetic_steps"
    )
    run.kinetic_solver = kinetics_solver(system, run.kinetic_specs)
    run.initial_state = snapshot_state(state)

    initial_record = unsolved_record(run.step_index, "initial_state", 0.0)
    if run.is_cancelled():
        return run.initial_state, run.cancelled("before_initial_output_extraction")
    if initial_reaction_rate_fields is None:
        initial_row = collect_row(case, state, initial_record, run.initial_state)
    else:
        initial_row = collect_row(
            case,
            state,
            initial_record,
            run.initial_state,
            include_reaction_rates=False,
        )
        initial_row.update(initial_reaction_rate_fields(state))
    run.emit_boundary("initial", initial_row)
    if run.output_due(0.0):
        run.emit_row(initial_row)

    if run.timestep.mode == "fixed":
        return run_fixed_timesteps(run)
    if isinstance(run.timestep, AdaptiveTimestepConfig):
        return run_adaptive_timesteps(run)
    if isinstance(run.timestep, AdaptiveErrorControlledTimestepConfig):
        return run_error_controlled_timesteps(run)
    raise TypeError(f"unsupported timestep config: {type(run.timestep).__name__}")

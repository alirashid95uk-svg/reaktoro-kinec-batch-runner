"""Simulation lifecycle diagnostics and failed-result construction."""

from datetime import datetime, timezone
from typing import Any

import reaktoro as rkt

from batch_runner import OUTPUT_SCHEMA_VERSION
from batch_runner.config import ResolvedCase

from .results import SimulationResult
from .chemistry.conditions import requires_initial_equilibrium


def build_diagnostics(
    case: ResolvedCase,
    run_started_at: str,
    system: Any | None,
    result_rows: int,
    solver_progress: dict[str, Any],
    database_sha256: str | None,
    kinetic_parameter_sha256: str | None,
) -> dict[str, Any]:
    estimated_solver_calls = (
        case.internal_step_count + int(requires_initial_equilibrium(case))
        if case.config.solver.timestep.mode == "fixed"
        else None
    )
    final_time_s = solver_progress["final_time_reached_s"]
    return {
        "case_name": case.config.case.name,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "stale_output_policy": "fresh output_dir required; legacy results.csv is rejected",
        "reaktoro_version": rkt.__version__,
        "database_source": case.config.database.source,
        "database_value": str(case.database_path or case.config.database.name),
        "database_sha256": database_sha256,
        "kinetic_model": case.config.kinetics.model,
        "kinetic_parameter_path": str(case.kinetics_path) if case.kinetics_path else None,
        "kinetic_parameter_sha256": kinetic_parameter_sha256,
        "system_counts": (
            {
                "elements": len(system.elements()),
                "species": len(system.species()),
                "phases": len(system.phases()),
                "reactions": len(system.reactions()),
                "surfaces": len(system.surfaces()),
            }
            if system is not None
            else None
        ),
        "requested_internal_steps": (
            case.internal_step_count
            if case.config.solver.timestep.mode == "fixed"
            else None
        ),
        "base_internal_steps": (
            case.base_internal_step_count
            if case.config.solver.timestep.mode == "fixed"
            else None
        ),
        "max_internal_steps": case.config.solver.timestep.max_internal_steps,
        "minimum_possible_accepted_steps": case.minimum_accepted_steps,
        "estimated_solver_calls": estimated_solver_calls,
        "estimated_result_rows": case.requested_output_row_count,
        "requested_output_rows": case.requested_output_row_count,
        "requested_checkpoint_count": len(case.checkpoint_times_s),
        "result_rows": result_rows,
        "partial_run": (
            not solver_progress["simulation_completed"] and final_time_s > 0.0
        ),
        "partial_outputs_written": False,
        "scientific_outputs_omitted": False,
        "output_completeness": {"status": "not_written", "files_written": []},
        **solver_progress,
        "final_time_reached_days": final_time_s / 86400.0,
        "solver_backend_type": "standard",
        "timestep_mode": case.config.solver.timestep.mode,
        "workflow_mode": case.config.solver.workflow.mode,
        "co2_runtime_workflow": case.config.solver.workflow.mode,
        "redox_enabled_runtime": case.config.redox.enabled,
        "redox_apply_during_runtime": case.config.redox.apply_during,
        "warnings": [],
        "run_started_at": run_started_at,
        "run_finished_at": datetime.now(timezone.utc).isoformat(),
    }


def exception_progress(
    stage: str,
    error: Exception,
    *,
    final_time_s: float = 0.0,
    accepted_steps: int = 0,
    rejected_steps: int = 0,
    internal_attempts: int = 0,
    solver_failed_attempts: int = 0,
    checkpoint_count: int = 0,
) -> dict[str, Any]:
    return {
        "simulation_completed": False,
        "failed_stage": stage,
        "exception_type": type(error).__name__,
        "error_message": str(error),
        "termination_reason": "lifecycle_exception",
        "final_time_reached_s": final_time_s,
        "number_of_accepted_steps": accepted_steps,
        "number_of_rejected_steps": rejected_steps,
        "number_of_failed_steps": rejected_steps,
        "smallest_dt_s": None,
        "largest_dt_s": None,
        "average_dt_s": None,
        "failed_attempt_target_time_s": None,
        "failed_attempt_dt_s": None,
        "accepted_state_restored": None,
        "checkpoint_count": checkpoint_count,
        "number_of_internal_attempts": internal_attempts,
        "number_of_solver_failed_attempts": solver_failed_attempts,
        "retries_at_final_accepted_time": None,
        "rejection_reason_counts": {},
        "cancellation_requested": False,
        "cancellation_boundary": None,
    }


def failed_result(
    case: ResolvedCase,
    run_started_at: str,
    stage: str,
    error: Exception,
    *,
    kinetic_mapping: list[dict[str, Any]],
    system: Any | None,
    state: Any | None,
    exception_traceback: str | None,
    source_config_sha256: str | None,
    database_sha256: str | None,
    kinetic_parameter_sha256: str | None,
) -> SimulationResult:
    return SimulationResult(
        rows=[],
        kinetic_mapping=kinetic_mapping,
        solver_history=[],
        diagnostics=build_diagnostics(
            case,
            run_started_at,
            system,
            0,
            exception_progress(stage, error),
            database_sha256,
            kinetic_parameter_sha256,
        ),
        initial_state=None,
        final_state=state,
        exception_traceback=exception_traceback,
        source_config_sha256=source_config_sha256,
        database_sha256=database_sha256,
        kinetic_parameter_sha256=kinetic_parameter_sha256,
    )

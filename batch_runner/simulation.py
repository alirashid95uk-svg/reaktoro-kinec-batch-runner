"""Orchestrate one Reaktoro batch simulation."""

from __future__ import annotations

import json
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import reaktoro as rkt

from batch_runner import OUTPUT_SCHEMA_VERSION
from batch_runner.config import ResolvedCase
from batch_runner.simulator.database import load_database
from batch_runner.simulator.kinetics import load_kinetic_parameters
from batch_runner.simulator.mapping import build_kinetic_mapping, require_valid_kinetic_mapping
from batch_runner.simulator.solver import execute_solver
from batch_runner.simulator.state_builder import build_chemical_state
from batch_runner.simulator.system_builder import build_chemical_system
from batch_runner.simulator.workflows import requires_initial_equilibrium


@dataclass
class SimulationResult:
    rows: list[dict[str, Any]] | None
    kinetic_mapping: list[dict[str, Any]]
    solver_history: list[dict[str, Any]] | None
    diagnostics: dict[str, Any]
    initial_state: Any
    final_state: Any
    row_stream_path: Path | None = None
    solver_history_stream_path: Path | None = None
    first_row: dict[str, Any] | None = None
    last_row: dict[str, Any] | None = None

    def iter_rows(self):
        if self.rows is not None:
            yield from self.rows
        elif self.row_stream_path is not None:
            yield from _read_json_lines(self.row_stream_path)

    def iter_solver_history(self):
        if self.solver_history is not None:
            yield from self.solver_history
        elif self.solver_history_stream_path is not None:
            yield from _read_json_lines(self.solver_history_stream_path)

    @property
    def initial_row(self) -> dict[str, Any]:
        row = self.rows[0] if self.rows else self.first_row
        if row is None:
            raise ValueError("simulation has no accepted result rows")
        return row

    @property
    def final_row(self) -> dict[str, Any]:
        row = self.rows[-1] if self.rows else self.last_row
        if row is None:
            raise ValueError("simulation has no accepted result rows")
        return row

    def cleanup_streams(self) -> None:
        for path in (self.row_stream_path, self.solver_history_stream_path):
            if path is not None:
                path.unlink(missing_ok=True)


def run_simulation(
    case: ResolvedCase,
    mapping_ready: Callable[[list[dict[str, Any]]], None] | None = None,
) -> SimulationResult:
    run_started_at = datetime.now(timezone.utc).isoformat()
    kinetic_mapping: list[dict[str, Any]] = []
    system = None
    state = None
    stage = "database_loading"
    try:
        database = load_database(case)
        stage = "kinetics_loading"
        params = load_kinetic_parameters(case)
        stage = "mapping"
        kinetic_mapping = build_kinetic_mapping(case, database, params)
        if mapping_ready is not None:
            stage = "output_writing"
            mapping_ready(kinetic_mapping)
            stage = "mapping"
        require_valid_kinetic_mapping(kinetic_mapping)
        stage = "system_construction"
        system = build_chemical_system(case, database, params)
        stage = "state_construction"
        state = build_chemical_state(case, system)
    except Exception as error:
        return _failed_result(
            case,
            run_started_at,
            stage,
            error,
            kinetic_mapping=kinetic_mapping,
            system=system,
            state=state,
        )

    row_stream_path = case.output_dir / ".timeseries.jsonl"
    solver_history_stream_path = case.output_dir / ".solver_history.jsonl"
    first_row = None
    last_row = None
    result_rows = 0
    checkpoint_index = 0
    accepted_time_s = 0.0
    accepted_steps = 0
    rejected_steps = 0
    internal_attempts = 0
    solver_failed_attempts = 0
    initial_state = None

    try:
        stage = "output_writing"
        case.output_dir.mkdir(parents=True, exist_ok=True)
        with ExitStack() as stack:
            row_stream = stack.enter_context(row_stream_path.open("w", encoding="utf-8"))
            solver_history_stream = stack.enter_context(
                solver_history_stream_path.open("w", encoding="utf-8")
            )
            checkpoint_stream = None
            checkpoint_dir = case.output_dir / "checkpoints"
            if case.config.solver.timestep.checkpoint_schedule.enabled:
                checkpoint_dir.mkdir()
                checkpoint_stream = stack.enter_context(
                    (checkpoint_dir / "index.jsonl").open("w", encoding="utf-8")
                )

            def row_ready(row: dict[str, Any]) -> None:
                nonlocal first_row, last_row, result_rows, stage
                stage = "output_writing"
                json.dump(row, row_stream, separators=(",", ":"))
                row_stream.write("\n")
                first_row = first_row or row
                last_row = row
                result_rows += 1
                stage = "solver_execution"

            def boundary_row_ready(which: str, row: dict[str, Any]) -> None:
                nonlocal first_row, last_row
                if which == "initial":
                    first_row = row
                else:
                    last_row = row

            def solver_record_ready(record: dict[str, Any]) -> None:
                nonlocal stage, accepted_time_s, accepted_steps, rejected_steps
                nonlocal internal_attempts, solver_failed_attempts
                if record["dt_s"] > 0.0:
                    internal_attempts += 1
                    if record["accepted"]:
                        accepted_steps += 1
                        accepted_time_s = record["time_end_s"]
                    else:
                        rejected_steps += 1
                    if record["solver_succeeded"] is False:
                        solver_failed_attempts += 1
                stage = "output_writing"
                json.dump(record, solver_history_stream, separators=(",", ":"))
                solver_history_stream.write("\n")
                stage = "solver_execution"

            def checkpoint_ready(record: dict[str, Any], accepted_state: Any) -> None:
                nonlocal checkpoint_index, stage
                stage = "output_writing"
                checkpoint_index += 1
                state_name = f"checkpoint_{checkpoint_index:06d}_state.txt"
                accepted_state.output(str(checkpoint_dir / state_name))
                json.dump(
                    {
                        "checkpoint_index": checkpoint_index,
                        "time_s": record["time_end_s"],
                        "dt_s": record["dt_s"],
                        "state_file": state_name,
                    },
                    checkpoint_stream,
                    separators=(",", ":"),
                )
                checkpoint_stream.write("\n")
                stage = "solver_execution"

            stage = "solver_execution"
            initial_state, solver_progress = execute_solver(
                case,
                system,
                state,
                row_ready=row_ready,
                solver_record_ready=solver_record_ready,
                boundary_row_ready=boundary_row_ready,
                checkpoint_ready=checkpoint_ready,
            )
            stage = "output_writing"
    except Exception as error:
        solver_progress = _exception_progress(
            stage,
            error,
            final_time_s=accepted_time_s,
            accepted_steps=accepted_steps,
            rejected_steps=rejected_steps,
            internal_attempts=internal_attempts,
            solver_failed_attempts=solver_failed_attempts,
            checkpoint_count=checkpoint_index,
        )

    diagnostics = _build_diagnostics(
        case,
        run_started_at,
        system,
        result_rows,
        solver_progress,
    )
    return SimulationResult(
        rows=None,
        kinetic_mapping=kinetic_mapping,
        solver_history=None,
        diagnostics=diagnostics,
        initial_state=initial_state,
        final_state=state,
        row_stream_path=row_stream_path,
        solver_history_stream_path=solver_history_stream_path,
        first_row=first_row,
        last_row=last_row,
    )


def _build_diagnostics(
    case: ResolvedCase,
    run_started_at: str,
    system: Any | None,
    result_rows: int,
    solver_progress: dict[str, Any],
) -> dict[str, Any]:
    estimated_solver_calls = (
        case.internal_step_count + int(requires_initial_equilibrium(case))
        if case.config.solver.timestep.mode == "fixed"
        else None
    )
    if estimated_solver_calls is not None and (
        case.config.kinetics.enabled
        and case.config.solver.workflow.precondition_kinetics
        and case.config.solver.workflow.mode
        != "fixed_fugacity_initial_equilibrium_then_closed_kinetics"
    ):
        estimated_solver_calls += 1
    final_time_s = solver_progress["final_time_reached_s"]
    return {
        "case_name": case.config.case.name,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "stale_output_policy": "fresh output_dir required; legacy results.csv is rejected",
        "reaktoro_version": rkt.__version__,
        "database_source": case.config.database.source,
        "database_value": str(case.database_path or case.config.database.name),
        "kinetic_model": case.config.kinetics.model,
        "kinetic_parameter_path": str(case.kinetics_path) if case.kinetics_path else None,
        "kinetic_parameter_sha256": case.kinetic_parameter_sha256,
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
            case.internal_step_count if case.config.solver.timestep.mode == "fixed" else None
        ),
        "base_internal_steps": (
            case.base_internal_step_count if case.config.solver.timestep.mode == "fixed" else None
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
        "kinetic_precondition_requested": case.config.solver.workflow.precondition_kinetics,
        "warnings": [],
        "run_started_at": run_started_at,
        "run_finished_at": datetime.now(timezone.utc).isoformat(),
    }


def _exception_progress(
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
        "kinetic_precondition_applied": False,
        "failed_attempt_target_time_s": None,
        "failed_attempt_dt_s": None,
        "accepted_state_restored": None,
        "checkpoint_count": checkpoint_count,
        "number_of_internal_attempts": internal_attempts,
        "number_of_solver_failed_attempts": solver_failed_attempts,
        "retries_at_final_accepted_time": None,
        "rejection_reason_counts": {},
    }


def _failed_result(
    case: ResolvedCase,
    run_started_at: str,
    stage: str,
    error: Exception,
    *,
    kinetic_mapping: list[dict[str, Any]],
    system: Any | None,
    state: Any | None,
) -> SimulationResult:
    return SimulationResult(
        rows=[],
        kinetic_mapping=kinetic_mapping,
        solver_history=[],
        diagnostics=_build_diagnostics(
            case,
            run_started_at,
            system,
            0,
            _exception_progress(stage, error),
        ),
        initial_state=None,
        final_state=state,
    )


def _read_json_lines(path: Path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)

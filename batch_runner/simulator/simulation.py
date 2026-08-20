"""Orchestrate one Reaktoro batch simulation."""

from __future__ import annotations

import hashlib
import json
import traceback
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from batch_runner.config import AdaptiveErrorControlledTimestepConfig, ResolvedCase

from .chemistry import (
    build_chemical_state,
    build_chemical_system,
    collect_row,
    load_database,
)
from .diagnostics import build_diagnostics, exception_progress, failed_result
from .kinetics import (
    build_kinetic_mapping,
    load_kinetic_parameters,
    require_valid_kinetic_mapping,
)
from .results import PreparedSimulation, SimulationResult
from .solver import execute_solver
from .solver.records import unsolved_record


def prepare_simulation(
    case: ResolvedCase,
    mapping_ready: Callable[[list[dict[str, Any]]], None] | None = None,
    event_ready: Callable[[str, dict[str, Any]], None] | None = None,
) -> PreparedSimulation:
    """Build and validate the configured chemistry without starting a solver."""
    kinetic_mapping: list[dict[str, Any]] = []
    system = None
    state = None
    database_sha256 = None
    kinetic_parameter_sha256 = None
    source_config_sha256 = case.source_config_sha256
    stage = "database_loading"
    emit_event = event_ready or (lambda _event_type, _payload: None)
    try:
        emit_event("stage_started", {"stage": stage})
        database_sha256 = _sha256(case.database_path)
        database = load_database(case)
        emit_event("stage_completed", {"stage": stage})
        stage = "kinetics_loading"
        emit_event("stage_started", {"stage": stage})
        kinetic_parameter_sha256 = _sha256(case.kinetics_path)
        params = load_kinetic_parameters(case)
        emit_event("stage_completed", {"stage": stage})
        stage = "mapping"
        emit_event("stage_started", {"stage": stage})
        kinetic_mapping = build_kinetic_mapping(case, database, params)
        emit_event("mapping_result", {"mapping": kinetic_mapping})
        if mapping_ready is not None:
            stage = "output_writing"
            mapping_ready(kinetic_mapping)
            stage = "mapping"
        require_valid_kinetic_mapping(kinetic_mapping)
        emit_event("stage_completed", {"stage": stage})
        stage = "system_construction"
        emit_event("stage_started", {"stage": stage})
        system = build_chemical_system(case, database, params)
        emit_event("stage_completed", {"stage": stage})
        stage = "state_construction"
        emit_event("stage_started", {"stage": stage})
        state = build_chemical_state(case, system)
        emit_event("stage_completed", {"stage": stage})
    except Exception as error:
        emit_event(
            "validation_issue",
            {
                "stage": stage,
                "exception_type": type(error).__name__,
                "error_message": str(error),
            },
        )
        return PreparedSimulation(
            kinetic_mapping=kinetic_mapping,
            system=system,
            state=state,
            failed_stage=stage,
            error=error,
            exception_traceback=traceback.format_exc(),
            source_config_sha256=source_config_sha256,
            database_sha256=database_sha256,
            kinetic_parameter_sha256=kinetic_parameter_sha256,
        )
    return PreparedSimulation(
        kinetic_mapping,
        system,
        state,
        source_config_sha256=source_config_sha256,
        database_sha256=database_sha256,
        kinetic_parameter_sha256=kinetic_parameter_sha256,
    )


def preflight_case(
    case: ResolvedCase,
    event_ready: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    prepared = prepare_simulation(case, event_ready=event_ready)
    return {
        "ready": prepared.ready,
        "case_name": case.config.case.name,
        "failed_stage": prepared.failed_stage,
        "exception_type": type(prepared.error).__name__ if prepared.error else None,
        "error_message": str(prepared.error) if prepared.error else None,
        "kinetic_mapping": prepared.kinetic_mapping,
        "database_sha256": prepared.database_sha256,
        "kinetic_parameter_sha256": prepared.kinetic_parameter_sha256,
        "technical_traceback": prepared.exception_traceback,
    }


def run_simulation(
    case: ResolvedCase,
    mapping_ready: Callable[[list[dict[str, Any]]], None] | None = None,
    event_ready: Callable[[str, dict[str, Any]], None] | None = None,
    progress_ready: Callable[[dict[str, Any]], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
    accepted_row_ready: Callable[[dict[str, Any]], None] | None = None,
    accepted_state_ready: Callable[[Any, dict[str, Any]], None] | None = None,
) -> SimulationResult:
    run_started_at = datetime.now(timezone.utc).isoformat()
    prepared = prepare_simulation(case, mapping_ready, event_ready)
    if not prepared.ready:
        assert prepared.error is not None and prepared.failed_stage is not None
        return failed_result(
            case,
            run_started_at,
            prepared.failed_stage,
            prepared.error,
            kinetic_mapping=prepared.kinetic_mapping,
            system=prepared.system,
            state=prepared.state,
            exception_traceback=prepared.exception_traceback,
            source_config_sha256=prepared.source_config_sha256,
            database_sha256=prepared.database_sha256,
            kinetic_parameter_sha256=prepared.kinetic_parameter_sha256,
        )
    kinetic_mapping = prepared.kinetic_mapping
    system = prepared.system
    state = prepared.state

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
    reaktoro_solve_calls = 0
    initial_state = None
    exception_traceback = None

    try:
        raw_initial_state = None
        raw_initial_row = None
        if isinstance(
            case.config.solver.timestep, AdaptiveErrorControlledTimestepConfig
        ):
            raw_initial_state = state
            raw_initial_row = collect_row(
                case,
                state,
                unsolved_record(0, "initial_state", 0.0),
                state,
            )
            database = load_database(case)
            params = load_kinetic_parameters(case)
            system = build_chemical_system(case, database, params)
            state = build_chemical_state(case, system)

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
                row_stream.flush()
                first_row = first_row or row
                last_row = row
                result_rows += 1
                if accepted_row_ready is not None:
                    accepted_row_ready(row)
                stage = "solver_execution"

            def boundary_row_ready(which: str, row: dict[str, Any]) -> None:
                nonlocal first_row, last_row
                if which == "initial":
                    first_row = row
                    if accepted_state_ready is not None:
                        accepted_state_ready(
                            state,
                            {
                                "time_start_s": 0.0,
                                "time_end_s": 0.0,
                                "dt_s": 0.0,
                                "stage": "initial_state",
                                "accepted": True,
                                "solver_succeeded": None,
                            },
                        )
                else:
                    last_row = row

            def solver_record_ready(record: dict[str, Any]) -> None:
                nonlocal stage, accepted_time_s, accepted_steps, rejected_steps
                nonlocal internal_attempts, solver_failed_attempts, reaktoro_solve_calls
                reaktoro_solve_calls += int(
                    record.get("reaktoro_solve_calls")
                    or record.get("solver_succeeded") is not None
                )
                if record["dt_s"] > 0.0:
                    internal_attempts += 1
                    if record["accepted"]:
                        accepted_steps += 1
                        accepted_time_s = record["time_end_s"]
                    else:
                        rejected_steps += 1
                if record["solver_succeeded"] is False and record["dt_s"] > 0.0:
                    solver_failed_attempts += 1
                stage = "output_writing"
                json.dump(record, solver_history_stream, separators=(",", ":"))
                solver_history_stream.write("\n")
                solver_history_stream.flush()
                if (
                    accepted_state_ready is not None
                    and record["accepted"]
                    and record["dt_s"] > 0.0
                ):
                    accepted_state_ready(state, record)
                if progress_ready is not None:
                    progress_ready(
                        {
                            "accepted_time_s": accepted_time_s,
                            "requested_duration_s": case.duration_s,
                            "current_dt_s": record["dt_s"],
                            "next_dt_s": record.get("next_dt_s"),
                            "accepted_attempts": accepted_steps,
                            "rejected_attempts": rejected_steps,
                            "latest_accepted": record["accepted"],
                            "solver_succeeded": record["solver_succeeded"],
                            "latest_reason": record.get("failure_reason") or None,
                            "solver_iterations": record.get("iterations"),
                            "stage": record["stage"],
                        }
                    )
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
                checkpoint_stream.flush()
                if event_ready is not None:
                    event_ready(
                        "checkpoint_written",
                        {
                            "checkpoint_index": checkpoint_index,
                            "time_s": record["time_end_s"],
                            "state_file": str(checkpoint_dir / state_name),
                        },
                    )
                stage = "solver_execution"

            stage = "solver_execution"
            if event_ready is not None:
                event_ready("stage_started", {"stage": stage})
            solver_kwargs = {
                "row_ready": row_ready,
                "solver_record_ready": solver_record_ready,
                "boundary_row_ready": boundary_row_ready,
                "checkpoint_ready": checkpoint_ready,
            }
            if raw_initial_state is not None:
                solver_kwargs["raw_initial_state"] = raw_initial_state
                solver_kwargs["raw_initial_row"] = raw_initial_row
            if cancel_requested is not None:
                solver_kwargs["cancel_requested"] = cancel_requested
            initial_state, solver_progress = execute_solver(
                case, system, state, **solver_kwargs
            )
            if event_ready is not None:
                event_ready(
                    "stage_completed",
                    {
                        "stage": "solver_execution",
                        "termination_reason": solver_progress["termination_reason"],
                        "final_time_reached_s": solver_progress["final_time_reached_s"],
                    },
                )
            stage = "output_writing"
    except Exception as error:
        exception_traceback = traceback.format_exc()
        solver_progress = exception_progress(
            stage,
            error,
            final_time_s=accepted_time_s,
            accepted_steps=accepted_steps,
            rejected_steps=rejected_steps,
            internal_attempts=internal_attempts,
            solver_failed_attempts=solver_failed_attempts,
            reaktoro_solve_calls=reaktoro_solve_calls,
            checkpoint_count=checkpoint_index,
        )

    diagnostics = build_diagnostics(
        case,
        run_started_at,
        system,
        result_rows,
        solver_progress,
        prepared.database_sha256,
        prepared.kinetic_parameter_sha256,
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
        exception_traceback=exception_traceback,
        source_config_sha256=prepared.source_config_sha256,
        database_sha256=prepared.database_sha256,
        kinetic_parameter_sha256=prepared.kinetic_parameter_sha256,
    )



def _sha256(path: Path | None) -> str | None:
    if path is None:
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

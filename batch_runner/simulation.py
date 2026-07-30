"""Orchestrate one Reaktoro batch simulation."""

from __future__ import annotations

import json
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import reaktoro as rkt

from batch_runner.Kinect_Custom_Rates import KinecParams
from batch_runner.config import ResolvedCase
from batch_runner.simulator.database import load_database
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
    database = load_database(case)
    params = KinecParams.local(case.kinetics_path) if case.config.kinetics.enabled else None
    kinetic_mapping = build_kinetic_mapping(case, database, params)
    if mapping_ready is not None:
        mapping_ready(kinetic_mapping)
    require_valid_kinetic_mapping(kinetic_mapping)

    system = build_chemical_system(case, database, params)
    state = build_chemical_state(case, system)
    case.output_dir.mkdir(parents=True, exist_ok=True)
    row_stream_path = case.output_dir / ".timeseries.jsonl"
    solver_history_stream_path = case.output_dir / ".solver_history.jsonl"
    first_row = None
    last_row = None
    result_rows = 0
    checkpoint_index = 0

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
            nonlocal first_row, last_row, result_rows
            json.dump(row, row_stream, separators=(",", ":"))
            row_stream.write("\n")
            first_row = first_row or row
            last_row = row
            result_rows += 1

        def boundary_row_ready(which: str, row: dict[str, Any]) -> None:
            nonlocal first_row, last_row
            if which == "initial":
                first_row = row
            else:
                last_row = row

        def solver_record_ready(record: dict[str, Any]) -> None:
            json.dump(record, solver_history_stream, separators=(",", ":"))
            solver_history_stream.write("\n")

        def checkpoint_ready(record: dict[str, Any], accepted_state: Any) -> None:
            nonlocal checkpoint_index
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

        initial_state, solver_progress = execute_solver(
            case,
            system,
            state,
            params,
            row_ready=row_ready,
            solver_record_ready=solver_record_ready,
            boundary_row_ready=boundary_row_ready,
            checkpoint_ready=checkpoint_ready,
        )

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

    diagnostics = {
        "case_name": case.config.case.name,
        "output_schema_version": "objective1_audit_v2",
        "stale_output_policy": "fresh output_dir required; legacy results.csv is rejected",
        "reaktoro_version": rkt.__version__,
        "database_source": case.config.database.source,
        "database_value": str(case.database_path or case.config.database.name),
        "system_counts": {
            "elements": len(system.elements()),
            "species": len(system.species()),
            "phases": len(system.phases()),
            "reactions": len(system.reactions()),
            "surfaces": len(system.surfaces()),
        },
        "requested_internal_steps": (
            case.internal_step_count if case.config.solver.timestep.mode == "fixed" else None
        ),
        "base_internal_steps": (
            case.base_internal_step_count if case.config.solver.timestep.mode == "fixed" else None
        ),
        "max_internal_steps": case.config.solver.timestep.max_internal_steps,
        "estimated_solver_calls": estimated_solver_calls,
        "estimated_result_rows": case.requested_output_row_count,
        "requested_output_rows": case.requested_output_row_count,
        "requested_checkpoint_count": len(case.checkpoint_times_s),
        "result_rows": result_rows,
        "partial_run": (
            not solver_progress["simulation_completed"]
            and solver_progress["final_time_reached_s"] > 0.0
        ),
        "partial_outputs_written": False,
        "scientific_outputs_omitted": False,
        **solver_progress,
        "final_time_reached_days": solver_progress["final_time_reached_s"] / 86400.0,
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


def _read_json_lines(path: Path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)

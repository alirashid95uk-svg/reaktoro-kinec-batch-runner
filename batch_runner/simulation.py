"""Orchestrate one Reaktoro batch simulation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import reaktoro as rkt

from batch_runner.Kinect_Custom_Rates import KinecParams
from batch_runner.config import ResolvedCase
from batch_runner.simulator.database import load_database
from batch_runner.simulator.mapping import build_kinetic_mapping, require_valid_kinetic_mapping
from batch_runner.simulator.solver import execute_solver
from batch_runner.simulator.state_builder import build_chemical_state
from batch_runner.simulator.system_builder import build_chemical_system


@dataclass
class SimulationResult:
    rows: list[dict[str, Any]]
    kinetic_mapping: list[dict[str, Any]]
    solver_history: list[dict[str, Any]]
    diagnostics: dict[str, Any]
    initial_state: Any
    final_state: Any


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
    rows, solver_records, initial_state = execute_solver(case, system, state, params)

    accepted_records = [record for record in solver_records if record["accepted"]]
    dt_values = [record["dt_s"] for record in accepted_records if record["dt_s"] > 0]

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
        "result_rows": len(rows),
        "simulation_completed": True,
        "failed_stage": None,
        "error_message": None,
        "termination_reason": "completed",
        "final_time_reached_s": rows[-1]["time_s"],
        "final_time_reached_days": rows[-1]["time_days"],
        "number_of_accepted_steps": len(dt_values),
        "number_of_rejected_steps": 0,
        "largest_dt_s": max(dt_values) if dt_values else None,
        "smallest_dt_s": min(dt_values) if dt_values else None,
        "average_dt_s": sum(dt_values) / len(dt_values) if dt_values else None,
        "solver_backend_type": "standard",
        "workflow_mode": case.config.solver.workflow.mode,
        "co2_runtime_workflow": case.config.solver.workflow.mode,
        "redox_enabled_runtime": case.config.redox.enabled,
        "redox_apply_during_runtime": case.config.redox.apply_during,
        "kinetic_precondition_requested": case.config.solver.workflow.precondition_kinetics,
        "kinetic_precondition_applied": any(
            record["stage"] == "kinetics_precondition" for record in solver_records
        ),
        "warnings": [],
        "run_started_at": run_started_at,
        "run_finished_at": datetime.now(timezone.utc).isoformat(),
    }
    return SimulationResult(
        rows=rows,
        kinetic_mapping=kinetic_mapping,
        solver_history=solver_records,
        diagnostics=diagnostics,
        initial_state=initial_state,
        final_state=state,
    )

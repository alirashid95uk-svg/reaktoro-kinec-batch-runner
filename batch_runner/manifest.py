"""Build the compact, traceable run manifest."""

from __future__ import annotations

import hashlib
import platform
from pathlib import Path
from typing import Any

import reaktoro as rkt

from batch_runner import OUTPUT_SCHEMA_VERSION
from batch_runner.config import ResolvedCase
from batch_runner.simulation import SimulationResult
from batch_runner.simulator.mapping import _kinetic_name, _thermo_name

def build_manifest(
    case: ResolvedCase,
    result: SimulationResult,
    output_files: list[str],
) -> dict[str, Any]:
    config = case.config
    input_snapshot = None
    if config.outputs.manifest.include_input_snapshot:
        input_snapshot = {
            "model_scope": "simple Reaktoro batch simulation runner",
            "physical_conditions": config.physical.model_dump(mode="json"),
            "co2_setup": config.co2.model_dump(mode="json"),
            "redox_setup": config.redox.model_dump(mode="json"),
            "brine_setup": config.brine.model_dump(mode="json"),
            "mineral_setup": [
                {
                    "name": mineral.name,
                    "thermo_name": _thermo_name(mineral),
                    "kinetic_name": _kinetic_name(mineral),
                    "role": mineral.role,
                    "initial_amount": (
                        mineral.initial_amount.model_dump(mode="json")
                        if mineral.initial_amount is not None
                        else None
                    ),
                    "surface_area": (
                        mineral.surface_area.model_dump(mode="json")
                        if mineral.surface_area is not None
                        else None
                    ),
                }
                for mineral in config.minerals
            ],
            "kinetics_setup": config.kinetics.model_dump(mode="json"),
        }

    return {
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "stale_output_policy": "fresh output_dir required; legacy results.csv is rejected",
        "run_identity": {
            "case_name": config.case.name,
            "output_schema_version": OUTPUT_SCHEMA_VERSION,
            "run_started_at": result.diagnostics["run_started_at"],
            "run_finished_at": result.diagnostics["run_finished_at"],
            "simulation_completed": result.diagnostics["simulation_completed"],
        },
        "traceability": {
            "source_config_path": str(case.config_path),
            "source_config_sha256": _sha256(case.config_path),
            "database_path": str(case.database_path or config.database.name),
            "database_sha256": _sha256(case.database_path),
            "kinetic_yaml_path": str(case.kinetics_path) if case.kinetics_path else None,
            "kinetic_yaml_sha256": _sha256(case.kinetics_path),
        },
        "input_snapshot": input_snapshot,
        "solver_configuration": {
            "backend_type": "standard",
            "workflow": config.solver.workflow.model_dump(mode="json"),
            "kinetic_precondition_applied": result.diagnostics["kinetic_precondition_applied"],
            "timestep": config.solver.timestep.model_dump(mode="json"),
            "redox_apply_during": config.redox.apply_during,
        },
        "time_semantics": {
            "canonical_unit": "second",
            "duration_s": case.duration_s,
            "timestep_mode": config.solver.timestep.mode,
            "configured_fixed_dt_s": (
                case.dt_s if config.solver.timestep.mode == "fixed" else None
            ),
            "configured_adaptive_dt_initial_s": (
                case.dt_initial_s if config.solver.timestep.mode != "fixed" else None
            ),
            "configured_adaptive_dt_min_s": (
                case.dt_min_s if config.solver.timestep.mode != "fixed" else None
            ),
            "configured_adaptive_dt_max_s": (
                case.dt_max_s if config.solver.timestep.mode != "fixed" else None
            ),
            "base_internal_steps": (
                case.base_internal_step_count if config.solver.timestep.mode == "fixed" else None
            ),
            "resolved_internal_steps": (
                case.internal_step_count if config.solver.timestep.mode == "fixed" else None
            ),
            "minimum_possible_accepted_steps": case.minimum_accepted_steps,
            "solver_target_rule": (
                "absolute fixed-grid targets split at requested output and checkpoint timestamps"
                if config.solver.timestep.mode == "fixed"
                else "accepted adaptive targets capped at the next output, checkpoint, or final timestamp"
            ),
            "output_state_rule": "accepted states only; no interpolation",
            "output_schedule": case.output_schedule_summary(),
            "checkpoint_schedule": case.checkpoint_schedule_summary(),
            "restart": config.solver.restart.model_dump(mode="json"),
        },
        "output_configuration": config.outputs.model_dump(mode="json"),
        "software_environment": {
            "python_version": platform.python_version(),
            "reaktoro_version": rkt.__version__,
            "platform": platform.platform(),
        },
        "output_files": output_files,
    }


def _sha256(path: Path | None) -> str | None:
    if path is None:
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

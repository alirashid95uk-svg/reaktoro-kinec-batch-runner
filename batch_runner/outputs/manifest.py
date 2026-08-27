"""Build the output manifest from resolved inputs and recorded run evidence.

The writer calls this after package files are known. The manifest records
input hashes, configured scientific setup, exact time semantics, software
versions, and relative file inventory. Its input snapshot mirrors validated
models; it is provenance evidence, not another configuration source.
"""

from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from typing import TYPE_CHECKING, Any

import reaktoro as rkt

from batch_runner import OUTPUT_SCHEMA_VERSION
from batch_runner.config import ResolvedCase

if TYPE_CHECKING:
    from batch_runner.simulator import SimulationResult


_DOE_LINEAGE_FIELDS = (
    "schema_version",
    "design_id",
    "design_spec_hash_v1",
    "sample_id",
    "design_point_fingerprint_v1",
    "run_id",
    "run_snapshot_sha256",
    "batch_runner_source_sha256",
)


def _load_doe_lineage() -> dict[str, Any] | None:
    """Return optional run-scoped DoE lineage supplied by the DoE launcher."""
    value = os.environ.get("BATCH_RUNNER_DOE_LINEAGE_FILE")
    if not value:
        return None
    path = Path(value).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"DoE lineage file does not exist: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("DoE lineage file must contain a JSON object")
    if payload.get("schema_version") != "1.0":
        raise ValueError("unsupported DoE lineage schema version")
    missing = [key for key in _DOE_LINEAGE_FIELDS if key not in payload]
    if missing:
        raise ValueError(f"DoE lineage is missing required fields: {missing}")
    return {key: payload[key] for key in _DOE_LINEAGE_FIELDS}


def build_manifest(
    case: ResolvedCase,
    result: SimulationResult,
    output_files: list[str],
) -> dict[str, Any]:
    """Return the JSON-serializable manifest for one output package.

    ``output_files`` must contain paths relative to the package root. Runtime
    completion and timestamps come from ``result.diagnostics``; scientific
    configuration comes from the resolved case without added defaults or unit
    conversion beyond the canonical-second schedule already resolved upstream.
    """
    config = case.config
    input_snapshot = {
        "model_scope": "simple Reaktoro batch simulation runner",
        "physical_conditions": config.physical.model_dump(mode="json"),
        "co2_setup": config.co2.model_dump(mode="json"),
        "redox_setup": config.redox.model_dump(mode="json"),
        "brine_setup": config.brine.model_dump(mode="json"),
        "mineral_setup": [
            {
                "name": mineral.name,
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

    manifest = {
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
            "source_config_sha256": result.source_config_sha256,
            "database_path": str(case.database_path or config.database.name),
            "database_sha256": result.database_sha256,
            "kinetic_model": config.kinetics.model,
            "kinetic_parameter_path": str(case.kinetics_path) if case.kinetics_path else None,
            "kinetic_parameter_sha256": result.kinetic_parameter_sha256,
        },
        "input_snapshot": input_snapshot,
        "solver_configuration": {
            "backend_type": "standard",
            "workflow": config.solver.workflow.model_dump(mode="json"),
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
            "restart": {"enabled": False, "from_checkpoint": None},
        },
        "output_configuration": _output_configuration(config),
        "software_environment": {
            "python_version": platform.python_version(),
            "reaktoro_version": rkt.__version__,
            "platform": platform.platform(),
        },
        "output_files": output_files,
    }
    doe_lineage = _load_doe_lineage()
    if doe_lineage is not None:
        manifest["doe_lineage"] = doe_lineage
    return manifest


def _output_configuration(config: Any) -> dict[str, Any]:
    """Project the source schema into the stable v4 manifest record."""

    post = config.postprocessing
    return {
        "monitor": config.monitor.model_dump(mode="json"),
        "manifest": {"enabled": True, "include_input_snapshot": True},
        "diagnostics": {"enabled": True},
        "timeseries": {
            "enabled": True,
            "include_species_amounts": True,
            "include_species_molalities": True,
            "include_mineral_amounts": True,
            "include_mineral_deltas": True,
            "include_saturation_indices": True,
            "include_solver_columns": True,
        },
        "summaries": {
            "mineral_summary": bool(post.requested_minerals),
            "aqueous_summary": bool(post.requested_species),
            "reaction_rates": post.reaction_rates,
            "reaction_rate_validation": post.reaction_rate_validation,
            "carbon_inventory": post.carbon_inventory.enabled,
            "element_budget": post.element_budget.enabled,
            "mineral_volume_change": post.mineral_volume_change.enabled,
            "regime_classification": post.regime_classification.enabled,
            "surface_area_audit": post.surface_area_audit.enabled,
            "workflow_comparison": post.workflow_comparison.enabled,
            "secondary_mineral_assemblage": post.secondary_mineral_assemblage.enabled,
            "surrogate_dataset": post.surrogate_dataset.enabled,
            "porosity_permeability": post.porosity_permeability.enabled,
        },
        "solver_history": {"enabled": True},
        "plots": config.plots.model_dump(mode="json"),
        "debug": config.debug.model_dump(mode="json"),
    }

#!/usr/bin/env python3
"""Audit one Reaktoro Objective 1 output package without scientific guesswork."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import tempfile
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "objective1_audit_v4"
SUMMARY_FILES = {
    "mineral_summary": "mineral_summary.csv",
    "aqueous_summary": "aqueous_summary.csv",
    "reaction_rates": "reaction_rates.csv",
    "reaction_rate_validation": "reaction_rate_validation.csv",
    "carbon_inventory": "carbon_inventory.csv",
    "element_budget": "element_budget.csv",
    "mineral_volume_change": "mineral_volume_change.csv",
    "regime_classification": "regime_classification.csv",
    "surface_area_audit": "surface_area_audit.csv",
    "workflow_comparison": "workflow_comparison.csv",
    "secondary_mineral_assemblage": "secondary_mineral_assemblage.csv",
    "surrogate_dataset": "surrogate_dataset.csv",
    "validation_ledger": "validation_ledger.csv",
    "porosity_permeability": "porosity_permeability.csv",
}
PLOT_FILES = {
    "pH": "plots/pH_vs_time.png",
    "mineral_change": "plots/mineral_change_vs_time.png",
    "saturation_index": "plots/saturation_index_vs_time.png",
    "solver_dt": "plots/solver_dt_vs_time.png",
    "solver_iterations": "plots/solver_iterations_vs_time.png",
}
DEBUG_FILES = {
    "mineral_connection": "debug/mineral_connection.csv",
    "resolved_config": "debug/resolved_config.yaml",
    "final_state": "debug/final_state.txt",
}
SOLVER_HISTORY_COLUMNS = [
    "step_index",
    "attempt_index",
    "time_start_s",
    "time_end_s",
    "dt_s",
    "stage",
    "accepted",
    "solver_succeeded",
    "iterations",
    "wall_time_s",
    "failure_reason",
    "next_dt_s",
]


def _read_json(path: Path, errors: list[str]) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"cannot read {path.name}: {exc}")
        return {}


def _read_csv(path: Path, errors: list[str]) -> list[dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8") as stream:
            return list(csv.DictReader(stream))
    except (OSError, csv.Error) as exc:
        errors.append(f"cannot read {path.name}: {exc}")
        return []


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _enabled(config: dict[str, Any], *keys: str) -> bool:
    value: Any = config
    for key in keys:
        if not isinstance(value, dict):
            return False
        value = value.get(key)
    return value is True or isinstance(value, dict) and value.get("enabled") is True


def _expect_file(actual: set[str], enabled: bool, name: str, errors: list[str]) -> None:
    if enabled and name not in actual:
        errors.append(f"enabled output is missing: {name}")
    if not enabled and name in actual:
        errors.append(f"disabled output is present: {name}")


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def audit(output_dir: Path) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    metrics: dict[str, Any] = {}
    if not output_dir.is_dir():
        return {
            "ok": False,
            "errors": [f"output directory not found: {output_dir}"],
            "warnings": [],
            "metrics": {},
        }

    actual = {
        path.relative_to(output_dir).as_posix()
        for path in output_dir.rglob("*")
        if path.is_file()
    }
    if "manifest.json" not in actual:
        return {
            "ok": False,
            "errors": ["manifest.json is required for a complete audit"],
            "warnings": [],
            "metrics": {},
        }

    manifest = _read_json(output_dir / "manifest.json", errors)
    declared = set(manifest.get("output_files", []))
    for name in sorted(declared - actual):
        errors.append(f"manifest-declared file is missing: {name}")
    for name in sorted(actual - declared):
        errors.append(f"undeclared file is present: {name}")

    identity = manifest.get("run_identity", {})
    if manifest.get("output_schema_version") != SCHEMA_VERSION:
        errors.append("manifest output_schema_version is missing or unsupported")
    if identity.get("output_schema_version") != SCHEMA_VERSION:
        errors.append("run_identity output_schema_version disagrees with the active schema")
    if identity.get("simulation_completed") is not True:
        errors.append("manifest does not record a completed simulation")

    output_config = manifest.get("output_configuration", {})
    _expect_file(actual, _enabled(output_config, "manifest"), "manifest.json", errors)
    _expect_file(actual, _enabled(output_config, "diagnostics"), "diagnostics.json", errors)
    _expect_file(actual, _enabled(output_config, "timeseries"), "timeseries.csv", errors)
    _expect_file(actual, _enabled(output_config, "solver_history"), "solver_history.csv", errors)
    for key, name in SUMMARY_FILES.items():
        _expect_file(actual, _enabled(output_config, "summaries", key), name, errors)
    plots_enabled = _enabled(output_config, "plots")
    for key, name in PLOT_FILES.items():
        _expect_file(actual, plots_enabled and _enabled(output_config, "plots", key), name, errors)
    debug_enabled = _enabled(output_config, "debug")
    for key, name in DEBUG_FILES.items():
        _expect_file(actual, debug_enabled and _enabled(output_config, "debug", key), name, errors)

    diagnostics: dict[str, Any] = {}
    if "diagnostics.json" in actual:
        diagnostics = _read_json(output_dir / "diagnostics.json", errors)
        if diagnostics.get("output_schema_version") != SCHEMA_VERSION:
            errors.append("diagnostics output_schema_version disagrees with the active schema")
        if diagnostics.get("simulation_completed") is not True:
            errors.append("diagnostics does not record a completed simulation")
        for key in ("case_name", "run_started_at", "run_finished_at", "simulation_completed"):
            if identity.get(key) != diagnostics.get(key):
                errors.append(f"manifest and diagnostics disagree on {key}")
        completeness = diagnostics.get("output_completeness", {})
        if completeness.get("status") != "complete":
            errors.append("diagnostics does not record complete output writing")
        if set(completeness.get("files_written", [])) != actual:
            errors.append("diagnostics output_completeness disagrees with files present")

    traceability = manifest.get("traceability", {})
    for key in ("kinetic_model", "kinetic_parameter_path", "kinetic_parameter_sha256"):
        if diagnostics and diagnostics.get(key) != traceability.get(key):
            errors.append(f"manifest and diagnostics disagree on {key}")
    for prefix in ("source_config", "database", "kinetic_parameter"):
        expected = traceability.get(f"{prefix}_sha256")
        value = traceability.get(f"{prefix}_path")
        if expected is None:
            continue
        path = Path(value) if value else Path()
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.is_file():
            errors.append(f"traceability path is unavailable: {value}")
        elif _sha256(path) != expected:
            errors.append(f"traceability hash mismatch: {prefix}")

    if "timeseries.csv" in actual:
        timeseries = _read_csv(output_dir / "timeseries.csv", errors)
        times = [_float(row.get("time_s")) for row in timeseries]
        if any(value is None for value in times):
            errors.append("timeseries contains invalid time_s values")
        elif times != sorted(set(times)):
            errors.append("timeseries time_s is not strictly increasing and unique")
        else:
            metrics["timeseries_rows"] = len(timeseries)
            metrics["last_output_time_s"] = times[-1] if times else None
            if diagnostics.get("result_rows") != len(timeseries):
                errors.append("diagnostics result_rows does not match timeseries row count")
            schedule = manifest.get("time_semantics", {}).get("output_schedule")
            if schedule and schedule.get("resolved_times_s") is not None:
                if times != schedule["resolved_times_s"]:
                    errors.append("timeseries timestamps disagree with the resolved output schedule")
            elif schedule and schedule.get("include_final") and times:
                expected_final = _float(diagnostics.get("final_time_reached_s"))
                if expected_final is None or not math.isclose(
                    times[-1], expected_final, rel_tol=1e-12, abs_tol=1e-12
                ):
                    errors.append("configured final output does not match the accepted final time")
            elif not schedule and times:
                expected_final = _float(diagnostics.get("final_time_reached_s"))
                if expected_final is None or not math.isclose(
                    times[-1], expected_final, rel_tol=1e-12, abs_tol=1e-12
                ):
                    errors.append("diagnostics final time does not match timeseries")
        if any(
            str(row.get("solver_succeeded", "")).lower() == "false"
            for row in timeseries
        ):
            errors.append("timeseries contains a failed solver result")

    if "solver_history.csv" in actual:
        with (output_dir / "solver_history.csv").open(newline="", encoding="utf-8") as stream:
            columns = csv.DictReader(stream).fieldnames or []
        if columns != SOLVER_HISTORY_COLUMNS:
            errors.append("solver_history columns or ordering disagree with schema v4")
        solver_rows = _read_csv(output_dir / "solver_history.csv", errors)
        accepted_positive_dt = 0
        rejected = 0
        attempted_positive_dt = 0
        for row in solver_rows:
            accepted = str(row.get("accepted", "")).lower() == "true"
            succeeded = str(row.get("solver_succeeded", "")).lower()
            dt_s = _float(row.get("dt_s"))
            if accepted and succeeded == "false":
                errors.append("solver_history contains an accepted failed step")
            if accepted and dt_s is not None and dt_s > 0:
                accepted_positive_dt += 1
            if not accepted:
                rejected += 1
                start_s = _float(row.get("time_start_s"))
                end_s = _float(row.get("time_end_s"))
                if start_s is not None and end_s is not None and start_s != end_s:
                    errors.append("rejected solver attempt advanced accepted time")
            if dt_s is not None and dt_s > 0:
                attempted_positive_dt += 1
        metrics["solver_history_rows"] = len(solver_rows)
        if diagnostics.get("number_of_accepted_steps") != accepted_positive_dt:
            errors.append("accepted-step count disagrees with solver_history")
        if diagnostics.get("number_of_rejected_steps") != rejected:
            errors.append("rejected-step count disagrees with solver_history")
        reported_attempts = diagnostics.get("number_of_internal_attempts")
        if reported_attempts is not None and reported_attempts != attempted_positive_dt:
            errors.append("internal-attempt count disagrees with solver_history")

    if "reaction_rate_validation.csv" in actual:
        rows = _read_csv(output_dir / "reaction_rate_validation.csv", errors)
        failed = sum(row.get("sign_check") != "passed" for row in rows)
        metrics["reaction_rate_sign_failures"] = failed
        if failed:
            errors.append(f"reaction-rate sign validation failed for {failed} rows")

    for filename, column, metric in (
        (
            "carbon_inventory.csv",
            "carbon_balance_error_mol",
            "max_abs_carbon_balance_error_mol",
        ),
        ("element_budget.csv", "delta_mol", "max_abs_element_balance_error_mol"),
    ):
        if filename in actual:
            values = [
                abs(value)
                for row in _read_csv(output_dir / filename, errors)
                if (value := _float(row.get(column))) is not None
            ]
            metrics[metric] = max(values, default=None)

    if "validation_ledger.csv" in actual:
        rows = _read_csv(output_dir / "validation_ledger.csv", errors)
        outside = sum(
            row.get("evaluation_status") == "outside_uncertainty" for row in rows
        )
        metrics["validation_targets_outside_uncertainty"] = outside
        if outside:
            warnings.append(f"{outside} validation targets are outside uncertainty")

    if "porosity_permeability.csv" in actual:
        rows = _read_csv(output_dir / "porosity_permeability.csv", errors)
        metrics["porosity_statuses"] = sorted(
            {row.get("porosity_status", "") for row in rows}
        )
        metrics["permeability_statuses"] = sorted(
            {row.get("permeability_status", "") for row in rows}
        )
        metrics["capillary_entry_pressure_statuses"] = sorted(
            {row.get("capillary_entry_pressure_status", "") for row in rows}
        )

    return {"ok": not errors, "errors": errors, "warnings": warnings, "metrics": metrics}


def _self_test() -> bool:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        files = [
            "diagnostics.json",
            "manifest.json",
            "solver_history.csv",
            "timeseries.csv",
        ]
        diagnostics = {
            "case_name": "self_test",
            "output_schema_version": SCHEMA_VERSION,
            "run_started_at": "2026-01-01T00:00:00+00:00",
            "run_finished_at": "2026-01-01T00:00:01+00:00",
            "simulation_completed": True,
            "result_rows": 1,
            "final_time_reached_s": 1.0,
            "number_of_accepted_steps": 1,
            "number_of_rejected_steps": 0,
            "output_completeness": {"status": "complete", "files_written": files},
        }
        manifest = {
            "output_schema_version": SCHEMA_VERSION,
            "run_identity": {
                key: diagnostics[key]
                for key in (
                    "case_name",
                    "run_started_at",
                    "run_finished_at",
                    "simulation_completed",
                )
            }
            | {"output_schema_version": SCHEMA_VERSION},
            "traceability": {},
            "time_semantics": {
                "output_schedule": {
                    "include_final": False,
                    "resolved_times_s": [0.0],
                }
            },
            "output_configuration": {
                "manifest": {"enabled": True},
                "diagnostics": {"enabled": True},
                "timeseries": {"enabled": True},
                "summaries": {},
                "solver_history": {"enabled": True},
                "plots": {"enabled": False},
                "debug": {"enabled": False},
            },
            "output_files": files,
        }
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (root / "diagnostics.json").write_text(
            json.dumps(diagnostics), encoding="utf-8"
        )
        (root / "timeseries.csv").write_text(
            "time_s,time_days,stage,solver_succeeded\n0.0,0.0,initial_state,\n",
            encoding="utf-8",
        )
        with (root / "solver_history.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=SOLVER_HISTORY_COLUMNS)
            writer.writeheader()
            writer.writerow(
                {
                    "step_index": 0,
                    "attempt_index": 1,
                    "time_start_s": 0.0,
                    "time_end_s": 1.0,
                    "dt_s": 1.0,
                    "stage": "kinetic_step",
                    "accepted": True,
                    "solver_succeeded": True,
                }
            )
        return audit(root)["ok"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", nargs="?", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        ok = _self_test()
        print(json.dumps({"self_test": "passed" if ok else "failed"}, indent=2))
        return 0 if ok else 1
    if args.output_dir is None:
        parser.error("output_dir is required unless --self-test is used")
    report = audit(args.output_dir.resolve())
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

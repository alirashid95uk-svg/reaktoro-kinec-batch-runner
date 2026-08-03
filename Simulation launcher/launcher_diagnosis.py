"""Plain-language diagnoses for the Windows simulation launcher."""

from __future__ import annotations

import csv
import json
import tempfile
from pathlib import Path
from typing import Any

from batch_runner.config import load_case


PREFLIGHT_PREFIX = "PREFLIGHT_RESULT:"
PRE_SOLVER_STAGES = {
    "configuration_validation",
    "database_loading",
    "kinetics_loading",
    "mapping",
    "system_construction",
    "state_construction",
}


def format_preflight_diagnosis(case_path: Path, report: dict[str, Any]) -> str:
    if report.get("ready"):
        return "\n".join(
            [
                case_path.name,
                "OUTCOME: READY — full preflight passed",
                "SOLVER STARTED: No (preflight only)",
                "CHECKS: configuration, database, kinetics, mineral mapping, system, initial state",
                "SAFE NEXT ACTION: Run the case when ready.",
            ]
        )

    stage = report.get("failed_stage") or "preflight_process"
    mapping_failures = _mapping_failures(report.get("kinetic_mapping", []))
    lines = [
        case_path.name,
        f"OUTCOME: {_blocked_outcome(stage)}",
        f"PROBLEM SOURCE: {_problem_source(stage)}",
        "SOLVER STARTED: No",
        f"FAILED STAGE: {stage}",
        f"ISSUE: {report.get('error_message') or 'Preflight did not return a diagnostic message.'}",
    ]
    _append_mapping_failures(lines, mapping_failures)
    _append_safe_actions(lines, stage, mapping_failures)
    return "\n".join(lines)


def write_run_diagnosis(run_dir: Path, return_code: int) -> tuple[Path, str]:
    text = format_run_diagnosis(run_dir, return_code)
    path = run_dir / "diagnosis.txt"
    path.write_text(text + "\n", encoding="utf-8")
    return path, text


def format_run_diagnosis(run_dir: Path, return_code: int) -> str:
    diagnostics_path = run_dir / "results" / "diagnostics.json"
    diagnostics = _read_json(diagnostics_path)
    if diagnostics is None:
        return "\n".join(
            [
                "OUTCOME: PROCESS CRASH — no readable diagnostics were produced",
                "PROBLEM SOURCE: simulation code, native library, or runtime environment",
                "SOLVER STARTED: Unknown",
                f"CHILD PROCESS EXIT CODE: {return_code}",
                "ISSUE: The child process ended before it could finalize diagnostics.",
                "TECHNICAL DETAILS: See launch_log.txt for the faulthandler stack and process output.",
                "SAFE NEXT ACTIONS:",
                "- Provide diagnosis.txt and launch_log.txt for code investigation.",
                "- Do not change scientific inputs unless the log identifies an input problem.",
            ]
        )

    simulation_completed = diagnostics.get("simulation_completed") is True
    completeness = diagnostics.get("output_completeness", {}).get("status")
    failed_stage = diagnostics.get("failed_stage")
    output_failure = diagnostics.get("output_failure")
    if simulation_completed and completeness == "complete" and return_code == 0:
        outcome = "COMPLETED — chemistry and output package completed"
        source = "none detected by software checks"
        solver_started = "Yes"
        action_stage = None
    elif simulation_completed and completeness == "complete":
        outcome = "PROCESS EXIT FAILURE — chemistry and output package completed"
        source = "process finalization, native library, or runtime environment"
        solver_started = "Yes"
        action_stage = "process_crash"
    elif simulation_completed:
        outcome = "CHEMISTRY COMPLETED — output package incomplete"
        source = "output writing or postprocessing code"
        solver_started = "Yes"
        action_stage = "output_writing"
    elif failed_stage in PRE_SOLVER_STAGES:
        outcome = _blocked_outcome(failed_stage)
        source = _problem_source(failed_stage)
        solver_started = "No"
        action_stage = failed_stage
    elif failed_stage == "output_writing":
        outcome = "FAILED — output setup failed before solver completion"
        source = "output writing code or filesystem"
        solver_started = "No"
        action_stage = failed_stage
    else:
        outcome = "STOPPED — solver or numerical execution failed"
        source = "solver, numerical behaviour, or runtime code"
        solver_started = "Yes"
        action_stage = failed_stage

    requested_time_s, duration_error = _requested_duration(run_dir / "run_case.yaml")
    if simulation_completed:
        issue = (output_failure or {}).get("error_message")
        if issue is None and completeness != "complete":
            issue = "Output package did not complete; inspect launch_log.txt."
        if issue is None and return_code != 0:
            issue = "The process exited abnormally after writing the output package."
        issue = issue or "No software failure recorded."
    else:
        issue = diagnostics.get("error_message") or (output_failure or {}).get("error_message")
        issue = issue or "No diagnostic message recorded."
    reported_stage = (
        (output_failure or {}).get("failed_stage")
        if simulation_completed and completeness != "complete"
        else failed_stage
    )
    mapping_failures = _read_mapping_failures(
        run_dir / "results" / "debug" / "mineral_connection.csv"
    )
    lines = [
        f"OUTCOME: {outcome}",
        f"PROBLEM SOURCE: {source}",
        f"SOLVER STARTED: {solver_started}",
        f"FAILED STAGE: {reported_stage or 'None'}",
        f"REQUESTED TIME: {_seconds(requested_time_s)}",
        f"LAST ACCEPTED TIME: {_seconds(diagnostics.get('final_time_reached_s'))}",
        f"ACCEPTED / REJECTED STEPS: {diagnostics.get('number_of_accepted_steps', 0)} / {diagnostics.get('number_of_rejected_steps', 0)}",
        f"OUTPUT COMPLETENESS: {completeness or 'unknown'}",
        f"CHILD PROCESS EXIT CODE: {return_code}",
        f"ISSUE: {issue}",
    ]
    if duration_error:
        lines.append(f"REQUESTED-TIME READ ERROR: {duration_error}")
    _append_mapping_failures(lines, mapping_failures)
    lines.append("TECHNICAL DETAILS: See launch_log.txt; raw runtime evidence remains under results/.")
    _append_safe_actions(
        lines,
        action_stage,
        mapping_failures,
        completed=simulation_completed and completeness == "complete" and return_code == 0,
    )
    lines.append(
        "SCIENTIFIC BOUNDARY: Software completion does not establish calibration, timestep convergence, conservation, transport behaviour, or fracture sealing."
    )
    return "\n".join(lines)


def _blocked_outcome(stage: str) -> str:
    if stage in {"configuration_validation", "database_loading", "kinetics_loading", "mapping"}:
        return "BLOCKED — input or kinetic compatibility failed preflight"
    if stage in {"system_construction", "state_construction"}:
        return "BLOCKED — chemical system or initial state failed preflight"
    return "BLOCKED — preflight process failed"


def _problem_source(stage: str) -> str:
    if stage in {"configuration_validation", "database_loading", "kinetics_loading", "mapping"}:
        return "case input, database selection, or kinetic compatibility"
    if stage in {"system_construction", "state_construction"}:
        return "case input or chemical construction"
    return "preflight code or runtime environment"


def _mapping_failures(mapping: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [
        (str(row.get("mineral_name")), str(row.get("reason")))
        for row in mapping
        if row.get("status") == "failed"
    ]


def _read_mapping_failures(path: Path) -> list[tuple[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as stream:
        return _mapping_failures(list(csv.DictReader(stream)))


def _append_mapping_failures(lines: list[str], failures: list[tuple[str, str]]) -> None:
    if not failures:
        return
    lines.append("MAPPING FAILURES:")
    lines.extend(f"- {name}: {reason}" for name, reason in failures)


def _append_safe_actions(
    lines: list[str],
    stage: str | None,
    mapping_failures: list[tuple[str, str]],
    *,
    completed: bool = False,
) -> None:
    lines.append("SAFE NEXT ACTIONS:")
    if completed:
        lines.append("- Review the generated results and scientific validation checks.")
    elif mapping_failures:
        lines.extend(
            [
                "- Supply source-supported records for the selected kinetic model.",
                "- Select another kinetic model only if that is scientifically intended.",
                "- Change mineral roles only with scientific justification.",
                "- The program did not skip or alter any mineral automatically.",
            ]
        )
    elif stage == "output_writing":
        lines.extend(
            [
                "- Inspect launch_log.txt for the exact output exception.",
                "- Correct the output/code problem before rerunning; do not change chemistry inputs.",
            ]
        )
    elif stage == "process_crash":
        lines.extend(
            [
                "- Inspect launch_log.txt for the faulthandler stack and exit code.",
                "- Preserve the completed result package while investigating process finalization.",
                "- Do not change scientific inputs without evidence that they caused the exit failure.",
            ]
        )
    elif stage in PRE_SOLVER_STAGES:
        lines.extend(
            [
                "- Correct the exact input or construction issue reported above.",
                "- Run full preflight again before starting the solver.",
                "- No scientific setting will be changed automatically.",
            ]
        )
    else:
        lines.extend(
            [
                "- Inspect solver_history.csv and launch_log.txt at the last accepted time.",
                "- Do not tune timestep or chemistry settings without scientific justification.",
                "- Provide diagnosis.txt and launch_log.txt for code investigation.",
            ]
        )


def _requested_duration(config_path: Path) -> tuple[float | None, str | None]:
    try:
        with tempfile.TemporaryDirectory(prefix="reaktoro-diagnosis-") as temp_dir:
            case = load_case(
                config_path,
                output_dir_override=Path(temp_dir) / "results",
            )
        return case.duration_s, None
    except Exception as error:  # Diagnosis must not mask the original run failure.
        return None, f"{type(error).__name__}: {error}"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        with path.open(encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _seconds(value: Any) -> str:
    try:
        return f"{float(value):g} s"
    except (TypeError, ValueError):
        return "unavailable"

"""Command-line entry point for one YAML-defined batch case."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import traceback
from contextlib import nullcontext, redirect_stdout
from functools import partial
from pathlib import Path
from time import monotonic
from uuid import uuid4

import reaktoro as rkt
import yaml

from batch_runner.config import load_case
from batch_runner.config._base import PROJECT_ROOT
from batch_runner.outputs import write_kinetic_mapping, write_outputs
from batch_runner.protocol import ProtocolEmitter, cancellation_requested
from batch_runner.simulator import (
    preflight_case,
    run_simulation,
    uses_python_rate_callback,
)


PREFLIGHT_PREFIX = "PREFLIGHT_RESULT:"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one Reaktoro batch case from YAML.")
    parser.add_argument("case_config", help="Path to a runnable YAML case config.")
    parser.add_argument("--preflight", action="store_true", help="Validate construction without starting a solver.")
    parser.add_argument("--overwrite", action="store_true", help="Delete the configured output directory before a full run if it already exists.")
    parser.add_argument("--events-jsonl", action="store_true", help="Write versioned worker events to stdout.")
    parser.add_argument("--operation-id", help="Controller operation identifier.")
    parser.add_argument("--run-id", help="Controller run identifier.")
    parser.add_argument("--case-id", help="Stable source-case identifier for controller events.")
    parser.add_argument("--cancel-file", type=Path, help="Cooperative-cancellation sentinel path.")
    args = parser.parse_args()

    run_id = args.run_id or args.operation_id or str(uuid4())
    emitter = ProtocolEmitter(
        enabled=args.events_jsonl,
        run_id=run_id,
        case_id=args.case_id or Path(args.case_config).stem,
        stream=sys.stdout,
    )
    stdout_context = redirect_stdout(sys.stderr) if args.events_jsonl else nullcontext()
    with stdout_context:
        try:
            return_code, immediate_exit = _run(args, emitter)
        except Exception as error:
            emitter.emit(
                "worker_failure_reported",
                {
                    "failed_stage": "worker_execution",
                    "exception_type": type(error).__name__,
                    "error_message": str(error),
                },
            )
            if args.events_jsonl:
                traceback.print_exc(file=sys.stderr)
                raise SystemExit(1)
            raise

    if immediate_exit:
        os._exit(return_code)
    if return_code:
        raise SystemExit(return_code)


def _run(args: argparse.Namespace, emitter: ProtocolEmitter) -> tuple[int, bool]:
    operation_id = args.operation_id or emitter.run_id
    cancel = partial(cancellation_requested, args.cancel_file) if args.cancel_file else None
    emitter.emit(
        "worker_ready",
        {
            "operation_id": operation_id,
            "preflight": args.preflight,
            "process_id": os.getpid(),
        },
    )

    if cancel is not None and cancel():
        payload = {
            "termination_reason": "cancelled_cleanly",
            "cancellation_boundary": "before_configuration_validation",
        }
        emitter.emit("simulation_finished", payload)
        if args.preflight:
            report = {
                "ready": False,
                "case_name": None,
                "failed_stage": "cooperative_cancellation",
                "exception_type": None,
                "error_message": "cooperative cancellation requested",
                "kinetic_mapping": [],
                "database_sha256": None,
                "kinetic_parameter_sha256": None,
                "technical_traceback": None,
            }
            print(f"{PREFLIGHT_PREFIX}{json.dumps(report, separators=(',', ':'))}", flush=True)
            return 2, False
        return 1, False

    if args.preflight:
        return _run_preflight(args, emitter, cancel)
    return _run_simulation(args, emitter, cancel)


def _run_preflight(
    args: argparse.Namespace,
    emitter: ProtocolEmitter,
    cancel,
) -> tuple[int, bool]:
    case = None
    emitter.emit("stage_started", {"stage": "configuration_validation"})
    try:
        with tempfile.TemporaryDirectory(prefix="reaktoro-preflight-") as temp_dir:
            case = load_case(
                args.case_config,
                output_dir_override=Path(temp_dir) / "results",
            )
            emitter.emit("stage_completed", {"stage": "configuration_validation"})
            _emit_environment(emitter)
            if cancel is not None and cancel():
                report = {
                    "ready": False,
                    "case_name": case.config.case.name,
                    "failed_stage": "cooperative_cancellation",
                    "exception_type": None,
                    "error_message": "cooperative cancellation requested",
                    "kinetic_mapping": [],
                    "database_sha256": None,
                    "kinetic_parameter_sha256": None,
                    "technical_traceback": None,
                }
            else:
                emitter.emit("stage_started", {"stage": "preflight"})
                report = preflight_case(case, event_ready=emitter.emit)
    except Exception as error:  # CLI boundary reports configuration failures as data.
        report = {
            "ready": False,
            "case_name": None,
            "failed_stage": "configuration_validation",
            "exception_type": type(error).__name__,
            "error_message": str(error),
            "kinetic_mapping": [],
            "database_sha256": None,
            "kinetic_parameter_sha256": None,
            "technical_traceback": traceback.format_exc(),
        }
        emitter.emit(
            "validation_issue",
            {
                "stage": "configuration_validation",
                "exception_type": type(error).__name__,
                "error_message": str(error),
            },
        )

    print(f"{PREFLIGHT_PREFIX}{json.dumps(report, separators=(',', ':'))}", flush=True)
    return_code = 0 if report["ready"] else 2
    if report["ready"]:
        emitter.emit(
            "stage_completed",
            {
                "stage": "preflight",
                "ready": True,
                "database_sha256": report["database_sha256"],
                "kinetic_parameter_sha256": report["kinetic_parameter_sha256"],
            },
        )
    else:
        emitter.emit(
            "worker_failure_reported",
            {
                "failed_stage": report["failed_stage"],
                "exception_type": report["exception_type"],
                "error_message": report["error_message"],
            },
        )
        if emitter.enabled and report["technical_traceback"]:
            print(report["technical_traceback"], file=sys.stderr, flush=True)
    return return_code, case is not None and uses_python_rate_callback(case)


def _run_simulation(
    args: argparse.Namespace,
    emitter: ProtocolEmitter,
    cancel,
) -> tuple[int, bool]:
    emitter.emit("stage_started", {"stage": "configuration_validation"})
    if args.overwrite:
        _remove_existing_output_dir(args.case_config)
    case = load_case(args.case_config)
    emitter.emit("stage_completed", {"stage": "configuration_validation"})
    _emit_environment(emitter)
    emitter.emit("simulation_started", {"case_name": case.config.case.name})

    last_progress_at = None

    def progress_ready(payload: dict) -> None:
        nonlocal last_progress_at
        now = monotonic()
        if last_progress_at is None or now - last_progress_at >= 0.5:
            emitter.emit("progress_summary", payload)
            last_progress_at = now

    result = run_simulation(
        case,
        mapping_ready=lambda mapping: write_kinetic_mapping(case, mapping),
        event_ready=emitter.emit,
        progress_ready=progress_ready if emitter.enabled else None,
        cancel_requested=cancel,
    )
    emitter.emit("stage_started", {"stage": "output_writing"})
    output_dir = write_outputs(case, result, cancel_requested=cancel)
    emitter.emit(
        "output_written",
        {
            "output_dir": str(output_dir),
            "output_completeness": result.diagnostics["output_completeness"],
        },
    )
    emitter.emit("stage_completed", {"stage": "output_writing"})

    simulation_completed = result.diagnostics["simulation_completed"]
    package_complete = result.diagnostics["output_completeness"]["status"] == "complete"
    completed = simulation_completed and package_complete
    if result.exception_traceback:
        print("Technical traceback:", file=sys.stderr, flush=True)
        print(result.exception_traceback, file=sys.stderr, flush=True)
    status = (
        "Completed"
        if completed
        else "Cancelled"
        if result.diagnostics.get("termination_reason") == "cancelled_cleanly"
        else "Chemistry completed; output incomplete"
        if simulation_completed
        else "Failed"
    )
    print(f"{status} case '{case.config.case.name}'. Outputs: {output_dir}", flush=True)

    finish_payload = {
        "simulation_completed": simulation_completed,
        "package_complete": package_complete,
        "termination_reason": result.diagnostics["termination_reason"],
        "final_time_reached_s": result.diagnostics["final_time_reached_s"],
        "output_dir": str(output_dir),
    }
    emitter.emit("simulation_finished", finish_payload)
    if not completed and not result.diagnostics.get("cancellation_requested"):
        emitter.emit(
            "worker_failure_reported",
            {
                "failed_stage": result.diagnostics.get("failed_stage"),
                "exception_type": result.diagnostics.get("exception_type"),
                "error_message": result.diagnostics.get("error_message"),
                "output_failure": result.diagnostics.get("output_failure"),
            },
        )

    return (0 if completed else 1), uses_python_rate_callback(case)


def _remove_existing_output_dir(case_config: str | Path) -> None:
    """Remove the configured output directory for an explicit --overwrite run."""
    config_path = Path(case_config).resolve()
    if not config_path.is_file():
        raise FileNotFoundError(f"case config does not exist: {config_path}")

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"case config must contain a YAML mapping: {config_path}")
    paths = raw.get("paths")
    if not isinstance(paths, dict) or not isinstance(paths.get("output_dir"), str):
        raise ValueError("case config must define paths.output_dir before --overwrite can be used")

    output_dir = Path(paths["output_dir"])
    if not output_dir.is_absolute():
        output_dir = PROJECT_ROOT / output_dir
    output_dir = output_dir.resolve()

    protected_paths = {PROJECT_ROOT.resolve(), *PROJECT_ROOT.resolve().parents}
    if output_dir in protected_paths:
        raise ValueError(f"refusing to overwrite protected path: {output_dir}")
    if not output_dir.exists():
        return
    if not output_dir.is_dir():
        raise NotADirectoryError(f"output path exists and is not a directory: {output_dir}")
    shutil.rmtree(output_dir)


def _emit_environment(emitter: ProtocolEmitter) -> None:
    emitter.emit(
        "environment_verified",
        {
            "python_version": sys.version.split()[0],
            "reaktoro_version": rkt.__version__,
        },
    )


if __name__ == "__main__":
    main()

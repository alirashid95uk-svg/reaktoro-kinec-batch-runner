"""Process boundary for running one YAML-defined Reaktoro batch case.

The module coordinates CLI parsing, worker events, preflight or simulation,
output writing, and optional downstream validation. Scientific construction
and solver behaviour remain under :mod:`batch_runner`; the ``config`` command
is a read-only projection of the active Pydantic schema.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from contextlib import nullcontext, redirect_stdout
from functools import partial
from pathlib import Path
from time import monotonic
from typing import Sequence
from uuid import uuid4

import reaktoro as rkt
import yaml

from batch_runner.cli import build_run_parser, run_config_help
from batch_runner.config import load_case
from batch_runner.config._base import PROJECT_ROOT
from batch_runner.integrity_monitor import IntegritySimulationMonitor
from batch_runner.outputs import write_kinetic_mapping, write_outputs
from batch_runner.protocol import ProtocolEmitter, cancellation_requested
from batch_runner.run_directories import prepare_run_config_for_execution
from batch_runner.simulator import (
    preflight_case,
    run_simulation,
    uses_python_rate_callback,
)
from batch_runner.simulator.integrity import NumericalIntegrityObserver


PREFLIGHT_PREFIX = "PREFLIGHT_RESULT:"


def main(argv: Sequence[str] | None = None) -> None:
    """Dispatch read-only config help or execute one case as a worker process.

    Args:
        argv: Arguments excluding the executable name. ``None`` uses
            :data:`sys.argv`, preserving the command-line entry-point contract.

    Raises:
        SystemExit: If parsing, configuration, simulation, or output writing
            reports a non-zero process status.

    Side Effects:
        A simulation may create a fresh run directory, emit worker events,
        invoke Reaktoro, write the configured output package, and launch the
        optional downstream validation script. Configuration help is read-only.
    """

    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "config":
        return_code = run_config_help(arguments[1:])
        if return_code:
            raise SystemExit(return_code)
        return

    args = build_run_parser().parse_args(arguments)

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
            print(
                "ERROR   FAILED | stage=configuration_validation | last accepted=0 s | "
                f"{error}",
                file=sys.stderr,
                flush=True,
            )
            print(
                "INFO    No output package was initialized; rerun with --events-jsonl "
                "for a technical traceback.",
                file=sys.stderr,
                flush=True,
            )
            raise SystemExit(1)

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
    case_config = Path(args.case_config).resolve()
    if args.overwrite:
        _remove_existing_output_dir(case_config)
    else:
        case_config = prepare_run_config_for_execution(case_config)
    case = load_case(case_config)
    emitter.emit("stage_completed", {"stage": "configuration_validation"})
    _emit_environment(emitter)
    emitter.emit("simulation_started", {"case_name": case.config.case.name})

    monitor = IntegritySimulationMonitor(
        case,
        display_enabled=case.config.outputs.monitor.enabled and not emitter.enabled,
        stream=sys.stdout,
    )
    monitor.start(
        python_version=sys.version.split()[0],
        reaktoro_version=rkt.__version__,
    )
    integrity = NumericalIntegrityObserver(case)

    last_progress_at = None

    def event_ready(event_type: str, payload: dict) -> None:
        emitter.emit(event_type, payload)
        monitor.handle_event(event_type, payload)

    def mapping_ready(mapping: list[dict]) -> None:
        write_kinetic_mapping(case, mapping)
        monitor.activate_log()

    def progress_ready(payload: dict) -> None:
        nonlocal last_progress_at
        monitor.handle_progress(payload)
        now = monotonic()
        if emitter.enabled and (last_progress_at is None or now - last_progress_at >= 0.5):
            emitter.emit("progress_summary", payload)
            last_progress_at = now

    def accepted_state_ready(state, record: dict) -> None:
        snapshot = integrity.observe(
            state,
            time_s=float(record["time_end_s"]),
            initialize=record["stage"] == "initial_state",
        )
        monitor.handle_numerical_integrity(snapshot)

    result = run_simulation(
        case,
        mapping_ready=mapping_ready,
        event_ready=event_ready,
        progress_ready=progress_ready,
        cancel_requested=cancel,
        accepted_row_ready=monitor.handle_accepted_row,
        accepted_state_ready=accepted_state_ready,
    )
    result.diagnostics["numerical_integrity"] = integrity.summary()
    if integrity.unavailable_reason:
        result.diagnostics["warnings"].append(
            "numerical-integrity diagnostics unavailable: "
            f"{integrity.unavailable_reason}"
        )
    monitor.activate_log()
    if monitor.log_error:
        result.diagnostics["warnings"].append(
            f"simulation.log unavailable: {monitor.log_error}"
        )
    event_ready("stage_started", {"stage": "output_writing"})
    output_dir = write_outputs(case, result, cancel_requested=cancel)
    event_ready(
        "output_written",
        {
            "output_dir": str(output_dir),
            "output_completeness": result.diagnostics["output_completeness"],
        },
    )
    event_ready("stage_completed", {"stage": "output_writing"})

    simulation_completed = result.diagnostics["simulation_completed"]
    package_complete = result.diagnostics["output_completeness"]["status"] == "complete"
    completed = simulation_completed and package_complete
    if result.exception_traceback and emitter.enabled:
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
    if not monitor.display_enabled:
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
    monitor.finish(result, output_dir)
    _run_validation_hook(case, output_dir, emitter, completed=completed)

    return (0 if completed else 1), uses_python_rate_callback(case)


def _run_validation_hook(
    case,
    output_dir: Path,
    emitter: ProtocolEmitter,
    *,
    completed: bool,
) -> str:
    if not case.config.validation.enabled:
        return "disabled"

    script = case.validation_script_path
    assert script is not None
    validation_dir = output_dir.parent / "validation"
    common = {
        "script": str(script),
        "results_dir": str(output_dir),
        "validation_dir": str(validation_dir),
    }
    if not completed:
        emitter.emit(
            "stage_completed",
            {
                **common,
                "stage": "post_simulation_validation",
                "status": "skipped",
                "reason": "simulation package incomplete",
            },
        )
        print("Validation skipped: simulation package incomplete.", flush=True)
        return "skipped"

    emitter.emit("stage_started", {**common, "stage": "post_simulation_validation"})
    try:
        process = subprocess.run(
            [sys.executable, str(script), "--results-dir", str(output_dir)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        emitter.emit(
            "stage_completed",
            {
                **common,
                "stage": "post_simulation_validation",
                "status": "failed",
                "exit_code": None,
                "error_message": str(error),
            },
        )
        print(
            f"Validation FAILED: {error}. Simulation results remain valid.",
            file=sys.stderr,
            flush=True,
        )
        return "failed"

    if process.stdout:
        print(process.stdout, end="" if process.stdout.endswith("\n") else "\n", flush=True)
    if process.returncode:
        if process.stderr:
            print(
                process.stderr,
                end="" if process.stderr.endswith("\n") else "\n",
                file=sys.stderr,
                flush=True,
            )
        error_message = (
            (process.stderr.strip() or process.stdout.strip()).splitlines()[-1]
            if process.stderr.strip() or process.stdout.strip()
            else f"validation script exited with status {process.returncode}"
        )
        emitter.emit(
            "stage_completed",
            {
                **common,
                "stage": "post_simulation_validation",
                "status": "failed",
                "exit_code": process.returncode,
                "error_message": error_message,
            },
        )
        print(
            "Validation FAILED. Simulation results remain valid.",
            file=sys.stderr,
            flush=True,
        )
        return "failed"

    emitter.emit(
        "stage_completed",
        {
            **common,
            "stage": "post_simulation_validation",
            "status": "completed",
            "exit_code": 0,
        },
    )
    print(f"Validation completed. Outputs: {validation_dir}", flush=True)
    return "completed"


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

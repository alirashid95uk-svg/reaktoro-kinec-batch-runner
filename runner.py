"""Command-line entry point for one YAML-defined batch case."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

from batch_runner.config import load_case
from batch_runner.outputs import write_kinetic_mapping, write_outputs
from batch_runner.simulation import preflight_case, run_simulation
from batch_runner.simulator.kinetics import uses_python_rate_callback


PREFLIGHT_PREFIX = "PREFLIGHT_RESULT:"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one Reaktoro batch case from YAML.")
    parser.add_argument("case_config", help="Path to a runnable YAML case config.")
    parser.add_argument("--preflight", action="store_true", help="Validate construction without starting a solver.")
    args = parser.parse_args()

    if args.preflight:
        case = None
        try:
            with tempfile.TemporaryDirectory(prefix="reaktoro-preflight-") as temp_dir:
                case = load_case(
                    args.case_config,
                    output_dir_override=Path(temp_dir) / "results",
                )
                report = preflight_case(case)
        except Exception as error:  # CLI boundary reports configuration failures as data.
            report = {
                "ready": False,
                "case_name": None,
                "failed_stage": "configuration_validation",
                "exception_type": type(error).__name__,
                "error_message": str(error),
                "kinetic_mapping": [],
                "technical_traceback": traceback.format_exc(),
            }
        print(f"{PREFLIGHT_PREFIX}{json.dumps(report, separators=(',', ':'))}", flush=True)
        return_code = 0 if report["ready"] else 2
        if case is not None and uses_python_rate_callback(case):
            os._exit(return_code)
        raise SystemExit(return_code)

    case = load_case(args.case_config)
    result = run_simulation(case, mapping_ready=lambda mapping: write_kinetic_mapping(case, mapping))
    output_dir = write_outputs(case, result)
    simulation_completed = result.diagnostics["simulation_completed"]
    package_complete = result.diagnostics["output_completeness"]["status"] == "complete"
    completed = simulation_completed and package_complete
    if result.exception_traceback:
        print("Technical traceback:", file=sys.stderr, flush=True)
        print(result.exception_traceback, file=sys.stderr, flush=True)
    status = (
        "Completed"
        if completed
        else "Chemistry completed; output incomplete"
        if simulation_completed
        else "Failed"
    )
    print(f"{status} case '{case.config.case.name}'. Outputs: {output_dir}", flush=True)

    # Reaktoro 2.13 can retain Python rate callbacks until faulty interpreter
    # finalization on Windows. All run outputs are closed before this point.
    if uses_python_rate_callback(case):
        os._exit(0 if completed else 1)
    if not completed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

"""Command-line entry point for one YAML-defined batch case."""

from __future__ import annotations

import argparse
import os

from batch_runner.config import load_case
from batch_runner.outputs import write_kinetic_mapping, write_outputs
from batch_runner.simulation import run_simulation


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one Reaktoro batch case from YAML.")
    parser.add_argument("case_config", help="Path to a runnable YAML case config.")
    args = parser.parse_args()

    case = load_case(args.case_config)
    result = run_simulation(case, mapping_ready=lambda mapping: write_kinetic_mapping(case, mapping))
    output_dir = write_outputs(case, result)
    completed = result.diagnostics["simulation_completed"]
    status = "Completed" if completed else "Failed"
    print(f"{status} case '{case.config.case.name}'. Outputs: {output_dir}", flush=True)

    # Reaktoro 2.13 can retain Python rate callbacks until faulty interpreter
    # finalization on Windows. All run outputs are closed before this point.
    if case.config.kinetics.enabled:
        os._exit(0 if completed else 1)
    if not completed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

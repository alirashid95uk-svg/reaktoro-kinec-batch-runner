"""Side-effect-free command-line definitions for the batch runner.

The executable remains :mod:`runner`; this module owns only argument parsing
and read-only configuration help so documentation can reuse the real CLI
definitions without importing Reaktoro or starting a simulation.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence, TextIO

from batch_runner.config.reference import render_text_reference


def build_run_parser() -> argparse.ArgumentParser:
    """Return the parser for the existing one-case simulation command."""

    parser = argparse.ArgumentParser(
        prog="python runner.py",
        description="Run one Reaktoro batch case from YAML.",
    )
    parser.add_argument("case_config", help="Path to a runnable YAML case config.")
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Validate construction without starting a solver.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Delete the configured output directory before a full run if it already exists.",
    )
    parser.add_argument(
        "--events-jsonl",
        action="store_true",
        help="Write versioned worker events to stdout.",
    )
    parser.add_argument("--operation-id", help="Controller operation identifier.")
    parser.add_argument("--run-id", help="Controller run identifier.")
    parser.add_argument(
        "--case-id",
        help="Stable source-case identifier for controller events.",
    )
    parser.add_argument(
        "--cancel-file",
        type=Path,
        help="Cooperative-cancellation sentinel path.",
    )
    return parser


def run_config_help(
    arguments: Sequence[str],
    *,
    stream: TextIO | None = None,
    error_stream: TextIO | None = None,
) -> int:
    """Print model-derived configuration help and return a process status.

    Both ``config timestep`` and ``config --help timestep`` are accepted. The
    command performs no YAML loading, path resolution, or simulation setup.
    """

    stream = sys.stdout if stream is None else stream
    error_stream = sys.stderr if error_stream is None else error_stream
    parser = argparse.ArgumentParser(
        prog="python runner.py config",
        add_help=False,
        description="Inspect the active YAML configuration schema.",
    )
    parser.add_argument("-h", "--help", action="store_true", dest="help_requested")
    parser.add_argument("query", nargs="?")
    try:
        parsed = parser.parse_args(list(arguments))
        print(render_text_reference(parsed.query), file=stream)
    except (ValueError, SystemExit) as error:
        if isinstance(error, SystemExit):
            return int(error.code or 2)
        print(f"config help error: {error}", file=error_stream)
        return 2
    return 0


def render_cli_markdown() -> str:
    """Return generated Markdown for the run and configuration-help commands."""

    return "\n".join(
        (
            "# Command-line Reference",
            "",
            "This page is generated from the same `argparse` definitions used by "
            "`runner.py`.",
            "",
            "## Run one case",
            "",
            "```text",
            build_run_parser().format_help().rstrip(),
            "```",
            "",
            "## Inspect configuration",
            "",
            "```text",
            "python runner.py config --help",
            "python runner.py config --help <section-or-path>",
            "python runner.py config <section-or-path>",
            "```",
            "",
            "Configuration help reads only the Pydantic model reference. It does "
            "not load a case, resolve paths, or construct Reaktoro objects.",
            "",
        )
    )

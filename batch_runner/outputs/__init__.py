"""Public boundary for writing traceable simulation output packages.

The runner calls these wrappers after configuration resolution and simulation.
They inject the package's deterministic CSV writer into orchestration while
keeping table derivation, plots, diagnostics, and manifest construction in
focused modules.  Output functions consume existing accepted rows and never
advance or reinterpret chemistry.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable

from batch_runner.config import ResolvedCase

from .tables import write_csv
from .writer import write_kinetic_mapping as _write_kinetic_mapping
from .writer import write_outputs as _write_outputs

if TYPE_CHECKING:
    from batch_runner.simulator import SimulationResult

__all__ = ["write_kinetic_mapping", "write_outputs"]


def write_kinetic_mapping(case: ResolvedCase, mapping: list[dict]) -> Path:
    """Create the fresh output directory and optional mapping audit CSV.

    Raises:
        FileExistsError: The resolved output directory already exists.
        OSError: Directory or file creation fails.
    """
    return _write_kinetic_mapping(case, mapping, write_csv)


def write_outputs(
    case: ResolvedCase,
    result: SimulationResult,
    cancel_requested: Callable[[], bool] | None = None,
) -> Path:
    """Write the configured package for a completed or failed simulation.

    Output-writing failures are contained by the implementation and converted
    into a partial diagnostic package where possible.  Scientific summaries,
    plots, and surrogate rows are withheld for incomplete or cancelled runs.
    """
    return _write_outputs(case, result, cancel_requested, csv_writer=write_csv)

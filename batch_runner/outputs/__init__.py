"""Stable output-package API."""

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
    return _write_kinetic_mapping(case, mapping, write_csv)


def write_outputs(
    case: ResolvedCase,
    result: SimulationResult,
    cancel_requested: Callable[[], bool] | None = None,
) -> Path:
    return _write_outputs(case, result, cancel_requested, csv_writer=write_csv)

"""Carry preparation status and streamed simulation evidence between layers.

Simulation orchestration returns these records to output writers and the CLI.
Large trajectories normally remain in temporary JSONL staging streams and are
exposed through iterators, avoiding an in-memory copy.  The records contain
Reaktoro states as opaque runtime objects; serialization is owned by outputs.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class SimulationResult:
    """Outcome and provenance of one attempted simulation.

    ``rows`` and ``solver_history`` are either in-memory sequences or ``None``
    when their corresponding JSONL stream paths are authoritative.  Boundary
    rows remain cached for summaries even when scheduled output contains no
    matching boundary.  ``diagnostics`` states whether the run completed;
    constructing this record does not imply scientific success.
    """
    rows: list[dict[str, Any]] | None
    kinetic_mapping: list[dict[str, Any]]
    solver_history: list[dict[str, Any]] | None
    diagnostics: dict[str, Any]
    initial_state: Any
    final_state: Any
    row_stream_path: Path | None = None
    solver_history_stream_path: Path | None = None
    first_row: dict[str, Any] | None = None
    last_row: dict[str, Any] | None = None
    exception_traceback: str | None = None
    source_config_sha256: str | None = None
    database_sha256: str | None = None
    kinetic_parameter_sha256: str | None = None

    def iter_rows(self):
        """Yield accepted result rows from memory or the staging stream."""
        if self.rows is not None:
            yield from self.rows
        elif self.row_stream_path is not None:
            yield from _read_json_lines(self.row_stream_path)

    def iter_solver_history(self):
        """Yield all solver-attempt records from memory or the staging stream."""
        if self.solver_history is not None:
            yield from self.solver_history
        elif self.solver_history_stream_path is not None:
            yield from _read_json_lines(self.solver_history_stream_path)

    @property
    def initial_row(self) -> dict[str, Any]:
        """Return the accepted initial boundary row.

        Raises:
            ValueError: No accepted result row was captured.
        """
        row = self.rows[0] if self.rows else self.first_row
        if row is None:
            raise ValueError("simulation has no accepted result rows")
        return row

    @property
    def final_row(self) -> dict[str, Any]:
        """Return the last accepted boundary row.

        Raises:
            ValueError: No accepted result row was captured.
        """
        row = self.rows[-1] if self.rows else self.last_row
        if row is None:
            raise ValueError("simulation has no accepted result rows")
        return row

    def cleanup_streams(self) -> None:
        """Delete temporary row/history staging files after output consumption."""
        for path in (self.row_stream_path, self.solver_history_stream_path):
            if path is not None:
                path.unlink(missing_ok=True)


@dataclass
class PreparedSimulation:
    """Chemistry preparation result without any solver advancement.

    A failed preparation retains the last constructed objects, exact failed
    stage, traceback, and input hashes so callers can still write diagnostic
    evidence.  ``ready`` is true only when no preparation exception was caught.
    """
    kinetic_mapping: list[dict[str, Any]]
    system: Any | None
    state: Any | None
    failed_stage: str | None = None
    error: Exception | None = None
    exception_traceback: str | None = None
    source_config_sha256: str | None = None
    database_sha256: str | None = None
    kinetic_parameter_sha256: str | None = None

    @property
    def ready(self) -> bool:
        """Return whether database, mapping, system, and state preparation succeeded."""
        return self.error is None


def _read_json_lines(path: Path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)

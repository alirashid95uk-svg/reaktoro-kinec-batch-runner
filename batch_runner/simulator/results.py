"""Simulation preparation and result records."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class SimulationResult:
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
        if self.rows is not None:
            yield from self.rows
        elif self.row_stream_path is not None:
            yield from _read_json_lines(self.row_stream_path)

    def iter_solver_history(self):
        if self.solver_history is not None:
            yield from self.solver_history
        elif self.solver_history_stream_path is not None:
            yield from _read_json_lines(self.solver_history_stream_path)

    @property
    def initial_row(self) -> dict[str, Any]:
        row = self.rows[0] if self.rows else self.first_row
        if row is None:
            raise ValueError("simulation has no accepted result rows")
        return row

    @property
    def final_row(self) -> dict[str, Any]:
        row = self.rows[-1] if self.rows else self.last_row
        if row is None:
            raise ValueError("simulation has no accepted result rows")
        return row

    def cleanup_streams(self) -> None:
        for path in (self.row_stream_path, self.solver_history_stream_path):
            if path is not None:
                path.unlink(missing_ok=True)


@dataclass
class PreparedSimulation:
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
        return self.error is None


def _read_json_lines(path: Path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)

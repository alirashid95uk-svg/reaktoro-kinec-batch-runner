"""Rebuildable SQLite projection of immutable run artifacts."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    run_path TEXT NOT NULL,
    case_name TEXT,
    scientific_fingerprint TEXT,
    status TEXT NOT NULL,
    output_completeness TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    kinetic_model TEXT,
    workflow_mode TEXT,
    output_schema_version TEXT,
    warnings_json TEXT NOT NULL,
    study_id TEXT,
    artifact_groups_json TEXT NOT NULL,
    legacy_unmanaged INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_case_name ON runs(case_name);
CREATE INDEX IF NOT EXISTS runs_status ON runs(status);
CREATE INDEX IF NOT EXISTS runs_fingerprint ON runs(scientific_fingerprint);
CREATE INDEX IF NOT EXISTS runs_study ON runs(study_id);
"""


def rebuild_index(index_path: str | Path, runs_root: str | Path) -> int:
    """Replace the disposable index from artifact evidence only."""
    index = Path(index_path)
    index.parent.mkdir(parents=True, exist_ok=True)
    temporary = index.with_suffix(index.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    count = 0
    connection = sqlite3.connect(temporary)
    try:
        connection.executescript(SCHEMA)
        for directory in sorted(_run_directories(Path(runs_root))):
            row = _projection(directory)
            connection.execute(
                "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                row,
            )
            count += 1
        connection.commit()
    finally:
        connection.close()
    temporary.replace(index)
    return count


def search_runs(
    index_path: str | Path,
    *,
    text: str = "",
    status: str | None = None,
    kinetic_model: str | None = None,
    workflow_mode: str | None = None,
    study_id: str | None = None,
    output_schema_version: str | None = None,
    started_after: str | None = None,
    started_before: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    clauses = []
    parameters: list[Any] = []
    if text:
        clauses.append("(case_name LIKE ? OR run_id LIKE ? OR warnings_json LIKE ?)")
        pattern = f"%{text}%"
        parameters.extend([pattern, pattern, pattern])
    if status:
        clauses.append("status = ?")
        parameters.append(status)
    for column, value in (
        ("kinetic_model", kinetic_model),
        ("workflow_mode", workflow_mode),
        ("study_id", study_id),
        ("output_schema_version", output_schema_version),
    ):
        if value:
            clauses.append(f"{column} = ?")
            parameters.append(value)
    if started_after:
        clauses.append("started_at >= ?")
        parameters.append(started_after)
    if started_before:
        clauses.append("started_at <= ?")
        parameters.append(started_before)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    query = f"SELECT * FROM runs{where} ORDER BY finished_at DESC, run_id LIMIT ?"
    parameters.append(limit)
    with sqlite3.connect(index_path) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(query, parameters)]


def _run_directories(root: Path):
    seen: set[Path] = set()
    for name in ("run_record.json", "manifest.json", "diagnostics.json"):
        for artifact in root.rglob(name):
            directory = artifact.parent
            if directory.name == "results":
                directory = directory.parent
            if directory not in seen:
                seen.add(directory)
                yield directory


def _projection(directory: Path) -> tuple[Any, ...]:
    run = _read_json(directory / "run_record.json")
    results = directory / "results" if (directory / "results").is_dir() else directory
    manifest = _read_json(results / "manifest.json")
    diagnostics = _read_json(results / "diagnostics.json")
    legacy = not bool(run)
    run_identity = manifest.get("run_identity", {})
    input_snapshot = manifest.get("input_snapshot", {}) or {}
    solver = manifest.get("solver_configuration", {})
    status = run.get("state") or run.get("termination_category") or _legacy_status(diagnostics)
    run_completeness = run.get("output_completeness")
    completeness = (
        run_completeness.get("status") if isinstance(run_completeness, dict) else run_completeness
        or diagnostics.get("output_completeness", {}).get("status")
        or "unknown"
    )
    artifacts = sorted(
        path.relative_to(results).parts[0]
        for path in results.iterdir()
    ) if results.is_dir() else []
    return (
        str(run.get("run_id") or f"legacy:{directory.as_posix()}"),
        str(directory.resolve()),
        run.get("case_name") or run.get("case_id") or run_identity.get("case_name") or diagnostics.get("case_name"),
        run.get("scientific_fingerprint"),
        status,
        completeness,
        run.get("started_at_utc") or run_identity.get("run_started_at"),
        run.get("finished_at_utc") or run_identity.get("run_finished_at"),
        run.get("kinetic_model") or input_snapshot.get("kinetics_setup", {}).get("model"),
        run.get("workflow_mode") or solver.get("workflow", {}).get("mode"),
        manifest.get("output_schema_version") or diagnostics.get("output_schema_version"),
        json.dumps(diagnostics.get("warnings", []), separators=(",", ":")),
        run.get("study_id"),
        json.dumps(sorted(set(artifacts)), separators=(",", ":")),
        int(legacy),
    )


def _legacy_status(diagnostics: dict[str, Any]) -> str:
    completed = diagnostics.get("simulation_completed") is True
    completeness = diagnostics.get("output_completeness", {}).get("status")
    if completed and completeness == "complete":
        return "legacy_completed"
    if completed:
        return "legacy_chemistry_completed_output_incomplete"
    if diagnostics:
        return "legacy_partial_or_failed"
    return "legacy_indeterminate"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}

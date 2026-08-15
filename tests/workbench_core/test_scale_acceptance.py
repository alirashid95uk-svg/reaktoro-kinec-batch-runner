"""Representative target-workstation scale fixtures from the workbench contract."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from workbench_core.result_readers import ResultPackage
from workbench_core.run_index import rebuild_index, search_runs
from workbench_core.schemas.study_spec import GeneratedSampleRecord, StudyManifest


HASH = "0" * 64


def test_index_rebuilds_10_000_mixed_run_records_deterministically(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    states = ("completed", "partial_numerical_failure", "blocked_preflight", "crashed")
    for index in range(10_000):
        run_dir = runs / f"run-{index:05d}"
        run_dir.mkdir(parents=True)
        (run_dir / "run_record.json").write_text(
            json.dumps(
                {
                    "run_id": f"run-{index:05d}",
                    "case_id": f"case-{index % 25:02d}",
                    "state": states[index % len(states)],
                    "scientific_fingerprint": f"{index:064x}",
                    "output_completeness": {
                        "status": "complete" if index % len(states) == 0 else "partial"
                    },
                    "started_at_utc": "2026-08-05T00:00:00Z",
                    "finished_at_utc": "2026-08-05T00:00:01Z",
                }
            ),
            encoding="utf-8",
        )
    database = tmp_path / "run_index.sqlite"
    assert rebuild_index(database, runs) == 10_000
    assert rebuild_index(database, runs) == 10_000
    for state in states:
        assert len(search_runs(database, status=state, limit=10_000)) == 2_500


def test_result_reader_streams_1_000_000_rows_and_large_solver_history(tmp_path: Path) -> None:
    package = tmp_path / "results"
    package.mkdir()
    (package / "manifest.json").write_text(
        json.dumps(
            {
                "output_schema_version": "objective1_audit_v4",
                "run_identity": {"simulation_completed": True, "run_id": "scale-run"},
                "output_files": ["manifest.json", "diagnostics.json", "timeseries.csv", "solver_history.csv"],
            }
        ),
        encoding="utf-8",
    )
    (package / "diagnostics.json").write_text(
        json.dumps(
            {
                "output_schema_version": "objective1_audit_v4",
                "simulation_completed": True,
                "output_completeness": {"status": "complete"},
            }
        ),
        encoding="utf-8",
    )
    with (package / "timeseries.csv").open("w", encoding="utf-8", newline="") as stream:
        stream.write("time_s,pH\n")
        stream.writelines(f"{index},7.0\n" for index in range(1_000_000))
    with (package / "solver_history.csv").open("w", encoding="utf-8", newline="") as stream:
        stream.write("attempt_index,time_end_s,accepted,failure_reason\n")
        stream.writelines(
            f"{index},{index},{str(index % 11 != 0)},"
            f"{'solver rejection' if index % 11 == 0 else ''}\n"
            for index in range(250_000)
        )
    reader = ResultPackage(package)
    assert reader.status.interpretation_supported
    assert sum(len(chunk) for chunk in reader.iter_table("timeseries.csv", chunksize=100_000)) == 1_000_000
    assert sum(len(chunk) for chunk in reader.iter_table("solver_history.csv", chunksize=50_000)) == 250_000


def test_study_manifest_validates_500_traceable_samples() -> None:
    now = datetime(2026, 8, 5, tzinfo=timezone.utc)
    samples = tuple(
        GeneratedSampleRecord(
            study_id="scale-study",
            sample_id=f"sample-{index:03d}",
            baseline_case_sha256=HASH,
            input_parameter_vector={"pressure_bar": float(index)},
            canonical_parameter_vector={"pressure_bar": float(index)},
            constraint_outcomes=(),
            generation_outcome="generated",
            case_path=f"cases/sample-{index:03d}.yaml",
            case_sha256=f"{index:064x}",
            deliberate_replicate=False,
            validation_status="not_checked",
        )
        for index in range(500)
    )
    manifest = StudyManifest(
        study_manifest_schema_version="1.0",
        study_id="scale-study",
        study_name="500 sample acceptance fixture",
        created_at_utc=now,
        finalised_at_utc=None,
        specification_sha256=HASH,
        generator_version="1.0",
        sampling_method="grid",
        seed=0,
        samples=samples,
        required_outputs=("timeseries.csv",),
        validity_domain={"purpose": "scale acceptance"},
        dataset_exports=(),
        ready=False,
    )
    assert len(manifest.samples) == 500

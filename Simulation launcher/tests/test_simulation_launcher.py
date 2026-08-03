from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import yaml

LAUNCHER_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = LAUNCHER_DIR.parent
for import_path in (PROJECT_ROOT, LAUNCHER_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from batch_runner.config import CaseConfig
from launcher_diagnosis import format_preflight_diagnosis, write_run_diagnosis
from simulation_launcher import create_run_snapshot


SOURCE_CASE = PROJECT_ROOT / "cases" / "calcite_quartz_illite_development.yaml"
WINDOWS_LAUNCHER = LAUNCHER_DIR / "Run Simulations.cmd"


def test_windows_launcher_activates_fypr_reaktoro_with_conda() -> None:
    launcher = WINDOWS_LAUNCHER.read_text(encoding="utf-8")

    assert "%ProgramData%\\miniconda3\\Scripts\\conda.exe" in launcher
    assert '"%CONDA_EXE_PATH%" run --no-capture-output -n fypr-reaktoro python "%~dp0simulation_launcher.py"' in launcher
    assert ".conda\\envs\\fypr-reaktoro\\python.exe" not in launcher


def test_diagnosis_distinguishes_blocked_input_from_incomplete_output(tmp_path: Path) -> None:
    preflight = format_preflight_diagnosis(
        Path("blocked.yaml"),
        {
            "ready": False,
            "failed_stage": "mapping",
            "error_message": "missing kinec records",
            "kinetic_mapping": [
                {
                    "mineral_name": "Pyrite",
                    "status": "failed",
                    "reason": "missing kinec parameter record",
                }
            ],
        },
    )
    assert "BLOCKED — input or kinetic compatibility" in preflight
    assert "SOLVER STARTED: No" in preflight
    assert "Pyrite: missing kinec parameter record" in preflight

    run_dir = tmp_path / "run"
    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True)
    (results_dir / "diagnostics.json").write_text(
        json.dumps(
            {
                "simulation_completed": True,
                "failed_stage": None,
                "error_message": None,
                "final_time_reached_s": 60.0,
                "number_of_accepted_steps": 6,
                "number_of_rejected_steps": 0,
                "output_completeness": {"status": "partial", "files_written": []},
                "output_failure": {
                    "failed_stage": "output_writing",
                    "exception_type": "OSError",
                    "error_message": "forced output failure",
                },
            }
        ),
        encoding="utf-8",
    )
    diagnosis_path, diagnosis = write_run_diagnosis(run_dir, 1)

    assert diagnosis_path.is_file()
    assert "CHEMISTRY COMPLETED — output package incomplete" in diagnosis
    assert "SOLVER STARTED: Yes" in diagnosis
    assert "ISSUE: forced output failure" in diagnosis

    (results_dir / "diagnostics.json").write_text(
        json.dumps(
            {
                "simulation_completed": False,
                "failed_stage": "mapping",
                "error_message": "primary mapping failure",
                "final_time_reached_s": 0.0,
                "number_of_accepted_steps": 0,
                "number_of_rejected_steps": 0,
                "output_completeness": {"status": "partial", "files_written": []},
                "output_failure": {
                    "failed_stage": "output_writing",
                    "exception_type": "OSError",
                    "error_message": "secondary output failure",
                },
            }
        ),
        encoding="utf-8",
    )
    _, primary_diagnosis = write_run_diagnosis(run_dir, 1)
    assert "FAILED STAGE: mapping" in primary_diagnosis
    assert "ISSUE: primary mapping failure" in primary_diagnosis


def test_run_snapshot_preserves_scientific_settings_and_uses_fresh_output(tmp_path: Path) -> None:
    source_text = SOURCE_CASE.read_text(encoding="utf-8")
    snapshot = create_run_snapshot(
        SOURCE_CASE,
        runs_dir=tmp_path / "runs",
        now=datetime(2026, 8, 3, 14, 30, 12),
    )
    second_snapshot = create_run_snapshot(
        SOURCE_CASE,
        runs_dir=tmp_path / "runs",
        now=datetime(2026, 8, 3, 14, 30, 12),
    )

    source = yaml.safe_load(source_text)
    generated_text = snapshot.config_path.read_text(encoding="utf-8")
    generated = yaml.safe_load(generated_text)
    source["paths"].pop("output_dir")
    generated["paths"].pop("output_dir")

    assert generated == source
    assert "# Source case: cases/calcite_quartz_illite_development.yaml" in generated_text
    assert "# Source SHA256:" in generated_text
    assert snapshot.config_path.is_file()
    assert not snapshot.results_dir.exists()
    assert second_snapshot.run_dir.name == "2026-08-03_14-30-12_02"
    assert SOURCE_CASE.read_text(encoding="utf-8") == source_text
    CaseConfig.model_validate(yaml.safe_load(snapshot.config_path.read_text(encoding="utf-8")))

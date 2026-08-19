from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "cases" / "jayasekara_2020_reproduction.yaml"
MONITOR_CASE = ROOT / "cases" / "jayasekara_2020_reproduction_monitor.yaml"
RUNS_ROOT = ROOT / "runs" / "jayasekara_2020_reproduction"
ARTIFACT = ROOT / "results" / "jayasekara-validation"
EXPECTED_FINAL_TIME_S = 259.0 * 24.0 * 60.0 * 60.0

MONITOR = {
    "enabled": True,
    "refresh_interval_s": 0.5,
    "scalars": ["pH"],
    "species": ["H2O"],
    "minerals": ["Quartz", "Illite", "Kaolinite", "Montmor-Ca"],
    "result_times": [
        {"value": 14.0, "unit": "days"},
        {"value": 35.0, "unit": "days"},
        {"value": 63.0, "unit": "days"},
        {"value": 126.0, "unit": "days"},
        {"value": 259.0, "unit": "days"},
    ],
}


def _without_output_dir(raw: dict) -> dict:
    data = copy.deepcopy(raw)
    data["paths"].pop("output_dir", None)
    return data


def main() -> None:
    raw = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
    raw["outputs"]["monitor"] = copy.deepcopy(MONITOR)
    MONITOR_CASE.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    before = set(RUNS_ROOT.iterdir()) if RUNS_ROOT.exists() else set()
    try:
        subprocess.run(
            [sys.executable, str(ROOT / "runner.py"), str(MONITOR_CASE)],
            cwd=ROOT,
            check=True,
        )
    finally:
        MONITOR_CASE.unlink(missing_ok=True)

    after = set(RUNS_ROOT.iterdir()) if RUNS_ROOT.exists() else set()
    created = sorted(after - before)
    if len(created) != 1:
        raise SystemExit(f"expected one fresh Jayasekara run directory, found {len(created)}")
    run_dir = created[0]
    snapshot = run_dir / "run_case.yaml"
    diagnostics_path = run_dir / "results" / "diagnostics.json"
    if not snapshot.is_file():
        raise SystemExit(f"missing run snapshot: {snapshot}")
    if not diagnostics_path.is_file():
        raise SystemExit(f"missing diagnostics: {diagnostics_path}")

    run_raw = yaml.safe_load(snapshot.read_text(encoding="utf-8"))
    if _without_output_dir(run_raw) != _without_output_dir(raw):
        raise SystemExit("run snapshot changed settings other than paths.output_dir")

    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    if not diagnostics.get("simulation_completed"):
        raise SystemExit("Jayasekara monitor validation did not complete")
    if diagnostics.get("output_completeness", {}).get("status") != "complete":
        raise SystemExit("Jayasekara monitor output package is incomplete")
    if float(diagnostics.get("final_time_reached_s", -1.0)) != EXPECTED_FINAL_TIME_S:
        raise SystemExit("Jayasekara monitor validation did not reach 259 days")

    if ARTIFACT.exists():
        shutil.rmtree(ARTIFACT)
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(run_dir, ARTIFACT)
    print(f"Jayasekara monitor validation passed: {diagnostics_path}")


if __name__ == "__main__":
    main()

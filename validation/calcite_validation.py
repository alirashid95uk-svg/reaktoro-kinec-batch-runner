from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "cases" / "calcite_quartz_illite_development.yaml"
OUTPUT = ROOT / "outputs" / "calcite_quartz_illite_development"
ARTIFACT = ROOT / "results" / "calcite-validation"


def main() -> None:
    subprocess.run(
        [sys.executable, str(ROOT / "runner.py"), str(CASE), "--overwrite"],
        cwd=ROOT,
        check=True,
    )
    diagnostics_path = OUTPUT / "diagnostics.json"
    if not diagnostics_path.is_file():
        raise SystemExit(f"missing diagnostics: {diagnostics_path}")
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    if not diagnostics.get("simulation_completed"):
        raise SystemExit("canonical Calcite validation did not complete")
    if diagnostics.get("output_completeness", {}).get("status") != "complete":
        raise SystemExit("canonical Calcite output package is incomplete")
    if float(diagnostics.get("final_time_reached_s", -1.0)) != 60.0:
        raise SystemExit("canonical Calcite validation did not reach 60 s")

    if ARTIFACT.exists():
        shutil.rmtree(ARTIFACT)
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(OUTPUT, ARTIFACT)
    print(f"Calcite validation passed: {diagnostics_path}")


if __name__ == "__main__":
    main()

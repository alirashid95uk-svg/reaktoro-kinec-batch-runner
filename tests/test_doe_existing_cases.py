from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from batch_runner.doe import generate_design, load_manifest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_CASE = PROJECT_ROOT / "tests" / "fixtures" / "cases" / "synthetic_kinec_case.yaml"
KINEC_PATH = PROJECT_ROOT / "data" / "kinetics" / "kinec_rates_minimal.yaml"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_spec(path: Path, case_path: Path) -> None:
    payload = {
        "mode": "existing_cases",
        "name": "existing_dependency_identity",
        "cases": [
            {
                "case_id": "case_001",
                "path": str(case_path),
                "sha256": _sha256(case_path),
                "provenance": {
                    "kind": "user_defined",
                    "justification": "software verification only",
                    "applicability_domain": "synthetic software-test fixture",
                },
            }
        ],
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def test_existing_case_dependency_bytes_change_design_identity(tmp_path: Path) -> None:
    kinetics_copy = tmp_path / "rates.yaml"
    kinetics_copy.write_bytes(KINEC_PATH.read_bytes())

    raw = yaml.safe_load(SYNTHETIC_CASE.read_text(encoding="utf-8"))
    raw["kinetics"]["path"] = str(kinetics_copy)
    case_path = tmp_path / "case.yaml"
    case_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    spec_path = tmp_path / "existing.yaml"
    _write_spec(spec_path, case_path)

    package_one = generate_design(spec_path, tmp_path / "design_one")
    _, manifest_one = load_manifest(package_one)
    identity_one = manifest_one["dependencies"]["kinetics"][0]["identity"]["sha256"]

    kinetics_copy.write_text(
        kinetics_copy.read_text(encoding="utf-8") + "\n# dependency identity mutation\n",
        encoding="utf-8",
    )
    package_two = generate_design(spec_path, tmp_path / "design_two")
    _, manifest_two = load_manifest(package_two)
    identity_two = manifest_two["dependencies"]["kinetics"][0]["identity"]["sha256"]

    assert manifest_one["generation_status"] == "ready"
    assert manifest_two["generation_status"] == "ready"
    assert identity_one != identity_two
    assert manifest_one["design_id"] != manifest_two["design_id"]
    assert len(manifest_one["dependencies"]["databases"]) == 1
    assert len(manifest_one["dependencies"]["kinetics"]) == 1

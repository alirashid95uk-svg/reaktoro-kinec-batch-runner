from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from batch_runner.doe import generate_design, load_manifest, read_ledger


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_CASE = PROJECT_ROOT / "tests" / "fixtures" / "cases" / "synthetic_kinec_case.yaml"
KINEC_PATH = PROJECT_ROOT / "data" / "kinetics" / "kinec_rates_minimal.yaml"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _provenance() -> dict:
    return {
        "kind": "user_defined",
        "justification": "software verification only",
        "applicability_domain": "synthetic software-test fixture",
    }


def _write_spec(path: Path, case_path: Path) -> None:
    payload = {
        "mode": "existing_cases",
        "name": "existing_dependency_identity",
        "cases": [
            {
                "case_id": "case_001",
                "path": str(case_path),
                "sha256": _sha256(case_path),
                "provenance": _provenance(),
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


def test_existing_cases_isolate_schema_invalid_case(tmp_path: Path) -> None:
    valid_raw = yaml.safe_load(SYNTHETIC_CASE.read_text(encoding="utf-8"))
    valid_path = tmp_path / "valid.yaml"
    valid_path.write_text(yaml.safe_dump(valid_raw, sort_keys=False), encoding="utf-8")

    invalid_raw = yaml.safe_load(SYNTHETIC_CASE.read_text(encoding="utf-8"))
    invalid_raw["physical"]["pressure_bar"] = -1.0
    invalid_path = tmp_path / "invalid.yaml"
    invalid_path.write_text(yaml.safe_dump(invalid_raw, sort_keys=False), encoding="utf-8")

    spec = {
        "mode": "existing_cases",
        "name": "fault_tolerant_existing_cases",
        "cases": [
            {
                "case_id": "invalid_first",
                "path": str(invalid_path),
                "sha256": _sha256(invalid_path),
                "provenance": _provenance(),
            },
            {
                "case_id": "valid_second",
                "path": str(valid_path),
                "sha256": _sha256(valid_path),
                "provenance": _provenance(),
            },
        ],
    }
    spec_path = tmp_path / "existing-mixed.yaml"
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")

    package = generate_design(spec_path, tmp_path / "mixed-design")
    package_root, manifest = load_manifest(package)
    ledger = read_ledger(package_root, manifest)

    assert manifest["generation_status"] == "ready_with_exclusions"
    assert [record["outcome"] for record in ledger] == ["schema_blocked", "accepted"]
    assert ledger[0]["sample_id"] is None
    assert ledger[1]["sample_id"] == "sample-000001"
    assert ledger[0]["error"]["stage"] == "configuration_validation"

    identities = manifest["resolved_spec"]
    resolved = __import__("json").loads(
        (package_root / identities["package_path"]).read_text(encoding="utf-8")
    )
    invalid_identity = resolved["existing_cases"][0]
    assert invalid_identity["database_identity"] is not None
    assert invalid_identity["kinetics_identity"] is not None

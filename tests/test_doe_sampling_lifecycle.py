from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import yaml

from batch_runner.doe import generate_design, load_manifest, read_ledger
from batch_runner.doe.sampling import ResolvedParameter, random_vectors


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_CASE = PROJECT_ROOT / "tests" / "fixtures" / "cases" / "synthetic_kinec_case.yaml"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _provenance() -> dict:
    return {
        "kind": "user_defined",
        "justification": "software verification only",
        "applicability_domain": "synthetic software-test fixture",
        "distribution_rationale": "software verification distribution",
    }


def _temperature_parameter(sampling: dict) -> dict:
    return {
        "parameter_id": "temperature",
        "target": {"kind": "temperature"},
        "sampling": sampling,
        "provenance": _provenance(),
    }


def _base_spec(sampler: dict, sampling: dict) -> dict:
    return {
        "mode": "generated",
        "name": "sampling_lifecycle",
        "base_case": {
            "path": str(SYNTHETIC_CASE),
            "sha256": _sha256(SYNTHETIC_CASE),
        },
        "parameters": [_temperature_parameter(sampling)],
        "sampler": sampler,
        "constraints": [],
    }


def test_random_sampling_uses_declared_pcg64_draw_order() -> None:
    parameters = [
        ResolvedParameter(
            parameter_id="x",
            data_type="float",
            canonical_unit="1",
            sampling={"kind": "uniform", "lower": 10.0, "upper": 20.0},
        ),
        ResolvedParameter(
            parameter_id="choice",
            data_type="int",
            canonical_unit="1",
            sampling={"kind": "discrete_uniform", "values": [2, 4, 8]},
        ),
    ]
    actual = list(random_vectors(parameters, seed=19, max_candidates=3))

    rng = np.random.Generator(np.random.PCG64(19))
    expected = []
    for _ in range(3):
        u_x = float(rng.random())
        u_choice = float(rng.random())
        expected.append(
            [10.0 + u_x * 10.0, [2, 4, 8][int(np.floor(u_choice * 3))]]
        )
    assert actual == expected


def test_random_design_is_incomplete_at_exact_max_candidates(tmp_path: Path) -> None:
    spec = _base_spec(
        {"kind": "random", "sample_count": 2, "seed": 17, "max_candidates": 3},
        {"kind": "uniform", "lower": 20.0, "upper": 30.0, "entered_unit": "degC"},
    )
    spec["constraints"] = [
        {
            "constraint_id": "reject_all",
            "kind": "bounds",
            "parameter_id": "temperature",
            "lower": {"value": 100.0, "unit": "degC"},
        }
    ]
    spec_path = tmp_path / "random.yaml"
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")

    package = generate_design(spec_path, tmp_path / "random-design")
    package_root, manifest = load_manifest(package)
    ledger = read_ledger(package_root, manifest)

    assert manifest["generation_status"] == "incomplete"
    assert manifest["candidate_counts"]["attempted"] == 3
    assert manifest["candidate_counts"].get("accepted", 0) == 0
    assert [record["outcome"] for record in ledger] == [
        "constraint_rejected",
        "constraint_rejected",
        "constraint_rejected",
    ]


def test_lhs_rejection_blocks_whole_design(tmp_path: Path) -> None:
    spec = _base_spec(
        {"kind": "latin_hypercube", "sample_count": 4, "seed": 23},
        {"kind": "uniform", "lower": 20.0, "upper": 30.0, "entered_unit": "degC"},
    )
    spec["constraints"] = [
        {
            "constraint_id": "reject_all",
            "kind": "bounds",
            "parameter_id": "temperature",
            "lower": {"value": 100.0, "unit": "degC"},
        }
    ]
    spec_path = tmp_path / "lhs.yaml"
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")

    package = generate_design(spec_path, tmp_path / "lhs-design")
    package_root, manifest = load_manifest(package)
    ledger = read_ledger(package_root, manifest)

    assert manifest["generation_status"] == "blocked"
    assert len(ledger) == 4
    assert all(record["sample_id"] is None for record in ledger)
    assert all(record["outcome"] == "constraint_rejected" for record in ledger)


def test_imported_matrix_is_snapshotted_hashed_and_preserves_row_order(tmp_path: Path) -> None:
    matrix = tmp_path / "matrix.csv"
    matrix.write_text("temperature\n26\n27\n", encoding="utf-8")
    matrix_hash = _sha256(matrix)
    spec = _base_spec(
        {"kind": "imported_matrix", "path": str(matrix), "sha256": matrix_hash},
        {"kind": "imported_column", "column": "temperature", "entered_unit": "degC"},
    )
    spec_path = tmp_path / "imported.yaml"
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")

    package = generate_design(spec_path, tmp_path / "imported-design")
    package_root, manifest = load_manifest(package)
    ledger = read_ledger(package_root, manifest)

    assert manifest["generation_status"] == "ready"
    assert manifest["imported_matrix"]["sha256"] == matrix_hash
    snapshot = package_root / manifest["imported_matrix"]["package_path"]
    assert snapshot.read_bytes() == matrix.read_bytes()
    assert [record["canonical_parameter_vector"][0]["value"] for record in ledger] == [
        26.0,
        27.0,
    ]
    assert [record["sample_id"] for record in ledger] == [
        "sample-000001",
        "sample-000002",
    ]

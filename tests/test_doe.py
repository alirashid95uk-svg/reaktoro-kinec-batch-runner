from __future__ import annotations

import ast
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
import scipy
import yaml
from scipy.stats import qmc

from batch_runner.config import load_case
from batch_runner.doe import generate_design, launch_sample, load_manifest, read_ledger
from batch_runner.doe.constraints import evaluate_constraints
from batch_runner.doe.models import (
    BoundsConstraint,
    ConstraintLiteral,
    ParameterSpec,
    Target,
    UserDefinedProvenance,
    Uniform,
    ExplicitValues,
    LogUniform,
)
from batch_runner.doe.sampling import (
    SCIPY_QMC_VERSION,
    ResolvedParameter,
    grid_vectors,
    lhs_vectors,
    sobol_vectors,
)
from batch_runner.doe.targets import canonicalize_sampling, resolve_target
from batch_runner.outputs.manifest import _load_doe_lineage


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_CASE = PROJECT_ROOT / "tests" / "fixtures" / "cases" / "synthetic_kinec_case.yaml"
KINEC_PATH = PROJECT_ROOT / "data" / "kinetics" / "kinec_rates_minimal.yaml"
PALANDRI_PATH = PROJECT_ROOT / "data" / "kinetics" / "PalandriKharaka_local.yaml"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _raw_case() -> dict:
    return yaml.safe_load(SYNTHETIC_CASE.read_text(encoding="utf-8"))


def _provenance() -> UserDefinedProvenance:
    return UserDefinedProvenance(
        kind="user_defined",
        justification="software verification only",
        applicability_domain="synthetic software-test fixture",
        distribution_rationale="deterministic software verification range",
    )


def _generated_spec(*, value: float = 58001.0) -> dict:
    return {
        "mode": "generated",
        "name": "synthetic_kinec_doe",
        "base_case": {
            "path": str(SYNTHETIC_CASE),
            "sha256": _sha256(SYNTHETIC_CASE),
        },
        "parameters": [
            {
                "parameter_id": "calcite_acid_E",
                "target": {"kind": "kinec_E", "mineral": "Calcite", "term": "acid"},
                "sampling": {
                    "kind": "explicit_values",
                    "values": [value],
                    "entered_unit": "J/mol",
                },
                "provenance": {
                    "kind": "user_defined",
                    "justification": "software verification only",
                    "applicability_domain": "synthetic software-test fixture",
                    "distribution_rationale": "single deterministic software-test value",
                },
            }
        ],
        "sampler": {"kind": "grid"},
        "constraints": [],
    }


def test_doe_package_imports_no_workbench_modules() -> None:
    for source in (PROJECT_ROOT / "batch_runner" / "doe").glob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            assert not any(
                name == "workbench"
                or name.startswith("workbench.")
                or name == "workbench_core"
                or name.startswith("workbench_core.")
                for name in names
            )


def test_grid_order_is_declared_parameter_order() -> None:
    parameters = [
        ResolvedParameter(
            parameter_id="a",
            data_type="float",
            canonical_unit="1",
            sampling={"kind": "explicit_values", "values": [1.0, 2.0]},
        ),
        ResolvedParameter(
            parameter_id="b",
            data_type="float",
            canonical_unit="1",
            sampling={"kind": "explicit_values", "values": [10.0, 20.0]},
        ),
    ]
    assert list(grid_vectors(parameters)) == [
        [1.0, 10.0],
        [1.0, 20.0],
        [2.0, 10.0],
        [2.0, 20.0],
    ]


def test_lhs_matches_pinned_scipy_contract() -> None:
    assert scipy.__version__ == SCIPY_QMC_VERSION == "1.16.1"
    parameter = ResolvedParameter(
        parameter_id="x",
        data_type="float",
        canonical_unit="1",
        sampling={"kind": "uniform", "lower": 0.0, "upper": 1.0},
    )
    actual = np.asarray(lhs_vectors([parameter], seed=42, sample_count=4))
    expected = qmc.LatinHypercube(
        1,
        scramble=True,
        strength=1,
        optimization=None,
        rng=np.random.Generator(np.random.PCG64(42)),
    ).random(n=4)
    np.testing.assert_array_equal(actual, expected)


def test_sobol_matches_pinned_base2_contract() -> None:
    parameter = ResolvedParameter(
        parameter_id="x",
        data_type="float",
        canonical_unit="1",
        sampling={"kind": "uniform", "lower": 0.0, "upper": 1.0},
    )
    actual = np.asarray(sobol_vectors([parameter], seed=7, sample_count=4))
    expected = qmc.Sobol(
        1,
        scramble=True,
        bits=64,
        optimization=None,
        rng=np.random.Generator(np.random.PCG64(7)),
    ).random_base2(2)
    np.testing.assert_array_equal(actual, expected)


def test_integer_target_rejects_continuous_sampling() -> None:
    raw = _raw_case()
    resolved = resolve_target(Target(kind="solver_max_internal_steps"), raw, None)
    parameter = ParameterSpec(
        parameter_id="steps",
        target=Target(kind="solver_max_internal_steps"),
        sampling=Uniform(kind="uniform", lower=10, upper=20),
        provenance=_provenance(),
    )
    with pytest.raises(ValueError, match="does not allow sampling kind"):
        canonicalize_sampling(parameter, resolved, year_days=None)


def test_pk_lgk_rejects_log_uniform() -> None:
    raw = _raw_case()
    raw["kinetics"] = {
        "enabled": True,
        "model": "palandri_kharaka",
        "path": str(PALANDRI_PATH),
    }
    kinetics = yaml.safe_load(PALANDRI_PATH.read_text(encoding="utf-8"))
    target = Target(kind="pk_lgk", record="Calcite", mechanism="Acid")
    resolved = resolve_target(target, raw, kinetics)
    parameter = ParameterSpec(
        parameter_id="calcite_lgk",
        target=target,
        sampling=LogUniform(
            kind="log_uniform",
            lower=0.1,
            upper=1.0,
            entered_unit="lg10(mol m^-2 s^-1)",
        ),
        provenance=_provenance(),
    )
    with pytest.raises(ValueError, match="does not allow sampling kind"):
        canonicalize_sampling(parameter, resolved, year_days=None)


def test_kinec_activation_energy_rejects_negative_values() -> None:
    raw = _raw_case()
    kinetics = yaml.safe_load(KINEC_PATH.read_text(encoding="utf-8"))
    target = Target(kind="kinec_E", mineral="Calcite", term="acid")
    resolved = resolve_target(target, raw, kinetics)
    parameter = ParameterSpec(
        parameter_id="negative_E",
        target=target,
        sampling=ExplicitValues(
            kind="explicit_values", values=[-100.0], entered_unit="J/mol"
        ),
        provenance=_provenance(),
    )
    with pytest.raises(ValueError, match="requires value >= 0"):
        canonicalize_sampling(parameter, resolved, year_days=None)


def test_dimensional_constraint_requires_unit() -> None:
    parameter = ResolvedParameter(
        parameter_id="pressure",
        data_type="float",
        canonical_unit="bar",
        sampling={"kind": "uniform", "lower": 1.0, "upper": 2.0},
    )
    constraint = BoundsConstraint(
        constraint_id="pressure_floor",
        kind="bounds",
        parameter_id="pressure",
        lower=ConstraintLiteral(value=1.0, unit=None),
    )
    with pytest.raises(ValueError, match="requires unit"):
        evaluate_constraints(
            [constraint], {"pressure": 1.5}, [parameter], year_days=None
        )


def test_generated_kinec_design_packages_dependencies_and_preflights(tmp_path: Path) -> None:
    source_hash_before = _sha256(KINEC_PATH)
    spec_path = tmp_path / "doe.yaml"
    spec_path.write_text(yaml.safe_dump(_generated_spec(), sort_keys=False), encoding="utf-8")

    package = generate_design(spec_path, tmp_path / "design")
    package_root, manifest = load_manifest(package)
    ledger = read_ledger(package_root, manifest)

    assert manifest["generation_status"] == "ready"
    assert manifest["candidate_counts"]["accepted"] == 1
    assert ledger[0]["candidate_id"] == "candidate-000001"
    assert ledger[0]["sample_id"] == "sample-000001"
    assert ledger[0]["outcome"] == "accepted"
    assert ledger[0]["kinetics_path"]
    assert _sha256(KINEC_PATH) == source_hash_before

    candidate_path = package_root / ledger[0]["case_path"]
    resolved = load_case(
        candidate_path,
        output_dir_override=tmp_path / "resolved-output",
        artifact_root=package_root,
    )
    assert package_root in resolved.database_path.parents
    assert package_root in resolved.kinetics_path.parents

    result = launch_sample(package, "sample-000001", preflight_only=True)
    try:
        assert result["executed"] is False
        assert result["preflight"]["ready"] is True
        assert result["lineage"]["dependencies"]["database"]["source"] == "local"
        assert result["lineage"]["dependencies"]["kinetics"]["enabled"] is True
        snapshot = Path(result["run_snapshot"])
        run_raw = yaml.safe_load(snapshot.read_text(encoding="utf-8"))
        candidate_raw = yaml.safe_load(candidate_path.read_text(encoding="utf-8"))
        run_output = run_raw["paths"].pop("output_dir")
        candidate_raw["paths"].pop("output_dir")
        assert run_output
        assert run_raw == candidate_raw
    finally:
        snapshot_value = result.get("run_snapshot")
        if snapshot_value:
            shutil.rmtree(Path(snapshot_value).parent, ignore_errors=True)


def test_real_doe_execution_writes_v4_lineage(tmp_path: Path) -> None:
    spec_path = tmp_path / "doe-execute.yaml"
    spec_path.write_text(yaml.safe_dump(_generated_spec(value=58002.0), sort_keys=False), encoding="utf-8")
    package = generate_design(spec_path, tmp_path / "design-execute")

    result = launch_sample(package, "sample-000001", preflight_only=False)
    try:
        assert result["executed"] is True
        snapshot = Path(result["run_snapshot"])
        run_raw = yaml.safe_load(snapshot.read_text(encoding="utf-8"))
        output_dir = Path(run_raw["paths"]["output_dir"])
        if not output_dir.is_absolute():
            output_dir = (PROJECT_ROOT / output_dir).resolve()
        manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["output_schema_version"] == "objective1_audit_v4"
        lineage = manifest["doe_lineage"]
        assert lineage["schema_version"] == "1.0"
        assert lineage["design_id"] == result["lineage"]["design_id"]
        assert lineage["sample_id"] == "sample-000001"
        assert lineage["run_id"] == result["run_id"]
        assert lineage["design_point_fingerprint_v1"] == result["lineage"]["design_point_fingerprint_v1"]
        assert lineage["dependencies"]["database"]["source"] == "local"
        assert lineage["dependencies"]["kinetics"]["enabled"] is True
    finally:
        snapshot_value = result.get("run_snapshot")
        if snapshot_value:
            shutil.rmtree(Path(snapshot_value).parent, ignore_errors=True)


def test_doe_lineage_file_is_optional_and_strict(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("BATCH_RUNNER_DOE_LINEAGE_FILE", raising=False)
    assert _load_doe_lineage() is None

    payload = {
        "schema_version": "1.0",
        "design_id": "doe-abc",
        "design_spec_hash_v1": "abc",
        "sample_id": "sample-000001",
        "design_point_fingerprint_v1": "def",
        "run_id": "run-1",
        "run_snapshot_sha256": "ghi",
        "batch_runner_source_sha256": "jkl",
        "code": {"git_commit": "abc123", "dirty": False},
        "software": {
            "python": "3.11",
            "reaktoro": "2.13.0",
            "numpy": "test",
            "scipy": "1.16.1",
        },
        "dependencies": {
            "database": {"source": "embedded", "name": "test"},
            "kinetics": {"enabled": False},
        },
    }
    path = tmp_path / "lineage.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("BATCH_RUNNER_DOE_LINEAGE_FILE", str(path))
    assert _load_doe_lineage() == payload

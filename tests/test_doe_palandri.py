from __future__ import annotations

import hashlib
import json
import shutil
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from batch_runner.config import load_case
from batch_runner.doe import generate_design, launch_sample, load_manifest, read_ledger
from batch_runner.doe.models import ExplicitValues, ParameterSpec, Target, UserDefinedProvenance
from batch_runner.doe.targets import canonicalize_sampling, resolve_target


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_CASE = PROJECT_ROOT / "tests" / "fixtures" / "cases" / "synthetic_kinec_case.yaml"
POKROVSKY_CASE = PROJECT_ROOT / "cases" / "pokrovsky_2005" / "pokrovsky_2005_2atm.yaml"
PALANDRI_DEFAULT = PROJECT_ROOT / "data" / "kinetics" / "PalandriKharaka_local.yaml"
POKROVSKY_PALANDRI = (
    PROJECT_ROOT / "data" / "kinetics" / "PalandriKharaka_pokrovsky_2005_weiss_calcite.yaml"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _provenance() -> dict:
    return {
        "kind": "user_defined",
        "justification": "software verification only",
        "applicability_domain": "DoE software verification",
        "distribution_rationale": "single deterministic verification value",
    }


def _pk_spec(base_case: Path, value_kj_mol: float) -> dict:
    return {
        "mode": "generated",
        "name": "palandri_primary_doe",
        "base_case": {"path": str(base_case), "sha256": _sha256(base_case)},
        "parameters": [
            {
                "parameter_id": "calcite_acid_E",
                "target": {
                    "kind": "pk_activation_energy",
                    "record": "Calcite",
                    "mechanism": "Acid",
                },
                "sampling": {
                    "kind": "explicit_values",
                    "values": [value_kj_mol],
                    "entered_unit": "kJ/mol",
                },
                "provenance": _provenance(),
            }
        ],
        "sampler": {"kind": "grid"},
        "constraints": [],
    }


def test_enabled_kinetics_defaults_to_palandri_and_doe_uses_that_default(tmp_path: Path) -> None:
    raw = yaml.safe_load(SYNTHETIC_CASE.read_text(encoding="utf-8"))
    raw["kinetics"] = {"enabled": True}
    base_case = tmp_path / "default-palandri-case.yaml"
    base_case.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    resolved = load_case(base_case, output_dir_override=tmp_path / "resolved-output")
    assert resolved.config.kinetics.model == "palandri_kharaka"
    assert resolved.kinetics_path == PALANDRI_DEFAULT.resolve()

    spec_path = tmp_path / "default-palandri-doe.yaml"
    spec_path.write_text(
        yaml.safe_dump(_pk_spec(base_case, 14.5), sort_keys=False), encoding="utf-8"
    )
    package = generate_design(spec_path, tmp_path / "default-palandri-design")
    package_root, manifest = load_manifest(package)
    ledger = read_ledger(package_root, manifest)

    assert manifest["generation_status"] == "ready"
    assert ledger[0]["outcome"] == "accepted"
    candidate = load_case(
        package_root / ledger[0]["case_path"],
        output_dir_override=tmp_path / "candidate-output",
        artifact_root=package_root,
    )
    assert candidate.config.kinetics.model == "palandri_kharaka"
    assert candidate.kinetics_path is not None
    assert package_root in candidate.kinetics_path.parents

    result = launch_sample(package, "sample-000001", preflight_only=True)
    try:
        assert result["preflight"]["ready"] is True
        assert result["lineage"]["dependencies"]["kinetics"]["model"] == "palandri_kharaka"
    finally:
        shutil.rmtree(Path(result["run_snapshot"]).parent, ignore_errors=True)


def test_palandri_activation_energy_rejects_negative_values() -> None:
    raw = yaml.safe_load(POKROVSKY_CASE.read_text(encoding="utf-8"))
    kinetics = yaml.safe_load(POKROVSKY_PALANDRI.read_text(encoding="utf-8"))
    target = Target(kind="pk_activation_energy", record="Calcite", mechanism="Acid")
    resolved = resolve_target(target, raw, kinetics)
    parameter = ParameterSpec(
        parameter_id="calcite_acid_E",
        target=target,
        sampling=ExplicitValues(
            kind="explicit_values", values=[-0.1], entered_unit="kJ/mol"
        ),
        provenance=UserDefinedProvenance(**_provenance()),
    )
    with pytest.raises(ValueError, match="requires value >= 0"):
        canonicalize_sampling(parameter, resolved, year_days=None)


def test_real_palandri_doe_generation_preflight_and_execution(tmp_path: Path) -> None:
    source_hash_before = _sha256(POKROVSKY_PALANDRI)
    source_kinetics = yaml.safe_load(POKROVSKY_PALANDRI.read_text(encoding="utf-8"))

    spec_path = tmp_path / "palandri-doe.yaml"
    spec_path.write_text(
        yaml.safe_dump(_pk_spec(POKROVSKY_CASE, 14.5), sort_keys=False), encoding="utf-8"
    )
    package = generate_design(spec_path, tmp_path / "palandri-design")
    package_root, manifest = load_manifest(package)
    ledger = read_ledger(package_root, manifest)

    assert manifest["generation_status"] == "ready"
    assert ledger[0]["outcome"] == "accepted"
    assert ledger[0]["sample_id"] == "sample-000001"
    assert ledger[0]["kinetics_path"]
    assert _sha256(POKROVSKY_PALANDRI) == source_hash_before

    generated_path = package_root / ledger[0]["kinetics_path"]
    generated_kinetics = yaml.safe_load(generated_path.read_text(encoding="utf-8"))
    expected = deepcopy(source_kinetics)
    expected["ReactionRateModelParams"]["PalandriKharaka"]["Calcite"]["Mechanisms"]["Acid"]["E"] = 14.5
    assert generated_kinetics == expected

    result = launch_sample(package, "sample-000001", preflight_only=False)
    try:
        assert result["executed"] is True
        assert result["preflight"]["ready"] is True
        assert result["lineage"]["dependencies"]["kinetics"]["model"] == "palandri_kharaka"

        snapshot = Path(result["run_snapshot"])
        run_raw = yaml.safe_load(snapshot.read_text(encoding="utf-8"))
        output_dir = Path(run_raw["paths"]["output_dir"])
        if not output_dir.is_absolute():
            output_dir = (PROJECT_ROOT / output_dir).resolve()
        output_manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
        assert output_manifest["output_schema_version"] == "objective1_audit_v4"
        assert output_manifest["doe_lineage"]["sample_id"] == "sample-000001"
        assert output_manifest["doe_lineage"]["dependencies"]["kinetics"]["model"] == "palandri_kharaka"
    finally:
        shutil.rmtree(Path(result["run_snapshot"]).parent, ignore_errors=True)

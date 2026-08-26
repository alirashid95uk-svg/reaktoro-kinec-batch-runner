from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

import batch_runner.config as config_api
from batch_runner.config import CaseConfig
from yaml_to_reaktoro import _validate_structure


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_CASE = (
    PROJECT_ROOT / "tests" / "fixtures" / "cases" / "synthetic_kinec_case.yaml"
)
SCHEMA_TEMPLATE = PROJECT_ROOT / "cases" / "schema_template.yaml"

REMOVED_SOLVER_BLOCKS = [
    ("backend", {"type": "standard"}),
    ("restart", {"enabled": False, "from_checkpoint": None}),
    ("safety", {}),
    ("conservation", {}),
    ("geochemical_controls", {}),
]


def _source_case() -> dict:
    with SOURCE_CASE.open(encoding="utf-8") as stream:
        data = yaml.safe_load(stream)
    assert isinstance(data, dict)
    return data


@pytest.mark.parametrize(("name", "value"), REMOVED_SOLVER_BLOCKS)
def test_removed_solver_placeholder_blocks_are_rejected_by_case_schema(
    name: str,
    value: dict,
) -> None:
    raw = _source_case()
    raw["solver"][name] = value

    with pytest.raises(ValidationError, match=name):
        CaseConfig.model_validate(raw)


@pytest.mark.parametrize(("name", "value"), REMOVED_SOLVER_BLOCKS)
def test_removed_solver_placeholder_blocks_are_rejected_by_standalone_generator(
    name: str,
    value: dict,
) -> None:
    raw = _source_case()
    raw["solver"][name] = value

    with pytest.raises(ValueError, match=name):
        _validate_structure(raw)


def test_restart_config_is_not_public_api() -> None:
    assert not hasattr(config_api, "RestartConfig")


def test_schema_template_has_no_removed_solver_placeholders() -> None:
    template = SCHEMA_TEMPLATE.read_text(encoding="utf-8")
    for name, _ in REMOVED_SOLVER_BLOCKS:
        assert f"  {name}:" not in template

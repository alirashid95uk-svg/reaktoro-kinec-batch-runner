from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

import batch_runner.config as config_api
from batch_runner.config import CaseConfig
from yaml_to_reaktoro import _validate_structure


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_CASE = PROJECT_ROOT / "cases" / "source_supported_kinetic_case.yaml"
SCHEMA_TEMPLATE = PROJECT_ROOT / "cases" / "schema_template.yaml"


def _source_case() -> dict:
    with SOURCE_CASE.open(encoding="utf-8") as stream:
        data = yaml.safe_load(stream)
    assert isinstance(data, dict)
    return data


def test_restart_block_is_rejected_by_case_schema() -> None:
    raw = _source_case()
    raw["solver"]["restart"] = {"enabled": False, "from_checkpoint": None}

    with pytest.raises(ValidationError, match="restart"):
        CaseConfig.model_validate(raw)


def test_restart_block_is_rejected_by_standalone_generator() -> None:
    raw = _source_case()
    raw["solver"]["restart"] = {"enabled": False, "from_checkpoint": None}

    with pytest.raises(ValueError, match="restart"):
        _validate_structure(raw)


def test_restart_config_is_not_public_api() -> None:
    assert not hasattr(config_api, "RestartConfig")


def test_schema_template_has_no_restart_placeholder() -> None:
    assert "restart:" not in SCHEMA_TEMPLATE.read_text(encoding="utf-8")

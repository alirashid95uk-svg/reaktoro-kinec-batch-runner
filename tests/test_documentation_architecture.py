from __future__ import annotations

import io
import json
from pathlib import Path

import griffe

from batch_runner.cli import build_run_parser, render_cli_markdown, run_config_help
from batch_runner.config import CaseConfig
from batch_runner.config.reference import (
    configuration_reference,
    render_markdown_reference,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STABLE_INTERFACES = {
    "batch_runner.config": (
        "CaseConfig",
        "ResolvedCase",
        "load_case",
        "resolve_case",
    ),
    "batch_runner.outputs": ("write_kinetic_mapping", "write_outputs"),
    "batch_runner.protocol": ("ProtocolEmitter", "cancellation_requested"),
    "batch_runner.simulator": (
        "execute_solver",
        "prepare_simulation",
        "preflight_case",
        "run_simulation",
        "uses_python_rate_callback",
    ),
}


def test_every_user_facing_config_field_has_a_description() -> None:
    missing = sorted(
        f"{option.model}.{option.path}"
        for option in configuration_reference().options
        if not option.description.strip()
    )
    assert not missing, "undocumented configuration fields:\n" + "\n".join(missing)


def test_case_config_json_schema_remains_serializable_and_described() -> None:
    schema = CaseConfig.model_json_schema()
    encoded = json.dumps(schema)

    assert encoded
    assert schema["additionalProperties"] is False
    assert "outputs" not in schema["properties"]
    assert {"postprocessing", "plots", "monitor", "debug"}.issubset(schema["properties"])
    assert schema["properties"]["solver"]["description"]
    assert schema["$defs"]["SolverConfig"]["properties"]["timestep"]["discriminator"] == {
        "mapping": {
            "adaptive": "#/$defs/AdaptiveTimestepConfig",
            "adaptive_error_controlled": "#/$defs/AdaptiveErrorControlledTimestepConfig",
            "fixed": "#/$defs/FixedTimestepConfig",
        },
        "propertyName": "mode",
    }


def test_stable_batch_runner_interfaces_have_docstrings() -> None:
    missing = []
    for module_name, names in STABLE_INTERFACES.items():
        module = griffe.load(
            module_name,
            search_paths=[PROJECT_ROOT],
            allow_inspection=False,
        )
        missing.extend(
            f"{module_name}.{name}"
            for name in names
            if not module[name].docstring or not module[name].docstring.value.strip()
        )

    assert not missing, "stable interfaces without docstrings: " + ", ".join(missing)


def test_config_help_exposes_representative_sections_and_paths() -> None:
    stream = io.StringIO()
    errors = io.StringIO()

    assert run_config_help(["--help", "timestep"], stream=stream, error_stream=errors) == 0
    output = stream.getvalue()
    assert "solver.timestep.mode" in output
    assert "adaptive_error_controlled" in output
    assert "Conditional and cross-field rules:" in output
    assert not errors.getvalue()

    stream = io.StringIO()
    assert run_config_help(["kinetics.model"], stream=stream, error_stream=errors) == 0
    assert "kinetics.model" in stream.getvalue()
    assert "palandri_kharaka" in stream.getvalue()

    stream = io.StringIO()
    assert run_config_help(["plots"], stream=stream, error_stream=errors) == 0
    assert "plots.enabled" in stream.getvalue()
    assert "plots.pH" in stream.getvalue()


def test_unknown_config_help_path_fails_without_loading_a_case() -> None:
    stream = io.StringIO()
    errors = io.StringIO()

    assert run_config_help(["does_not_exist"], stream=stream, error_stream=errors) == 2
    assert not stream.getvalue()
    assert "unknown configuration section or path" in errors.getvalue()


def test_generated_reference_views_use_live_definitions() -> None:
    config_page = render_markdown_reference()
    cli_page = render_cli_markdown()

    assert "This page is generated from `CaseConfig`" in config_page
    assert "`solver.timestep.mode`" in config_page
    assert build_run_parser().format_usage().strip() in cli_page

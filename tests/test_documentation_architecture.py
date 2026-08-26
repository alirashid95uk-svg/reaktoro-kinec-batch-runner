from __future__ import annotations

import inspect
import io
import json

from batch_runner.cli import build_run_parser, render_cli_markdown, run_config_help
from batch_runner.config import CaseConfig, ResolvedCase, load_case, resolve_case
from batch_runner.config.reference import (
    configuration_reference,
    render_markdown_reference,
)
from batch_runner.outputs import write_kinetic_mapping, write_outputs
from batch_runner.protocol import ProtocolEmitter, cancellation_requested
from batch_runner.simulator import (
    execute_solver,
    prepare_simulation,
    preflight_case,
    run_simulation,
    uses_python_rate_callback,
)
from runner import main as runner_main


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
    interfaces = (
        CaseConfig,
        ResolvedCase,
        load_case,
        resolve_case,
        write_kinetic_mapping,
        write_outputs,
        ProtocolEmitter,
        cancellation_requested,
        execute_solver,
        prepare_simulation,
        preflight_case,
        run_simulation,
        uses_python_rate_callback,
    )

    missing = [item.__qualname__ for item in interfaces if not inspect.getdoc(item)]
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


def test_unknown_config_help_path_fails_without_loading_a_case() -> None:
    stream = io.StringIO()
    errors = io.StringIO()

    assert run_config_help(["does_not_exist"], stream=stream, error_stream=errors) == 2
    assert not stream.getvalue()
    assert "unknown configuration section or path" in errors.getvalue()


def test_runner_dispatches_config_help_without_a_case(capsys) -> None:
    runner_main(["config", "--help", "timestep"])

    assert "solver.timestep.mode" in capsys.readouterr().out


def test_generated_reference_views_use_live_definitions() -> None:
    config_page = render_markdown_reference()
    cli_page = render_cli_markdown()

    assert "This page is generated from `CaseConfig`" in config_page
    assert "`solver.timestep.mode`" in config_page
    assert build_run_parser().format_usage().strip() in cli_page

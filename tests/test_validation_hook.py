import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError

import batch_runner.config as config_api
from batch_runner.config import CaseConfig, load_case
from batch_runner.protocol import ProtocolEmitter
from runner import _run_validation_hook


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_CASE = PROJECT_ROOT / "cases" / "jayasekara_2020_reproduction_monitor.yaml"
VALIDATION_SCRIPT = PROJECT_ROOT / "validation" / "jayasekara_comparison_figures.py"


def _raw_case(tmp_path: Path) -> dict:
    raw = yaml.safe_load(SOURCE_CASE.read_text(encoding="utf-8"))
    raw["paths"]["output_dir"] = str(tmp_path / "results")
    return raw


def _write_case(tmp_path: Path, raw: dict) -> Path:
    path = tmp_path / "case.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


def _hook_case(script: Path, *, enabled: bool = True):
    return SimpleNamespace(
        config=SimpleNamespace(validation=SimpleNamespace(enabled=enabled)),
        validation_script_path=script,
    )


def _emitter(stream: io.StringIO) -> ProtocolEmitter:
    return ProtocolEmitter(enabled=True, run_id="run-1", case_id="case-1", stream=stream)


def test_validation_schema_and_script_path_resolution(tmp_path: Path) -> None:
    raw = _raw_case(tmp_path)
    raw["validation"] = {
        "enabled": True,
        "script": "validation/jayasekara_comparison_figures.py",
    }
    resolved = load_case(_write_case(tmp_path, raw))
    assert resolved.validation_script_path == VALIDATION_SCRIPT.resolve()
    assert resolved.as_dict()["validation"]["script"] == str(VALIDATION_SCRIPT.resolve())

    raw["validation"] = {"enabled": True}
    with pytest.raises(ValidationError, match="enabled validation requires script"):
        CaseConfig.model_validate(raw)

    raw["validation"] = {"enabled": False, "script": "validation/example.py"}
    with pytest.raises(ValidationError, match="disabled validation forbids script"):
        CaseConfig.model_validate(raw)

    raw["validation"] = {"enabled": False, "targets": []}
    with pytest.raises(ValidationError, match="targets"):
        CaseConfig.model_validate(raw)

    raw["validation"] = {"enabled": False}
    raw["postprocessing"]["validation_ledger"] = False
    with pytest.raises(ValidationError, match="validation_ledger"):
        CaseConfig.model_validate(raw)
    assert not hasattr(config_api, "ValidationTarget")


@pytest.mark.parametrize(
    ("script", "message"),
    [
        ("runner.py", "inside the project validation directory"),
        ("validation/not-python.txt", "must use a .py path"),
        ("validation/missing.py", "validation script does not exist"),
    ],
)
def test_validation_script_path_rejections(
    tmp_path: Path, script: str, message: str
) -> None:
    raw = _raw_case(tmp_path)
    raw["validation"] = {"enabled": True, "script": script}
    with pytest.raises((ValueError, FileNotFoundError), match=message):
        load_case(_write_case(tmp_path, raw))


def test_successful_validation_hook_preserves_manifest_and_emits_status(
    tmp_path: Path, capsys
) -> None:
    run_dir = tmp_path / "timestamped-run"
    results = run_dir / "results"
    results.mkdir(parents=True)
    manifest = results / "manifest.json"
    manifest.write_bytes(b'{"authoritative":true}\n')
    before = manifest.read_bytes()
    script = tmp_path / "success.py"
    script.write_text(
        """from argparse import ArgumentParser
from pathlib import Path
p = ArgumentParser()
p.add_argument('--results-dir', type=Path, required=True)
results = p.parse_args().results_dir
out = results.parent / 'validation'
out.mkdir()
(out / 'done.txt').write_text(str(results), encoding='utf-8')
print('validation child completed')
""",
        encoding="utf-8",
    )
    stream = io.StringIO()

    status = _run_validation_hook(
        _hook_case(script), results, _emitter(stream), completed=True
    )

    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert status == "completed"
    assert [event["event_type"] for event in events] == [
        "stage_started",
        "stage_completed",
    ]
    assert all(event["payload"]["stage"] == "post_simulation_validation" for event in events)
    assert events[-1]["payload"]["status"] == "completed"
    assert events[-1]["payload"]["results_dir"] == str(results)
    assert (run_dir / "validation" / "done.txt").read_text(encoding="utf-8") == str(results)
    assert manifest.read_bytes() == before
    assert "Validation completed." in capsys.readouterr().out


def test_failed_validation_hook_is_not_a_simulation_failure(
    tmp_path: Path, capsys
) -> None:
    results = tmp_path / "timestamped-run" / "results"
    results.mkdir(parents=True)
    manifest = results / "manifest.json"
    manifest.write_bytes(b'{"authoritative":true}\n')
    before = manifest.read_bytes()
    script = tmp_path / "failure.py"
    script.write_text(
        "import sys\nprint('comparison failed', file=sys.stderr)\nraise SystemExit(7)\n",
        encoding="utf-8",
    )
    stream = io.StringIO()

    status = _run_validation_hook(
        _hook_case(script), results, _emitter(stream), completed=True
    )

    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert status == "failed"
    assert events[-1]["event_type"] == "stage_completed"
    assert events[-1]["payload"]["status"] == "failed"
    assert events[-1]["payload"]["exit_code"] == 7
    assert all(event["event_type"] != "worker_failure_reported" for event in events)
    assert manifest.read_bytes() == before
    captured = capsys.readouterr()
    assert "Validation FAILED" in captured.err
    assert "Simulation results remain valid" in captured.err


def test_validation_hook_is_skipped_for_incomplete_package(
    tmp_path: Path, capsys
) -> None:
    results = tmp_path / "timestamped-run" / "results"
    results.mkdir(parents=True)
    marker = tmp_path / "must-not-run"
    script = tmp_path / "must_not_run.py"
    script.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n",
        encoding="utf-8",
    )
    stream = io.StringIO()

    status = _run_validation_hook(
        _hook_case(script), results, _emitter(stream), completed=False
    )

    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert status == "skipped"
    assert not marker.exists()
    assert [event["event_type"] for event in events] == ["stage_completed"]
    assert events[0]["payload"]["status"] == "skipped"
    assert "Validation skipped" in capsys.readouterr().out

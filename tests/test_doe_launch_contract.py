from __future__ import annotations

import pytest

import batch_runner.doe.launch as launch_module


def test_run_id_is_created_before_manifest_verification(monkeypatch) -> None:
    calls: list[str] = []

    def fake_uuid4() -> str:
        calls.append("uuid4")
        return "run-test"

    def fail_manifest(_path):
        calls.append("load_manifest")
        raise ValueError("verification failure")

    monkeypatch.setattr(launch_module, "uuid4", fake_uuid4)
    monkeypatch.setattr(launch_module, "load_manifest", fail_manifest)

    with pytest.raises(ValueError, match="verification failure"):
        launch_module.launch_sample(
            "missing-design", "sample-000001", preflight_only=True
        )

    assert calls == ["uuid4", "load_manifest"]

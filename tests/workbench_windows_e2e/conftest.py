from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_CASE = PROJECT_ROOT / "cases" / "source_supported_kinetic_case.yaml"
SOURCE_RESULT = (
    PROJECT_ROOT
    / "runs"
    / "source_supported_kinetic_case"
    / "902f0b41-ff37-42c2-ac8b-6ecd438017ac"
)


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers", "windows_uia: native Windows UI Automation acceptance test"
    )


@pytest.fixture(scope="session", autouse=True)
def require_native_windows() -> None:
    if sys.platform != "win32":
        pytest.skip("Windows UI Automation is available only on Windows")
    if os.environ.get("RUN_WORKBENCH_WINDOWS_UIA") != "1":
        pytest.skip("set RUN_WORKBENCH_WINDOWS_UIA=1 to run native desktop acceptance")
    os.environ.pop("QT_QPA_PLATFORM", None)
    os.environ["QT_QPA_PLATFORM"] = "windows"
    os.environ["QT_ACCESSIBILITY"] = "1"


@pytest.fixture()
def isolated_project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "cases").mkdir(parents=True)
    (root / "runs").mkdir()
    shutil.copy2(SOURCE_CASE, root / "cases" / SOURCE_CASE.name)
    if SOURCE_RESULT.is_dir():
        shutil.copytree(
            SOURCE_RESULT,
            root / "runs" / "source_supported" / SOURCE_RESULT.name,
        )
        from workbench_core.run_index import rebuild_index

        rebuild_index(root / ".workbench" / "run_index.sqlite", root / "runs")
    return root


@pytest.fixture()
def workbench_process(isolated_project: Path, tmp_path: Path):
    from pywinauto import Application

    env = os.environ.copy()
    env.pop("QT_QPA_PLATFORM", None)
    env.update(
        QT_QPA_PLATFORM="windows",
        QT_ACCESSIBILITY="1",
        APPDATA=str(tmp_path / "appdata"),
        LOCALAPPDATA=str(tmp_path / "localappdata"),
        TEMP=str(tmp_path / "temp"),
        TMP=str(tmp_path / "temp"),
    )
    for name in ("APPDATA", "LOCALAPPDATA", "TEMP"):
        Path(env[name]).mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "workbench",
            "--project-root",
            str(isolated_project),
            "--solver-prefix",
            str(Path(sys.executable).parents[1] / "fypr-reaktoro"),
        ],
        cwd=PROJECT_ROOT,
        env=env,
    )
    app = Application(backend="uia").connect(process=process.pid, timeout=30)
    window = app.window(title="Reaktoro Scientific Workbench")
    window.wait("visible enabled ready", timeout=30)
    yield process, window
    if process.poll() is None:
        try:
            window.close()
            process.wait(timeout=10)
        except Exception:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()

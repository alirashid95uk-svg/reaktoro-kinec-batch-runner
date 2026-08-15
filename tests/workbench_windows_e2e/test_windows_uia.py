from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pywinauto = pytest.importorskip("pywinauto")
Desktop = pywinauto.Desktop
from pywinauto.keyboard import send_keys
from pywinauto.timings import wait_until


PROJECT_ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.windows_uia


def _by_id(window, identifier: str):
    control = window.child_window(auto_id=identifier)
    control.wait("exists", timeout=10)
    return control.wrapper_object()


def _current_focus(window):
    for control in window.descendants():
        try:
            if control.element_info.element.CurrentHasKeyboardFocus:
                return control
        except Exception:
            continue
    return None


def test_ctrl_1_to_ctrl_7_updates_accessible_workspace_identity(workbench_process) -> None:
    _process, window = workbench_process
    navigation = _by_id(window, "primaryNavigation")
    for index, expected in enumerate(
        ("Home", "Cases", "Queue", "Runs", "Explore", "Compare", "Studies"), 1
    ):
        navigation.set_focus()
        send_keys(f"^{index}", vk_packet=False)
        wait_until(5, 0.05, lambda: _by_id(window, "pageTitle").window_text(), expected)
        assert _by_id(window, "pageTitle").window_text() == expected


def test_native_focus_skips_hidden_and_disabled_actions(workbench_process) -> None:
    _process, window = workbench_process
    window.set_focus()
    send_keys("^2", vk_packet=False)
    forbidden = {"casesSave", "casesValidate", "casesPrepare"}
    observed: set[str] = set()
    for _ in range(18):
        send_keys("{TAB}")
        focused = _current_focus(window)
        assert focused is not None
        assert focused.is_visible() and focused.is_enabled()
        observed.add(focused.element_info.automation_id)
    assert not observed.intersection(forbidden)


def test_uia_exposes_identity_role_state_and_helptext(workbench_process) -> None:
    _process, window = workbench_process
    groups = (
        (1, ("primaryNavigation", "pageTitle", "operationStatus")),
        (2, ("casesSearch", "casesOpen")),
        (3, ("queueStart",)),
        (4, ("runsSearch",)),
        (6, ("compareAdd",)),
        (7, ("studiesOpenSpec",)),
    )
    navigation = _by_id(window, "primaryNavigation")
    for page, identifiers in groups:
        navigation.set_focus()
        send_keys(f"^{page}", vk_packet=False)
        for identifier in identifiers:
            control = _by_id(window, identifier)
            info = control.element_info
            assert info.automation_id == identifier
            assert info.name
            assert info.control_type
            assert isinstance(control.is_enabled(), bool)
            assert info.element.CurrentIsKeyboardFocusable in (0, 1)
            assert info.element.CurrentHelpText is not None


def test_runs_can_open_explore_with_exact_table_alternative(workbench_process) -> None:
    _process, window = workbench_process
    _by_id(window, "primaryNavigation").set_focus()
    send_keys("^4", vk_packet=False)
    table = _by_id(window, "runsTable")
    assert table.is_visible()
    table.set_focus()
    send_keys("{HOME}{ENTER}", vk_packet=False)
    wait_until(10, 0.05, lambda: _by_id(window, "pageTitle").window_text(), "Explore")
    assert _by_id(window, "pageTitle").window_text() == "Explore"
    tabs = _by_id(window, "exploreTabs")
    exact_tab = next(
        item
        for item in tabs.descendants(control_type="TabItem")
        if item.window_text() == "Data table"
    )
    exact_tab.select()
    exact = _by_id(window, "exploreExactData")
    assert exact.is_visible() and exact.is_enabled()


@pytest.mark.skipif(
    os.environ.get("RUN_WORKBENCH_LAUNCHER_UIA") != "1",
    reason="set RUN_WORKBENCH_LAUNCHER_UIA=1 for the launcher/splash acceptance",
)
def test_cmd_launcher_shows_splash_before_main_window() -> None:
    env = os.environ.copy()
    env.update(QT_ACCESSIBILITY="1", QT_QPA_PLATFORM="windows")
    process = subprocess.Popen(
        ["cmd.exe", "/c", str(PROJECT_ROOT / "Run Workbench.cmd")],
        cwd=PROJECT_ROOT,
        env=env,
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )
    desktop = Desktop(backend="uia")
    win32_desktop = Desktop(backend="win32")
    splash_seen = False
    main_seen = False
    seen: set[tuple[str, str, str]] = set()

    def inspect_windows() -> bool:
        nonlocal splash_seen, main_seen
        for candidate in desktop.windows():
            auto_id = candidate.element_info.automation_id
            title = candidate.window_text()
            seen.add((auto_id, title, candidate.element_info.control_type))
            splash_seen = splash_seen or auto_id == "workbenchSplash" or title in {
                "Reaktoro Scientific Workbench - Starting",
                "Workbench startup status",
            }
            main_seen = main_seen or title == "Reaktoro Scientific Workbench"
        splash_seen = splash_seen or any(
            candidate.window_text() == "Reaktoro Scientific Workbench - Starting"
            for candidate in win32_desktop.windows()
        )
        return main_seen

    try:
        wait_until(45, 0.025, inspect_windows, True)
        assert splash_seen, sorted(
            entry for entry in seen if entry[0] or "Reaktoro" in entry[1] or "Workbench" in entry[1]
        )
        assert main_seen
        main = desktop.window(title="Reaktoro Scientific Workbench")
        operation = main.child_window(auto_id="operationStatus").wrapper_object()
        wait_until(
            30,
            0.1,
            lambda: any(
                item.window_text().startswith(("Ready", "Attention"))
                for item in operation.descendants(control_type="Text")
            ),
            True,
        )
        main.set_focus()
        send_keys("%{F4}", vk_packet=False)
        process.wait(timeout=20)
    finally:
        if process.poll() is None:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                capture_output=True,
            )

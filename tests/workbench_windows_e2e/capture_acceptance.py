"""Generate retained native-Qt UX evidence; this is not scientific evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
OUTPUT_ROOT = PROJECT_ROOT / ".workbench" / "acceptance" / "ux"
PAGE_IDS = ("home", "cases", "queue", "runs", "explore", "compare", "studies")
SIZES = ((1024, 600), (1440, 900))
SCALES = ("1.00", "1.25", "1.50")
STATES = ("empty", "representative")
EVIDENCE_SOURCE_PATHS = (
    "workbench",
    "workbench_core",
    "tests/workbench_gui",
    "tests/workbench_windows_e2e",
    "docs/workbench",
    "Run Workbench.cmd",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(*arguments: str) -> str:
    return subprocess.check_output(
        ["git", *arguments], cwd=PROJECT_ROOT, text=True, stderr=subprocess.DEVNULL
    ).strip()


def _relevant_tree_identity() -> tuple[str, int]:
    files: list[Path] = []
    for relative in EVIDENCE_SOURCE_PATHS:
        path = PROJECT_ROOT / relative
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(
                candidate
                for candidate in path.rglob("*")
                if candidate.is_file()
                and "__pycache__" not in candidate.parts
                and candidate.suffix != ".pyc"
            )
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda item: item.relative_to(PROJECT_ROOT).as_posix()):
        relative = path.relative_to(PROJECT_ROOT).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(bytes.fromhex(_sha256(path)))
    return digest.hexdigest(), len(files)


def _capture_worker(arguments: argparse.Namespace) -> int:
    from PySide6 import QtCore, __version__ as pyside_version
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QPushButton, QScrollArea
    import pyqtgraph

    from workbench.app import create_application
    from workbench.main_window import MainWindow, PAGE_NAMES

    temporary: tempfile.TemporaryDirectory[str] | None = None
    if arguments.state == "empty":
        temporary = tempfile.TemporaryDirectory(prefix="workbench-ux-empty-")
        root = Path(temporary.name)
        (root / "cases").mkdir()
        (root / "runs").mkdir()
    else:
        root = PROJECT_ROOT

    app = create_application([])
    solver = Path(sys.executable).parents[1] / "fypr-reaktoro"
    window = MainWindow(root, solver)
    if arguments.state == "representative":
        _populate(window)
    window.show()
    QApplication.processEvents()
    window.setFixedSize(arguments.width, arguments.height)
    QApplication.processEvents()

    records: list[dict[str, object]] = []
    for index, page_id in enumerate(PAGE_IDS):
        window.navigation.setCurrentRow(index)
        page = window.pages.currentWidget()
        page.setFocus(Qt.FocusReason.OtherFocusReason)
        if QApplication.focusWidget() is page:
            page.focusNextChild()
        QApplication.processEvents()
        screenshot = (
            OUTPUT_ROOT
            / "screenshots"
            / arguments.state
            / f"{arguments.width}x{arguments.height}"
            / arguments.scale
            / f"{page_id}.png"
        )
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        pixmap = window.grab()
        if not pixmap.save(str(screenshot), "PNG"):
            raise RuntimeError(f"could not save {screenshot}")

        failures: list[str] = []
        if (window.width(), window.height()) != (arguments.width, arguments.height):
            failures.append(
                f"requested logical window {arguments.width}x{arguments.height}, "
                f"actual {window.width()}x{window.height()}"
            )
        visible_buttons = [
            button
            for button in page.findChildren(QPushButton)
            if button.isVisible() and not button.visibleRegion().isEmpty()
        ]
        for button in visible_buttons:
            if not button.visibleRegion().boundingRect().contains(button.rect()):
                failures.append(f"visible action partially obscured: {button.text()}")
            required_width = button.fontMetrics().horizontalAdvance(button.text()) + 24
            if button.width() < required_width:
                failures.append(
                    f"visible action text clipped: {button.text()} "
                    f"({button.width()} < {required_width})"
                )
            top_left = button.mapTo(page, button.rect().topLeft())
            bottom_right = button.mapTo(page, button.rect().bottomRight())
            if not page.rect().contains(top_left) or not page.rect().contains(bottom_right):
                failures.append(f"visible action clipped: {button.text()}")
        for button_index, button in enumerate(visible_buttons):
            button_rect = button.rect().translated(button.mapTo(page, button.rect().topLeft()))
            for other in visible_buttons[button_index + 1 :]:
                other_rect = other.rect().translated(other.mapTo(page, other.rect().topLeft()))
                if button_rect.intersects(other_rect):
                    failures.append(
                        f"visible actions overlap: {button.text()} / {other.text()}"
                    )
        root_scroll = any(
            scroll.horizontalScrollBar().isVisible()
            for scroll in page.children()
            if isinstance(scroll, QScrollArea)
        )
        if root_scroll:
            failures.append("root-level horizontal scrollbar visible")
        focus = QApplication.focusWidget()
        focus_record = {
            "identifier": focus.accessibleIdentifier() if focus else "",
            "name": focus.accessibleName() if focus else "",
            "class": type(focus).__name__ if focus else "",
            "visible": bool(focus and focus.isVisible()),
            "enabled": bool(focus and focus.isEnabled()),
        }
        if not focus_record["visible"] or not focus_record["enabled"]:
            failures.append("initial focus is not visible and enabled")
        relative = screenshot.relative_to(OUTPUT_ROOT).as_posix()
        records.append(
            {
                "page": PAGE_NAMES[index],
                "page_id": page_id,
                "state_id": arguments.state,
                "fixture_identity": "UX test fixture - not scientific evidence",
                "requested_logical_size": [arguments.width, arguments.height],
                "qt_scale_factor": arguments.scale,
                "actual_window_geometry": [window.width(), window.height()],
                "actual_client_geometry": [window.centralWidget().width(), window.centralWidget().height()],
                "device_pixel_ratio": pixmap.devicePixelRatio(),
                "screenshot_pixel_dimensions": [pixmap.width(), pixmap.height()],
                "screenshot_path": relative,
                "screenshot_bytes": screenshot.stat().st_size,
                "screenshot_sha256": _sha256(screenshot),
                "focus_target": focus_record,
                "root_horizontal_scroll": root_scroll,
                "automated_failures": failures,
                "manual_review": "pending",
            }
        )
    fragment = OUTPUT_ROOT / "fragments" / (
        f"{arguments.state}-{arguments.width}x{arguments.height}-{arguments.scale}.json"
    )
    fragment.parent.mkdir(parents=True, exist_ok=True)
    fragment.write_text(
        json.dumps(
            {
                "pyside6": pyside_version,
                "qt": QtCore.qVersion(),
                "pyqtgraph": pyqtgraph.__version__,
                "records": records,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    window.close()
    app.processEvents()
    if temporary is not None:
        temporary.cleanup()
    return 1 if any(record["automated_failures"] for record in records) else 0


def _populate(window) -> None:
    case = PROJECT_ROOT / "cases" / "source_supported_kinetic_case.yaml"
    window.cases.editor.load_path(case)
    window.cases.editor_stack.setCurrentWidget(window.cases.editor)
    window.cases._refresh_actions()

    source_run = (
        PROJECT_ROOT
        / "runs"
        / "source_supported_kinetic_case"
        / "902f0b41-ff37-42c2-ac8b-6ecd438017ac"
    )
    snapshot = source_run / "run_case.yaml"
    if snapshot.is_file() and window.queue.table.rowCount() == 0:
        window.queue.add_prepared_run(
            {
                "state": "ready",
                "run_id": "ux-fixture-queued",
                "snapshot_path": str(snapshot),
                "validation_receipt_path": str(source_run / "validation_receipt.json"),
            }
        )
    window.runs.refresh()
    if window.runs.table.rowCount():
        window.runs.table.selectRow(0)

    results = source_run / "results"
    if results.is_dir():
        window.explore.load_package(results)

    comparison = PROJECT_ROOT / ".workbench" / "acceptance" / "comparison"
    if (comparison / "comparison_spec.json").is_file():
        window.compare.set_saved_artifacts(
            comparison / "comparison_spec.json", comparison / "comparison.csv"
        )

    study = PROJECT_ROOT / ".workbench" / "acceptance" / "study" / "cases" / "study_manifest.json"
    dataset = PROJECT_ROOT / ".workbench" / "acceptance" / "dataset" / "dataset_manifest.json"
    if study.is_file():
        window.studies.load_manifest(study)
        window.studies.study_tabs.setCurrentIndex(2)
    if dataset.is_file():
        window.studies.load_dataset_manifest(dataset)


def _build_manifest() -> int:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    fragments: list[dict[str, object]] = []
    worker_failures: list[str] = []
    for state in STATES:
        for width, height in SIZES:
            for scale in SCALES:
                env = os.environ.copy()
                env.update(
                    QT_QPA_PLATFORM="windows",
                    QT_ACCESSIBILITY="1",
                    QT_SCALE_FACTOR=scale,
                )
                command = [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--worker",
                    "--state",
                    state,
                    "--width",
                    str(width),
                    "--height",
                    str(height),
                    "--scale",
                    scale,
                ]
                completed = subprocess.run(command, cwd=PROJECT_ROOT, env=env, check=False)
                fragment_path = OUTPUT_ROOT / "fragments" / f"{state}-{width}x{height}-{scale}.json"
                if not fragment_path.is_file():
                    worker_failures.append("missing fragment: " + fragment_path.name)
                    continue
                fragments.append(json.loads(fragment_path.read_text(encoding="utf-8")))
                if completed.returncode:
                    worker_failures.append("worker checks failed: " + fragment_path.name)

    records = [record for fragment in fragments for record in fragment["records"]]
    expected = len(STATES) * len(SIZES) * len(SCALES) * len(PAGE_IDS)
    if len(records) != expected:
        worker_failures.append(f"expected {expected} records, found {len(records)}")
    relevant_tree_sha256, relevant_file_count = _relevant_tree_identity()
    manifest = {
        "manifest_schema_version": "1.1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Native Qt factor rendering and automated geometry; not physical multi-monitor DPI or scientific evidence",
        "git_commit": _git("rev-parse", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain")),
        "relevant_tree_sha256": relevant_tree_sha256,
        "relevant_file_count": relevant_file_count,
        "windows_build": platform.platform(),
        "python": platform.python_version(),
        "pyside6": fragments[0]["pyside6"] if fragments else None,
        "qt": fragments[0]["qt"] if fragments else None,
        "pyqtgraph": fragments[0]["pyqtgraph"] if fragments else None,
        "expected_record_count": expected,
        "records": records,
        "generator_failures": worker_failures,
    }
    (OUTPUT_ROOT / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    cards = "\n".join(
        f'<figure><img src="{record["screenshot_path"]}" alt="{record["page"]} {record["state_id"]} at {record["requested_logical_size"]}"><figcaption>{record["page"]} - {record["state_id"]} - {record["requested_logical_size"]} - scale {record["qt_scale_factor"]}</figcaption></figure>'
        for record in records
    )
    (OUTPUT_ROOT / "contact-sheet.html").write_text(
        "<!doctype html><meta charset='utf-8'><title>Workbench UX evidence</title>"
        "<style>body{font:14px Segoe UI;background:#f4f7fa}figure{background:white;padding:12px;border:1px solid #ccd6e2}img{max-width:100%;height:auto}</style>"
        + cards,
        encoding="utf-8",
    )
    print(json.dumps({"records": len(records), "failures": worker_failures}, indent=2))
    return 1 if worker_failures or any(record["automated_failures"] for record in records) else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--state", choices=STATES)
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument("--scale")
    arguments = parser.parse_args()
    if arguments.worker:
        return _capture_worker(arguments)
    return _build_manifest()


if __name__ == "__main__":
    raise SystemExit(main())

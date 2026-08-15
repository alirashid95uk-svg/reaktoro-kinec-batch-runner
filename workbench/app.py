from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox, QSplashScreen

from workbench.theme import apply_theme


QApplication.setHighDpiScaleFactorRoundingPolicy(
    Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
)


def create_application(argv: list[str] | None = None) -> QApplication:
    app = QApplication.instance() or QApplication(argv or sys.argv)
    app.setApplicationName("Reaktoro Scientific Workbench")
    app.setOrganizationName("Reaktoro Batch Runner")
    apply_theme(app)
    return app


def create_splash() -> QSplashScreen:
    canvas = QPixmap(520, 180)
    canvas.fill(QColor("#f4f7fa"))
    painter = QPainter(canvas)
    painter.fillRect(0, 0, 7, canvas.height(), QColor("#2563eb"))
    painter.setPen(QColor("#10213d"))
    painter.setFont(QFont("Segoe UI", 18, QFont.Weight.DemiBold))
    painter.drawText(34, 58, "Reaktoro Scientific Workbench")
    painter.setPen(QColor("#52647a"))
    painter.setFont(QFont("Segoe UI", 10))
    painter.drawText(35, 91, "Preparing the scientific orchestration interface")
    painter.drawText(35, 120, "The verified solver environment remains separate.")
    painter.end()
    splash = QSplashScreen(
        canvas,
        Qt.WindowType.Window
        | Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.WindowStaysOnTopHint,
    )
    splash.setWindowTitle("Reaktoro Scientific Workbench - Starting")
    splash.setObjectName("workbenchSplash")
    splash.setAccessibleIdentifier("workbenchSplash")
    splash.setAccessibleName("Workbench startup status")
    return splash


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Open the Reaktoro Scientific Workbench")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--solver-prefix",
        type=Path,
        default=None,
    )
    arguments = parser.parse_args(argv)
    app = create_application()
    splash = create_splash()
    splash_started = time.monotonic()
    splash.show()
    splash.showMessage(
        "Loading workbench components…",
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
        QColor("#315d9b"),
    )
    splash.setAccessibleDescription("Loading workbench components")
    app.processEvents()
    try:
        from workbench.main_window import MainWindow
        from workbench_core.operations import ProjectControlLock
        from workbench_core.persistence import atomic_write_json
    except Exception as error:
        splash.close()
        QMessageBox.critical(
            None,
            "Workbench startup failed",
            f"The workbench could not load its components.\n\n{type(error).__name__}: {error}",
        )
        return 1
    state_dir = arguments.project_root.resolve() / ".workbench"
    state_dir.mkdir(parents=True, exist_ok=True)
    settings_path = state_dir / "settings.json"
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        settings = {}
    solver_prefix = arguments.solver_prefix or (
        Path(os.environ["REAKTORO_SOLVER_PREFIX"])
        if os.environ.get("REAKTORO_SOLVER_PREFIX")
        else Path(settings["solver_environment_path"])
        if settings.get("solver_environment_path")
        else Path("__solver_environment_not_configured__")
    )
    queue_lock = ProjectControlLock(arguments.project_root)
    try:
        queue_lock.__enter__()
    except RuntimeError:
        splash.close()
        QMessageBox.critical(
            None,
            "Workbench already controls this project",
            "Another workbench instance owns the queue-control lock. No second controller was started.",
        )
        return 2
    previous_token = os.environ.get("REAKTORO_PROJECT_CONTROL_TOKEN")
    os.environ["REAKTORO_PROJECT_CONTROL_TOKEN"] = queue_lock.token
    try:
        atomic_write_json(
            settings_path,
            {
                "settings_schema_version": "1.0",
                "solver_environment_path": str(solver_prefix.resolve()),
                "launch_command_form": "conda run --no-capture-output -p <solver_environment_path> python",
            },
        )
        splash.showMessage(
            "Building the seven scientific workspaces…",
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
            QColor("#315d9b"),
        )
        splash.setAccessibleDescription("Building the seven scientific workspaces")
        app.processEvents()
        try:
            window = MainWindow(arguments.project_root, solver_prefix)
        except Exception as error:
            splash.close()
            QMessageBox.critical(
                None,
                "Workbench startup failed",
                f"The main window could not be constructed.\n\n{type(error).__name__}: {error}",
            )
            return 1
        def reveal_main_window() -> None:
            window.show()
            splash.finish(window)
            QTimer.singleShot(0, window.startup_recovery)

        remaining_ms = max(0, 1_200 - round((time.monotonic() - splash_started) * 1_000))
        QTimer.singleShot(remaining_ms, reveal_main_window)
        return app.exec()
    finally:
        queue_lock.__exit__(None, None, None)
        if previous_token is None:
            os.environ.pop("REAKTORO_PROJECT_CONTROL_TOKEN", None)
        else:
            os.environ["REAKTORO_PROJECT_CONTROL_TOKEN"] = previous_token

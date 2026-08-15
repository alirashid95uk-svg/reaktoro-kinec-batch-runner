"""Native Qt presentation settings for the scientific workbench."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory


LIGHT_STYLE = """
QWidget {
    color: #172033;
    font-family: "Segoe UI";
    font-size: 10.5pt;
}
QMainWindow, QWidget#workbenchRoot, QStackedWidget#workspaceStack {
    background: #f4f7fa;
}
QMenuBar, QMenu, QStatusBar {
    background: #ffffff;
    border-color: #d8e1eb;
}
QFrame#pageHeader, QFrame#card, QFrame#emptyState, QWidget#actionBar {
    background: #ffffff;
    border: 1px solid #d8e1eb;
    border-radius: 7px;
}
QFrame#pageHeader { border-left: 4px solid #2563eb; }
QWidget#statusPill {
    background: #edf4fb;
    border: 1px solid #b9cee3;
    border-radius: 12px;
}
QWidget#statusPill[statusTone="success"] { background: #eaf7ef; border-color: #8bc7a0; }
QWidget#statusPill[statusTone="warning"] { background: #fff7e6; border-color: #e2b95f; }
QWidget#statusPill[statusTone="failure"] { background: #fff0f0; border-color: #df9292; }
QWidget#statusPill[statusTone="busy"] { background: #eaf2ff; border-color: #80a9e8; }
QLabel#pageTitle { font-size: 19pt; font-weight: 600; color: #10213d; }
QLabel#pageSubtitle, QLabel#mutedText { color: #52647a; }
QLabel#cardTitle { font-size: 11pt; font-weight: 600; color: #172b4d; }
QLabel#sectionEyebrow {
    color: #315d9b;
    font-size: 9.5pt;
    font-weight: 600;
    text-transform: uppercase;
}
QPushButton, QToolButton {
    min-height: 34px;
    font-size: 10pt;
    padding: 3px 11px;
    border: 1px solid #b8c5d3;
    border-radius: 5px;
    background: #ffffff;
}
QPushButton:hover, QToolButton:hover { background: #eef4ff; border-color: #7aa2df; }
QPushButton:pressed, QToolButton:pressed { background: #dfeaff; }
QPushButton:focus, QToolButton:focus {
    border: 2px solid #174ea6;
    padding: 2px 10px;
}
QPushButton:disabled, QToolButton:disabled {
    color: #758397;
    background: #eef2f6;
    border-color: #cfd8e3;
}
QPushButton[primary="true"] { color: #ffffff; background: #2563eb; border-color: #1d4ed8; font-weight: 600; }
QPushButton[primary="true"]:hover { background: #1d4ed8; }
QPushButton[primary="true"]:pressed { background: #173f9d; }
QPushButton[primary="true"]:disabled {
    color: #718096;
    background: #e1e7ee;
    border-color: #c4ced9;
}
QPushButton[destructive="true"] { color: #9f2d2d; border-color: #e1aaaa; }
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTreeWidget,
QListWidget, QTableWidget {
    background: #ffffff;
    border: 1px solid #c7d2df;
    border-radius: 4px;
    selection-background-color: #dbeafe;
    selection-color: #172033;
}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { min-height: 34px; padding: 1px 7px; }
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QTreeWidget:focus, QListWidget:focus, QTableWidget:focus {
    border: 2px solid #174ea6;
}
QComboBox::drop-down { border: 0; width: 22px; }
QHeaderView::section {
    background: #eef3f8;
    color: #30445f;
    border: 0;
    border-right: 1px solid #d8e1eb;
    border-bottom: 1px solid #c7d2df;
    padding: 6px;
    font-weight: 600;
}
QTableWidget { gridline-color: #e3e9f0; alternate-background-color: #f8fafc; }
QTabWidget::pane { border: 1px solid #d8e1eb; background: #ffffff; top: -1px; }
QTabBar::tab { min-height: 28px; background: #eaf0f6; padding: 8px 14px; margin-right: 2px; border-radius: 4px 4px 0 0; }
QTabBar::tab:selected { background: #ffffff; color: #1d4ed8; font-weight: 600; }
QTabBar::tab:focus { border: 2px solid #174ea6; }
QListWidget#sidebar {
    background: #f8fafc;
    border: 0;
    border-right: 1px solid #d8e1eb;
    border-radius: 0;
    outline: 0;
}
QListWidget#sidebar::item { padding: 6px 8px; border-left: 4px solid transparent; color: transparent; }
QListWidget#sidebar::item:selected { background: #e7f0ff; border-left-color: #2563eb; color: transparent; }
QListWidget#sidebar::item:hover { background: #eef3f8; }
QListWidget#sidebar::item:focus { border: 2px solid #174ea6; border-left: 4px solid #2563eb; }
QSplitter::handle { background: #e7edf4; width: 2px; height: 2px; }
QProgressBar { border: 1px solid #c7d2df; border-radius: 4px; background: #eef2f6; text-align: center; }
QProgressBar::chunk { background: #3b82f6; }
"""


def apply_theme(app: QApplication) -> None:
    """Apply the fixed professional light theme without third-party assets."""

    fusion = QStyleFactory.create("Fusion")
    if fusion is not None:
        app.setStyle(fusion)
        app.setProperty("workbenchStyle", "Fusion")
    segoe = Path("C:/Windows/Fonts/segoeui.ttf")
    if segoe.is_file():
        QFontDatabase.addApplicationFont(str(segoe))
    font = QFont("Segoe UI")
    font.setPointSizeF(10.5)
    app.setFont(font)
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#f4f7fa"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#172033"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#f8fafc"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#172033"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#172033"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#dbeafe"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#172033"))
    app.setPalette(palette)
    app.setStyleSheet(LIGHT_STYLE)

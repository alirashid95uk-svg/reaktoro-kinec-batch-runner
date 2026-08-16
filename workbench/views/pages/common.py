"""Shared widgets and formatting helpers for Workbench pages."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pyqtgraph as pg
from PySide6.QtCore import QDateTime, QLocale, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

def _table(name: str, headers: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setAccessibleName(name)
    table.setAccessibleDescription(
        f"Keyboard-accessible table with columns: {', '.join(headers)}"
    )
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setAlternatingRowColors(True)
    table.setWordWrap(False)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    table.horizontalHeader().setAccessibleName(f"{name} column headers")
    table.verticalHeader().setAccessibleName(f"{name} row headers")
    return table

def _short_id(value: Any) -> str:
    text = str(value or "")
    return text[:8] if len(text) > 12 else text

def _friendly(value: Any) -> str:
    text = str(value or "")
    return text.replace("_", " ").strip().capitalize() or "Not recorded"

def _friendly_time(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "Not recorded"
    parsed = QDateTime.fromString(text, Qt.DateFormat.ISODate)
    if not parsed.isValid():
        return text
    return QLocale.system().toString(
        parsed.toLocalTime(), QLocale.FormatType.ShortFormat
    )

def _set_combo_options(
    combo: QComboBox, options: list[tuple[str, str]], selected: str | None = None
) -> None:
    combo.blockSignals(True)
    combo.clear()
    for label, value in options:
        combo.addItem(label, value)
    if selected is not None:
        index = combo.findData(selected)
        if index >= 0:
            combo.setCurrentIndex(index)
    combo.blockSignals(False)

def _combo_value(combo: QComboBox) -> str:
    value = combo.currentData(Qt.ItemDataRole.UserRole)
    return str(value if value is not None else combo.currentText())

def _quantity_label(name: str, descriptor: Any) -> str:
    label = str(getattr(descriptor, "label", "") or name).replace("_", " ")
    unit = str(getattr(descriptor, "unit", "") or "").strip()
    return f"{label} ({unit})" if unit and unit.casefold() != "unitless" else label

def _configure_plot(plot: pg.PlotWidget) -> None:
    plot.setBackground("#ffffff")
    plot.showGrid(x=True, y=True, alpha=0.16)
    plot.getPlotItem().setContentsMargins(10, 10, 10, 10)
    for name in ("left", "bottom"):
        axis = plot.getAxis(name)
        axis.setPen(pg.mkPen("#7b8ca2"))
        axis.setTextPen(pg.mkPen("#34445a"))
        axis.enableAutoSIPrefix(False)

def _primary(button: QPushButton) -> QPushButton:
    button.setProperty("primary", True)
    return button

def _set_action_state(
    button: QPushButton,
    enabled: bool,
    *,
    ready: str,
    blocked: str,
) -> None:
    """Expose eligibility in text and UIA HelpText as well as enabled state."""

    button.setEnabled(enabled)
    button.setProperty("eligibility", "ready" if enabled else "blocked")
    explanation = ready if enabled else blocked
    button.setToolTip(explanation)
    button.setAccessibleDescription(explanation)

def _fill(table: QTableWidget, rows: list[list[Any]]) -> None:
    table.setRowCount(len(rows))
    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(row):
            item = QTableWidgetItem("" if value is None else str(value))
            item.setToolTip(item.text())
            table.setItem(row_index, column_index, item)

def _new_output_directory(parent: QWidget, title: str, default_name: str) -> str:
    path, _ = QFileDialog.getSaveFileName(parent, title, default_name, "Directory (*)")
    return path

def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}

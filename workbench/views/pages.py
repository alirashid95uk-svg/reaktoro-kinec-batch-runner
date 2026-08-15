"""The seven permanent scientific-workbench pages."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pyqtgraph as pg
import pyqtgraph.exporters
import pandas as pd
from PySide6.QtCore import QDateTime, QLocale, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from workbench_core.result_readers import ResultPackage, time_log_allowed, y_log_allowed
from workbench_core.documents import CaseDocument
from workbench_core.fingerprints import sha256_file
from workbench_core.run_index import search_runs
from workbench_core.studies import save_study_spec_text, validate_study_spec_text

from workbench.controllers.processes import HeadlessTaskController
from workbench.widgets.case_editor import CaseEditor
from workbench.widgets.presentation import Disclosure, EmptyState, action_bar, section_card
from workbench.widgets.status import StatusLabel


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


class _QuantityChecklist(QWidget):
    """Searchable standard-Qt checklist used by the dataset safety form."""

    changed = Signal()

    def __init__(self, accessible_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName(accessible_name)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search saved quantities")
        self.search.setAccessibleName(f"Search {accessible_name.casefold()}")
        self.list = QListWidget()
        self.list.setAccessibleName(accessible_name)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.search)
        layout.addWidget(self.list)
        self.search.textChanged.connect(self._filter)
        self.list.itemChanged.connect(lambda _item: self.changed.emit())

    def set_quantities(self, descriptors: dict[str, Any]) -> None:
        selected = set(self.values())
        self.list.blockSignals(True)
        self.list.clear()
        for name in sorted(descriptors, key=lambda item: _quantity_label(item, descriptors[item]).casefold()):
            item = QListWidgetItem(_quantity_label(name, descriptors[name]))
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setToolTip(f"Saved column: {name}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if name in selected else Qt.CheckState.Unchecked
            )
            self.list.addItem(item)
        self.list.blockSignals(False)
        self._filter(self.search.text())
        self.changed.emit()

    def values(self) -> list[str]:
        return [
            str(self.list.item(index).data(Qt.ItemDataRole.UserRole))
            for index in range(self.list.count())
            if self.list.item(index).checkState() == Qt.CheckState.Checked
        ]

    def clear_checks(self) -> None:
        self.list.blockSignals(True)
        for index in range(self.list.count()):
            self.list.item(index).setCheckState(Qt.CheckState.Unchecked)
        self.list.blockSignals(False)

    def _filter(self, text: str) -> None:
        query = text.casefold().strip()
        for index in range(self.list.count()):
            item = self.list.item(index)
            item.setHidden(bool(query and query not in item.text().casefold()))


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


def _quantity_group(name: str) -> str:
    if name in {"pH", "ionic_strength_molal", "alkalinity_eq_per_l"} or name.startswith("species_"):
        return "Aqueous state"
    if name.startswith("mineral_"):
        return "Minerals"
    if name.startswith("saturation_index"):
        return "Saturation"
    if name.startswith("reaction_rate"):
        return "Kinetics"
    return "Other"


def _run_filter(name: str, placeholder: str) -> QLineEdit:
    field = QLineEdit()
    field.setAccessibleName(f"Run filter: {name}")
    field.setPlaceholderText(placeholder)
    return field


class EnvironmentPage(QWidget):
    def __init__(self, project_root: Path, solver_prefix: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project_root, self.solver_prefix = project_root, solver_prefix
        self.setAccessibleName("Home and Environment")
        self.status = StatusLabel("Environment readiness")
        self.refresh_button = QPushButton("Run Environment Doctor")
        self.refresh_button.setAccessibleName("Run Environment Doctor")
        self.refresh_button.setAccessibleDescription(
            "Diagnose only; this never installs, repairs, updates, or switches environments"
        )
        _primary(self.refresh_button)
        self.checks = _table("Environment Doctor results", ["Check", "Outcome", "Evidence"])
        self.activity = _table("Recent activity", ["Case", "Run", "Status", "Updated"])
        self.workbench_readiness = StatusLabel("Workbench environment readiness")
        self.solver_readiness = StatusLabel("Verified solver environment readiness")
        self.project_readiness = StatusLabel("Project readiness")
        self.project_readiness.set_status(
            f"Project located: {project_root.name}", QStyle.StandardPixmap.SP_DirIcon
        )
        warning = QLabel(
            "Batch geochemistry only: results are not reactive transport or fracture-sealing evidence."
        )
        warning.setWordWrap(True)
        warning.setAccessibleName("Scientific scope warning")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        readiness = QHBoxLayout()
        for title, status in (
            ("Workbench", self.workbench_readiness),
            ("Solver", self.solver_readiness),
            ("Project", self.project_readiness),
        ):
            card, card_layout = section_card(title)
            card_layout.addWidget(status)
            readiness.addWidget(card, 1)
        layout.addLayout(readiness)
        doctor_card, doctor_layout = section_card(
            "Environment readiness",
            "Checks both isolated environments and records their exact identities. It never installs or changes packages.",
        )
        doctor_layout.addWidget(warning)
        self.check_details = Disclosure("Show environment details", self.checks)
        doctor_actions = QHBoxLayout()
        doctor_actions.addWidget(self.status)
        doctor_actions.addStretch(1)
        doctor_actions.addWidget(self.refresh_button)
        self.check_details.move_toggle_to(doctor_actions)
        doctor_layout.addLayout(doctor_actions)
        doctor_layout.addWidget(self.check_details)
        layout.addWidget(doctor_card)
        activity_card, activity_layout = section_card(
            "Recent activity", "Saved run records only; select Runs for full diagnosis and provenance."
        )
        activity_layout.addWidget(self.activity)
        layout.addWidget(activity_card, 1)
        self._doctor = HeadlessTaskController(self)
        self._doctor.succeeded.connect(self._doctor_succeeded)
        self._doctor.failed.connect(self._doctor_failed)
        self.refresh_button.clicked.connect(self.refresh)
        self.refresh_activity()
        self.setFocusProxy(self.refresh_button)

    def refresh(self) -> None:
        if self._doctor.is_active:
            return
        self.status.set_status("Checking solver environment", QStyle.StandardPixmap.SP_BrowserReload)
        self.refresh_button.setEnabled(False)
        self._doctor.start(
            "doctor",
            self.project_root,
            ["doctor", "--solver-prefix", str(self.solver_prefix)],
        )

    def refresh_activity(self) -> None:
        rows = []
        index = self.project_root / ".workbench" / "run_index.sqlite"
        if index.is_file():
            records = search_runs(index, limit=8)
            rows = [
                [
                    record.get("case_name") or record.get("case_id") or "Unnamed case",
                    _short_id(record.get("run_id")),
                    _friendly(record.get("status")),
                    _friendly_time(record.get("finished_at") or record.get("started_at")),
                ]
                for record in records
            ]
            _fill(self.activity, rows)
            for row, record in enumerate(records):
                for column in range(self.activity.columnCount()):
                    self.activity.item(row, column).setToolTip(
                        f"Run ID: {record.get('run_id') or 'not recorded'}\n"
                        f"Saved package: {record.get('run_path') or 'not recorded'}\n"
                        f"Recorded time: {record.get('finished_at') or record.get('started_at') or 'not recorded'}"
                    )
            return
        for path in sorted((self.project_root / "runs").rglob("run_record.json"), reverse=True)[:8]:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                rows.append(
                    [
                        record.get("case_id") or record.get("case_name") or "Unnamed case",
                        _short_id(record.get("run_id")),
                        _friendly(record.get("state")),
                        _friendly_time(record.get("updated_at_utc")),
                    ]
                )
            except (OSError, ValueError):
                rows.append([path.parent.name, "Unreadable", "Indeterminate", "Not recorded"])
        _fill(self.activity, rows)

    def _doctor_succeeded(self, _operation: str, result: dict) -> None:
        rows = []
        for group_name in ("workbench", "solver"):
            group = result.get(group_name, {})
            for check in group.get("checks", []):
                rows.append(
                    [
                        f"{group_name}: {check.get('name')}",
                        "Ready" if check.get("ok") else "Blocked",
                        check.get("detail"),
                    ]
                )
            identity = group.get(f"{group_name}_environment_identity", {})
            for key in (
                "python_version",
                "reaktoro_version",
                "package_inventory_sha256",
                "environment_export_sha256",
                "environment_spec_sha256",
                "launch_command",
            ):
                if identity.get(key) is not None:
                    rows.append([f"{group_name}: {key}", "Recorded", identity[key]])
            code = group.get("code_identity", {})
            if code:
                rows.append(
                    [
                        f"{group_name}: code identity",
                        "Dirty" if code.get("dirty") else "Clean",
                        code.get("relevant_tree_sha256") or code.get("commit"),
                    ]
                )
        ready = all(result.get(group, {}).get("ready") is True for group in ("workbench", "solver"))
        _fill(self.checks, rows)
        for group_name, indicator in (
            ("workbench", self.workbench_readiness),
            ("solver", self.solver_readiness),
        ):
            group_ready = result.get(group_name, {}).get("ready") is True
            indicator.set_status(
                "Ready" if group_ready else "Blocked — see details",
                QStyle.StandardPixmap.SP_DialogApplyButton
                if group_ready
                else QStyle.StandardPixmap.SP_MessageBoxCritical,
            )
        self.status.set_status(
            "Environment ready" if ready else "Environment blocked",
            QStyle.StandardPixmap.SP_DialogApplyButton
            if ready
            else QStyle.StandardPixmap.SP_MessageBoxCritical,
        )
        self.check_details.set_expanded(not ready)
        self.check_details.toggle.setChecked(not ready)
        self.refresh_button.setEnabled(True)

    def _doctor_failed(self, _operation: str, detail: str) -> None:
        _fill(self.checks, [["Environment Doctor", "Blocked", detail]])
        self.status.set_status("Environment blocked", QStyle.StandardPixmap.SP_MessageBoxCritical)
        for indicator in (self.workbench_readiness, self.solver_readiness):
            indicator.set_status("Blocked — see details", QStyle.StandardPixmap.SP_MessageBoxCritical)
        self.check_details.toggle.setChecked(True)
        self.refresh_button.setEnabled(True)


class CasesPage(QWidget):
    validation_requested = Signal(str)
    prepare_requested = Signal(str)

    def __init__(self, project_root: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project_root = project_root
        self.setAccessibleName("Cases")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search cases")
        self.search.setAccessibleName("Search case library")
        self.case_list = QListWidget()
        self.case_list.setAccessibleName("Case library")
        self.case_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.case_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.case_list.setMinimumHeight(0)
        self.case_list.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Ignored)
        self.open_button = QPushButton("Open")
        self.open_button.setAccessibleName("Open selected case")
        self.new_button = QPushButton("New")
        self.new_button.setAccessibleName("Create unsaved case from schema template")
        self.duplicate_button = QPushButton("Duplicate")
        self.duplicate_button.setAccessibleName("Duplicate as unsaved case")
        self.import_button = QPushButton("Import")
        self.import_button.setAccessibleName("Import case without modifying its source")
        self.archive_button = QPushButton("Archive")
        self.archive_button.setAccessibleName("Archive selected case without deleting it")
        self.save_button = QPushButton("Save")
        self.save_button.setAccessibleName("Save current case atomically")
        self.validate_button = QPushButton("Validate saved case")
        self.validate_button.setAccessibleName("Validate saved case")
        self.prepare_button = QPushButton("Prepare validated run for queue")
        self.prepare_button.setAccessibleName("Prepare validated run for queue")
        _primary(self.prepare_button)
        self.document_status = StatusLabel("Case document state")
        self.validation_status = StatusLabel("Case validation state")
        self.editor = CaseEditor()
        self.editor_stack = QStackedWidget()
        self.editor_empty = EmptyState(
            "Open or create a case",
            "Select a project case, import YAML, or create a new case before scientific values are shown.",
        )
        self.editor_stack.addWidget(self.editor_empty)
        self.editor_stack.addWidget(self.editor)
        self._document_state = "none"
        self._validation_state = "not_checked"
        library, library_layout = section_card(
            "Case library", "Project cases remain ordinary YAML files."
        )
        library_layout.setSpacing(6)
        library.setMinimumWidth(240)
        library.setMaximumWidth(310)
        library_layout.addWidget(self.search)
        library_layout.addWidget(self.case_list, 1)
        library_actions = QGridLayout()
        library_actions.addWidget(self.open_button, 0, 0, 1, 2)
        library_actions.addWidget(self.new_button, 1, 0)
        library_actions.addWidget(self.import_button, 1, 1)
        library_actions.addWidget(self.duplicate_button, 2, 0)
        library_actions.addWidget(self.archive_button, 2, 1)
        library_layout.addLayout(library_actions)
        library_layout.addWidget(self.document_status)
        library_layout.addWidget(self.validation_status)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(library)
        splitter.addWidget(self.editor_stack)
        splitter.setSizes([270, 950])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(
            action_bar(self.save_button, self.validate_button, self.prepare_button)
        )
        layout.addWidget(splitter)
        self.search.textChanged.connect(self.refresh)
        self.open_button.clicked.connect(self.open_selected)
        self.new_button.clicked.connect(self.new_from_template)
        self.case_list.itemActivated.connect(self.open_selected)
        self.import_button.clicked.connect(self.import_case)
        self.duplicate_button.clicked.connect(self.duplicate_case)
        self.archive_button.clicked.connect(self.archive_selected)
        self.save_button.clicked.connect(self.editor.save)
        self.validate_button.clicked.connect(self.validate_case)
        self.prepare_button.clicked.connect(self.prepare_case)
        self.editor.document_state_changed.connect(self._document_state_changed)
        self.editor.validation_state_changed.connect(self._validation_state_changed)
        self.case_list.currentItemChanged.connect(lambda *_args: self._refresh_actions())
        self.editor.case_saved.connect(lambda _path: self._refresh_actions())
        self.refresh()
        self._refresh_actions()
        self.setFocusProxy(self.search)

    def refresh(self) -> None:
        selected = self.case_list.currentItem().data(Qt.ItemDataRole.UserRole) if self.case_list.currentItem() else None
        query = self.search.text().casefold()
        self.case_list.clear()
        for path in sorted((self.project_root / "cases").glob("*.yaml")):
            if query and query not in path.name.casefold():
                continue
            item = QListWidgetItem(path.stem)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            self.case_list.addItem(item)
        if selected:
            matches = self.case_list.findItems(Path(selected).stem, Qt.MatchFlag.MatchExactly)
            if matches:
                self.case_list.setCurrentItem(matches[0])

    def open_selected(self) -> None:
        item = self.case_list.currentItem()
        if item:
            try:
                self.editor.load_path(item.data(Qt.ItemDataRole.UserRole))
                self.editor_stack.setCurrentWidget(self.editor)
            except Exception as error:
                self.editor._show_error(error)
            self._refresh_actions()

    def import_case(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import case", str(self.project_root), "YAML (*.yaml *.yml)")
        if path:
            try:
                self.editor.import_path(path)
                self.editor_stack.setCurrentWidget(self.editor)
            except Exception as error:
                self.editor._show_error(error)
            self._refresh_actions()

    def new_from_template(self) -> None:
        try:
            self.editor.import_path(self.project_root / "cases" / "schema_template.yaml")
            self.editor_stack.setCurrentWidget(self.editor)
        except Exception as error:
            self.editor._show_error(error)
        self._refresh_actions()

    def archive_selected(self) -> None:
        item = self.case_list.currentItem()
        if item is None:
            return
        source = Path(item.data(Qt.ItemDataRole.UserRole)).resolve()
        if QMessageBox.question(
            self,
            "Archive case",
            f"Move {source.name} to cases/archive? The file is not deleted.",
        ) != QMessageBox.StandardButton.Yes:
            return
        archive = self.project_root / "cases" / "archive"
        archive.mkdir(parents=True, exist_ok=True)
        target = archive / source.name
        if target.exists():
            self.editor._show_error(FileExistsError(f"archive target already exists: {target}"))
            return
        source.replace(target)
        self.refresh()

    def duplicate_case(self) -> None:
        if self.editor.document is not None:
            self.editor.load_document(CaseDocument.from_text(self.editor.document.to_text()))
            self.editor_stack.setCurrentWidget(self.editor)
        self._refresh_actions()

    def validate_case(self) -> None:
        document = self.editor.document
        if document and document.source_path and not document.is_dirty and not self.editor.form_is_stale:
            self.validation_requested.emit(str(document.source_path))
        else:
            self.validation_status.set_status(
                "Blocked: save a valid current case first",
                QStyle.StandardPixmap.SP_MessageBoxWarning,
            )

    def prepare_case(self) -> None:
        document = self.editor.document
        if (
            document
            and document.source_path
            and not document.is_dirty
            and not self.editor.form_is_stale
            and self._validation_state == "ready"
        ):
            self.prepare_requested.emit(str(document.source_path))
        else:
            self.validation_status.set_status(
                "Blocked: validate the current saved revision first",
                QStyle.StandardPixmap.SP_MessageBoxWarning,
            )

    def _document_state_changed(self, state: str) -> None:
        self._document_state = state
        self.editor_stack.setCurrentWidget(self.editor)
        icon = (
            QStyle.StandardPixmap.SP_MessageBoxWarning
            if state in {"external_conflict", "template_placeholders"}
            else QStyle.StandardPixmap.SP_MessageBoxInformation
        )
        self.document_status.set_status(_friendly(state), icon)
        if state not in {"clean"}:
            self._validation_state = "stale"
        self._refresh_actions()

    def _validation_state_changed(self, state: str) -> None:
        self._validation_state = state
        self.validation_status.set_status(
            _friendly(state),
            QStyle.StandardPixmap.SP_DialogApplyButton
            if state == "ready"
            else QStyle.StandardPixmap.SP_MessageBoxWarning,
        )
        self._refresh_actions()

    def _refresh_actions(self) -> None:
        document = self.editor.document
        has_document = document is not None
        has_selection = self.case_list.currentItem() is not None
        clean_saved = bool(
            document
            and document.source_path
            and not document.is_dirty
            and not self.editor.form_is_stale
            and self._document_state == "clean"
        )
        saveable = bool(
            document
            and (document.source_path is None or document.is_dirty)
            and self._document_state != "external_conflict"
        )
        self.open_button.setEnabled(has_selection)
        self.archive_button.setEnabled(has_selection)
        self.archive_button.setVisible(has_selection)
        self.duplicate_button.setEnabled(has_document)
        self.duplicate_button.setVisible(has_document)
        self.document_status.setVisible(has_document)
        self.validation_status.setVisible(has_document)
        _set_action_state(
            self.save_button,
            saveable,
            ready="Save the current valid revision atomically.",
            blocked="Open or change a valid case before saving.",
        )
        _set_action_state(
            self.validate_button,
            clean_saved,
            ready="Validate the current clean saved revision.",
            blocked="Save a schema-valid current revision before validation.",
        )
        _set_action_state(
            self.prepare_button,
            clean_saved and self._validation_state == "ready",
            ready="Prepare the successfully validated saved revision for the queue.",
            blocked="Validate the unchanged current saved revision before preparing it.",
        )


class QueuePage(QWidget):
    run_requested = Signal(dict)
    graceful_cancel_requested = Signal()
    force_terminate_requested = Signal()
    pause_after_current_requested = Signal()
    cancel_after_current_requested = Signal()
    resume_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("Queue")
        self.policy = QComboBox()
        _set_combo_options(
            self.policy,
            [
                ("Stop after a failure", "stop_after_failure"),
                ("Continue after a failure", "continue_after_failure"),
                ("Pause for a decision", "pause_for_decision"),
            ],
        )
        self.policy.setAccessibleName("Queue failure policy")
        self.workers = QSpinBox()
        self.workers.setRange(1, 1)
        self.workers.setValue(1)
        self.workers.setReadOnly(True)
        self.workers.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.workers.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.workers.setAccessibleName("Maximum worker processes")
        self.workers.setAccessibleDescription("Sequential execution is verified; parallel execution is disabled")
        self.table = _table(
            "Immutable run queue",
            ["Order", "Case", "State", "Run", "Duplicate or replicate warning"],
        )
        self.start_button = QPushButton("Start queue")
        _primary(self.start_button)
        self.move_up_button = QPushButton("Move up")
        self.move_down_button = QPushButton("Move down")
        self.pause_button = QPushButton("Pause after current")
        self.cancel_after_button = QPushButton("Cancel after current")
        self.graceful_button = QPushButton("Graceful cancel current")
        self.force_button = QPushButton("Force terminate current")
        for button in (
            self.start_button,
            self.pause_button,
            self.cancel_after_button,
            self.graceful_button,
            self.force_button,
            self.move_up_button,
            self.move_down_button,
        ):
            button.setAccessibleName(button.text())
        for button in (
            self.pause_button,
            self.cancel_after_button,
            self.graceful_button,
            self.force_button,
            self.move_up_button,
            self.move_down_button,
        ):
            button.setEnabled(False)
        self.status = StatusLabel("Queue status")
        self.queue_state_summary = StatusLabel("Persisted queue state")
        self.entry_count_summary = StatusLabel("Queue entry count")
        self.active_run_summary = StatusLabel("Active queue run")
        self.worker_summary = StatusLabel("Verified worker count")
        self.queue_state_summary.set_status("Idle", QStyle.StandardPixmap.SP_MediaStop)
        self.entry_count_summary.set_status("0 entries", QStyle.StandardPixmap.SP_FileDialogListView)
        self.active_run_summary.set_status("No active run", QStyle.StandardPixmap.SP_MediaStop)
        self.worker_summary.set_status("1 verified sequential worker", QStyle.StandardPixmap.SP_ComputerIcon)
        self._active = False
        self._paused = False
        self.run_record_paths: list[str] = []
        self.monitor = _table("Current run monitor", ["Measure", "Current value"])
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("No active simulation")
        self.progress.setAccessibleName("Accepted simulation time progress")
        self._monitor_values = {
            "Queue position": "Not running",
            "Lifecycle stage": "Not started",
            "Accepted simulation time": "Not available",
            "Requested duration": "Not available",
            "Current or last timestep": "Not available",
            "Accepted attempts": "0",
            "Rejected attempts": "0",
            "Elapsed wall time": "0 s",
            "Latest warning or reason": "None",
            "Process state": "Not running",
            "Output completeness": "not_written",
        }
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        summaries = QHBoxLayout()
        for title, indicator in (
            ("Queue state", self.queue_state_summary),
            ("Entries", self.entry_count_summary),
            ("Active run", self.active_run_summary),
            ("Workers", self.worker_summary),
        ):
            card, card_layout = section_card(title)
            card_layout.addWidget(indicator)
            summaries.addWidget(card, 1)
        layout.addLayout(summaries)
        controls_card, controls_layout = section_card(
            "Execution policy",
            "Entries reference immutable validated snapshots. Execution is deliberately sequential.",
        )
        policy_form = QHBoxLayout()
        policy_form.addWidget(QLabel("Failure policy"))
        policy_form.addWidget(self.policy, 1)
        policy_form.addWidget(QLabel("Verified workers"))
        policy_form.addWidget(self.workers)
        policy_form.addWidget(self.start_button)
        policy_form.addWidget(self.move_up_button)
        policy_form.addWidget(self.move_down_button)
        controls_layout.addLayout(policy_form)
        self.execution_controls = action_bar(
            self.pause_button,
            self.cancel_after_button,
            self.graceful_button,
            self.force_button,
        )
        self.execution_controls.setVisible(False)
        controls_layout.addWidget(self.execution_controls)
        controls_layout.addWidget(self.status)
        self.live_area = QWidget()
        live_layout = QVBoxLayout(self.live_area)
        live_layout.setContentsMargins(0, 0, 0, 0)
        live_layout.addWidget(self.progress)
        controls_layout.addWidget(self.live_area)
        layout.addWidget(controls_card)
        queue_card, queue_layout = section_card("Prepared runs")
        queue_layout.addWidget(self.table)
        layout.addWidget(queue_card, 1)
        self.monitor_details = Disclosure("Show current-run numerical details", self.monitor)
        layout.addWidget(self.monitor_details)
        self._refresh_monitor()
        self.start_button.clicked.connect(self._start_current)
        self.pause_button.clicked.connect(self._toggle_pause)
        self.cancel_after_button.clicked.connect(self._cancel_pending)
        self.graceful_button.clicked.connect(self.graceful_cancel_requested)
        self.force_button.clicked.connect(self.force_terminate_requested)
        self.move_up_button.clicked.connect(lambda: self._move_selected(-1))
        self.move_down_button.clicked.connect(lambda: self._move_selected(1))
        self.table.currentCellChanged.connect(lambda *_args: self._refresh_actions())
        self._refresh_actions()
        self.live_area.setVisible(False)
        self.monitor_details.setVisible(False)
        self.setFocusProxy(self.policy)

    def add_snapshot(self, path: str | Path, run_id: str | None = None) -> str:
        raise RuntimeError("queue entries require a ready run record; use add_prepared_run")

    def _set_queue_row(
        self,
        row: int,
        *,
        order: int,
        case_name: str,
        snapshot: Path,
        state: str,
        run_id: str,
        reason: str,
        run_record: Path,
    ) -> None:
        warning = reason if len(reason) <= 72 else f"{reason[:69]}..."
        values = (order, case_name, _friendly(state), _short_id(run_id), warning)
        for column, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            self.table.setItem(row, column, item)
        self.table.item(row, 1).setData(Qt.ItemDataRole.UserRole, str(snapshot))
        self.table.item(row, 1).setToolTip(str(snapshot))
        self.table.item(row, 2).setData(Qt.ItemDataRole.UserRole, state)
        self.table.item(row, 3).setData(Qt.ItemDataRole.UserRole, str(run_record))
        self.table.item(row, 3).setData(Qt.ItemDataRole.UserRole + 1, run_id)
        self.table.item(row, 3).setToolTip(run_id)
        self.table.item(row, 4).setToolTip(reason)

    def _row_state(self, row: int) -> str:
        item = self.table.item(row, 2)
        return str(item.data(Qt.ItemDataRole.UserRole) or item.text()).casefold() if item else ""

    def _set_row_state(self, row: int, state: str) -> None:
        item = self.table.item(row, 2)
        if item is not None:
            item.setText(_friendly(state))
            item.setData(Qt.ItemDataRole.UserRole, state)

    def _run_id_at(self, row: int) -> str:
        item = self.table.item(row, 3)
        return str(item.data(Qt.ItemDataRole.UserRole + 1) or item.text()) if item else ""

    def _update_queue_summary(self, state: str | None = None) -> None:
        self.entry_count_summary.set_status(
            f"{self.table.rowCount()} entr{'y' if self.table.rowCount() == 1 else 'ies'}",
            QStyle.StandardPixmap.SP_FileDialogListView,
        )
        if state:
            self.queue_state_summary.set_status(
                state.replace("_", " ").title(),
                QStyle.StandardPixmap.SP_MediaPlay
                if state == "running"
                else QStyle.StandardPixmap.SP_MessageBoxWarning
                if "fail" in state or "block" in state or "interrupt" in state
                else QStyle.StandardPixmap.SP_DialogApplyButton,
            )

    def add_prepared_run(self, record: dict) -> str:
        if record.get("state") != "ready" or not record.get("validation_receipt_path"):
            raise ValueError("queue entries require a final ready snapshot and validation receipt")
        snapshot = Path(record["snapshot_path"]).resolve()
        run_record = snapshot.parent / "run_record.json"
        if not run_record.is_file():
            raise FileNotFoundError(run_record)
        identifier = str(record["run_id"])
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._set_queue_row(
            row,
            order=row + 1,
            case_name=str(record.get("case_id") or snapshot.stem),
            snapshot=snapshot,
            state="queued",
            run_id=identifier,
            reason="None",
            run_record=run_record,
        )
        self.run_record_paths.append(str(run_record))
        self._update_queue_summary("ready")
        self.table.selectRow(row)
        self._refresh_actions()
        return identifier

    def restore_queue(self, path: str | Path) -> None:
        queue_path = Path(path)
        if not queue_path.is_file():
            return
        queue = json.loads(queue_path.read_text(encoding="utf-8"))
        self.table.setRowCount(0)
        self.run_record_paths.clear()
        for entry in sorted(queue.get("entries", []), key=lambda item: item["order"]):
            snapshot = Path(entry["snapshot_path"])
            run_record = snapshot.parent / "run_record.json"
            row = self.table.rowCount()
            self.table.insertRow(row)
            self._set_queue_row(
                row,
                order=entry["order"] + 1,
                case_name=str(entry.get("case_id") or snapshot.stem),
                snapshot=snapshot,
                state=entry["entry_state"],
                run_id=str(entry["run_id"]),
                reason=entry.get("status_reason") or "None",
                run_record=run_record,
            )
            self.run_record_paths.append(str(run_record))
        policy_index = self.policy.findData(queue.get("failure_policy", "stop_after_failure"))
        if policy_index >= 0:
            self.policy.setCurrentIndex(policy_index)
        state = str(queue.get("queue_state", "indeterminate"))
        self.set_persisted_state(state)
        self.status.set_status(
            f"Restored queue state: {state.replace('_', ' ')}",
            QStyle.StandardPixmap.SP_MessageBoxInformation,
        )
        self._update_queue_summary(state)
        self._refresh_actions()

    def _move_selected(self, offset: int) -> None:
        if self._active:
            return
        source = self.table.currentRow()
        target = source + offset
        if source < 0 or target < 0 or target >= self.table.rowCount():
            return
        values = [self.table.takeItem(source, column) for column in range(self.table.columnCount())]
        self.table.removeRow(source)
        self.table.insertRow(target)
        for column, item in enumerate(values):
            self.table.setItem(target, column, item)
        moved = self.run_record_paths.pop(source)
        self.run_record_paths.insert(target, moved)
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setText(str(row + 1))
        self.table.selectRow(target)

    def _start_current(self) -> None:
        if self._active or self._paused:
            return
        for row in range(self.table.rowCount()):
            if self._row_state(row) == "queued":
                self._set_row_state(row, "starting")
                pending = [
                    self.table.item(candidate, 3).data(Qt.ItemDataRole.UserRole)
                    for candidate in range(self.table.rowCount())
                    if self._row_state(candidate) in {"queued", "starting"}
                ]
                self.run_requested.emit(
                    {
                        "run_records": pending,
                        "failure_policy": _combo_value(self.policy),
                    }
                )
                self.status.set_status("Starting one sequential worker", QStyle.StandardPixmap.SP_MediaPlay)
                self._active = True
                self._refresh_actions()
                return

    def _toggle_pause(self) -> None:
        self._paused = not self._paused
        self.pause_button.setText("Resume queue" if self._paused else "Pause after current")
        self.status.set_status(
            "Pause after current requested" if self._paused else "Queue resumed",
            QStyle.StandardPixmap.SP_MediaPause
            if self._paused
            else QStyle.StandardPixmap.SP_MediaPlay,
        )
        if self._paused:
            self.pause_after_current_requested.emit()
            if self._active:
                self.pause_button.setEnabled(False)
        else:
            self.resume_requested.emit()
            if not self._active:
                self.pause_button.setVisible(False)
                self.execution_controls.setVisible(False)
        self._refresh_actions()

    def _cancel_pending(self) -> None:
        self.cancel_after_current_requested.emit()
        self.cancel_after_button.setEnabled(False)
        self.status.set_status(
            "Cancel after current requested; active solver is not paused",
            QStyle.StandardPixmap.SP_DialogCancelButton,
        )

    def mark_finished(self, run_id: str, classification: str) -> None:
        for row in range(self.table.rowCount()):
            if self._run_id_at(row) == run_id:
                self._set_row_state(row, classification)
        self.status.set_status(classification.replace("_", " ").title(), QStyle.StandardPixmap.SP_MessageBoxInformation)
        self._monitor_values["Process state"] = classification
        self.active_run_summary.set_status("No active run", QStyle.StandardPixmap.SP_MediaStop)
        self._active = False
        self._refresh_monitor()
        self._refresh_actions()

    def finish_queue(self, status: str) -> None:
        self._active = False
        has_queued = any(
            self._row_state(row) == "queued" for row in range(self.table.rowCount())
        )
        show_resume = self._paused and has_queued
        self.pause_button.setEnabled(show_resume)
        self.pause_button.setVisible(show_resume)
        self.cancel_after_button.setEnabled(False)
        self.cancel_after_button.setVisible(False)
        self.graceful_button.setEnabled(False)
        self.graceful_button.setVisible(False)
        self.force_button.setEnabled(False)
        self.force_button.setVisible(False)
        self.execution_controls.setVisible(show_resume)
        self._monitor_values["Process state"] = status
        self.active_run_summary.set_status("No active run", QStyle.StandardPixmap.SP_MediaStop)
        self.progress.setValue(0)
        self.progress.setFormat("No active simulation")
        self.live_area.setVisible(False)
        self.monitor_details.setVisible(False)
        self._update_queue_summary(status)
        self._refresh_monitor()
        self._refresh_actions()

    def begin_execution(self) -> None:
        self._active = True
        for button in (
            self.pause_button,
            self.cancel_after_button,
            self.graceful_button,
            self.force_button,
        ):
            button.setEnabled(True)
            button.setVisible(True)
        self.execution_controls.setVisible(True)
        self.live_area.setVisible(True)
        self.monitor_details.setVisible(True)
        self._monitor_values["Process state"] = "running"
        self._update_queue_summary("running")
        self._refresh_monitor()
        self._refresh_actions()

    def set_persisted_state(self, state: str) -> None:
        self._paused = state == "paused"
        self.pause_button.setText("Resume queue" if self._paused else "Pause after current")
        if state != "running":
            self._monitor_values.update(
                {
                    "Queue position": "No active run",
                    "Lifecycle stage": "Not active",
                    "Accepted simulation time": "See individual run record",
                    "Requested duration": "See individual run record",
                    "Current or last timestep": "See individual run record",
                    "Output completeness": "See individual run record",
                }
            )
        self.finish_queue(state)

    def mark_running(self, run_id: str) -> None:
        for row in range(self.table.rowCount()):
            if self._run_id_at(row) == run_id:
                self._set_row_state(row, "running")
                self._monitor_values["Queue position"] = f"{row + 1} of {self.table.rowCount()}"
        self._monitor_values["Process state"] = "running"
        self.active_run_summary.set_status(
            _short_id(run_id), QStyle.StandardPixmap.SP_MediaPlay
        )
        self._refresh_monitor()
        self._refresh_actions()

    def update_monitor(self, event: dict, elapsed_seconds: float) -> None:
        payload = event.get("payload", {})
        event_type = event.get("event_type")
        if event_type in {"stage_started", "stage_completed"}:
            self._monitor_values["Lifecycle stage"] = str(payload.get("stage", "unknown"))
        elif event_type == "progress_summary":
            accepted = payload.get("accepted_time_s", payload.get("accepted_simulation_time_s"))
            requested = payload.get("requested_duration_s")
            self._monitor_values.update(
                {
                    "Accepted simulation time": f"{accepted if accepted is not None else 'unknown'} s",
                    "Requested duration": f"{requested if requested is not None else 'unknown'} s",
                    "Current or last timestep": f"{payload.get('current_dt_s', 'unknown')} s",
                    "Accepted attempts": str(payload.get("accepted_attempts", "unknown")),
                    "Rejected attempts": str(payload.get("rejected_attempts", "unknown")),
                    "Latest warning or reason": str(payload.get("latest_reason") or "None"),
                }
            )
            try:
                fraction = max(0.0, min(1.0, float(accepted) / float(requested)))
            except (TypeError, ValueError, ZeroDivisionError):
                self.progress.setRange(0, 0)
                self.progress.setFormat("Simulation running")
            else:
                self.progress.setRange(0, 100)
                self.progress.setValue(round(fraction * 100))
                self.progress.setFormat(f"Accepted time: {fraction:.0%}")
        elif event_type == "warning":
            self._monitor_values["Latest warning or reason"] = str(
                payload.get("message") or payload
            )
        elif event_type == "output_written":
            completeness = payload.get("output_completeness", {})
            self._monitor_values["Output completeness"] = str(
                completeness.get("status", completeness)
                if isinstance(completeness, dict)
                else completeness
            )
        self._monitor_values["Elapsed wall time"] = f"{elapsed_seconds:.1f} s"
        self._refresh_monitor()

    def _refresh_monitor(self) -> None:
        _fill(self.monitor, [[key, value] for key, value in self._monitor_values.items()])

    def _refresh_actions(self) -> None:
        queued_rows = [
            row for row in range(self.table.rowCount()) if self._row_state(row) == "queued"
        ]
        current = self.table.currentRow()
        selected_queued = current in queued_rows
        can_reorder = selected_queued and not self._active and not self._paused
        can_start = bool(queued_rows) and not self._active and not self._paused
        _set_action_state(
            self.start_button,
            can_start,
            ready="Start the queued validated snapshots with the displayed policy.",
            blocked="Add queued entries and ensure no execution is active or paused.",
        )
        self.move_up_button.setEnabled(can_reorder and current > 0)
        self.move_down_button.setEnabled(
            can_reorder and current >= 0 and current < self.table.rowCount() - 1
        )
        self.move_up_button.setVisible(bool(queued_rows) and not self._active)
        self.move_down_button.setVisible(bool(queued_rows) and not self._active)


class RunsPage(QWidget):
    run_selected = Signal(str)
    report_requested = Signal(dict)
    rebuild_requested = Signal()

    def __init__(self, project_root: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project_root = project_root
        self.setAccessibleName("Runs")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search case, run ID, or warning text")
        self.search.setAccessibleName("Search run history")
        self.status_filter = _run_filter("Status", "Exact status")
        self.model_filter = _run_filter("Kinetic model", "Exact kinetic model")
        self.workflow_filter = _run_filter("Workflow", "Exact workflow mode")
        self.study_filter = _run_filter("Study", "Exact study ID")
        self.schema_filter = _run_filter("Output schema", "Exact output schema")
        self.started_after = _run_filter("Started after", "ISO timestamp, inclusive")
        self.started_before = _run_filter("Started before", "ISO timestamp, inclusive")
        self.table = _table(
            "Artifact-derived run history",
            ["Run ID", "Case", "Status", "Completeness", "Updated", "Path"],
        )
        self.refresh_button = QPushButton("Rebuild index")
        self.refresh_button.setAccessibleName("Rebuild run view from authoritative artifacts")
        self.report_type = QComboBox()
        _set_combo_options(
            self.report_type,
            [("Failure diagnosis", "diagnosis"), ("Run summary", "run")],
        )
        self.report_type.setAccessibleName("Selected run report type")
        self.report_button = QPushButton("Generate selected report")
        self.report_button.setAccessibleName("Generate selected report from saved artifacts")
        self.selected_run_heading = QLabel("Select a run to inspect its saved evidence")
        self.selected_run_heading.setObjectName("cardTitle")
        self.selected_run_heading.setAccessibleName("Selected run")
        self.summary_evidence = _table("Selected run summary", ["Evidence", "Recorded value"])
        self.diagnosis_evidence = _table("Selected run failure diagnosis", ["Evidence", "Recorded value"])
        self.provenance_evidence = _table("Selected run provenance", ["Evidence", "Recorded value"])
        self.evidence = self.provenance_evidence
        self.report_evidence = QLabel(
            "Select a run with saved run, diagnostics, or manifest artifacts to generate a derived report."
        )
        self.report_evidence.setWordWrap(True)
        self.report_evidence.setAccessibleName("Selected run report eligibility")
        report_page = QWidget()
        report_layout = QVBoxLayout(report_page)
        report_layout.addWidget(self.report_evidence)
        report_layout.addWidget(action_bar(self.report_type, self.report_button))
        report_layout.addStretch(1)
        self.detail_tabs = QTabWidget()
        self.detail_tabs.setAccessibleName("Selected run evidence")
        self.detail_tabs.addTab(self.summary_evidence, "Summary")
        self.detail_tabs.addTab(self.diagnosis_evidence, "Failure / diagnosis")
        self.detail_tabs.addTab(self.provenance_evidence, "Provenance")
        self.detail_tabs.addTab(report_page, "Report")
        self.result_count = QLabel("0 saved runs")
        self.result_count.setObjectName("mutedText")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        search_row = QHBoxLayout()
        search_row.addWidget(self.search, 1)
        search_row.addWidget(self.result_count)
        search_row.addWidget(self.refresh_button)
        filter_widget = QWidget()
        filters = QGridLayout(filter_widget)
        filters.setContentsMargins(0, 0, 0, 0)
        for index, (label, widget) in enumerate(
            (
                ("Status", self.status_filter),
                ("Model", self.model_filter),
                ("Workflow", self.workflow_filter),
                ("Study", self.study_filter),
                ("Schema", self.schema_filter),
                ("Started after", self.started_after),
                ("Started before", self.started_before),
            )
        ):
            row, column = divmod(index, 4)
            filters.addWidget(QLabel(label), row * 2, column)
            filters.addWidget(widget, row * 2 + 1, column)
        self.filter_details = Disclosure("Show run filters", filter_widget)
        self.filter_details.move_toggle_to(search_row)
        layout.addLayout(search_row)
        layout.addWidget(self.filter_details)
        runs_card, runs_layout = section_card(
            "Saved runs", "Activate a row to open the immutable result package."
        )
        runs_layout.addWidget(self.table)
        detail_card, detail_layout = section_card(
            "Diagnosis and provenance", "Immutable recorded evidence from the selected result package."
        )
        detail_layout.addWidget(self.selected_run_heading)
        detail_layout.addWidget(self.detail_tabs)
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(runs_card)
        splitter.addWidget(detail_card)
        splitter.setSizes([390, 260])
        layout.addWidget(splitter, 1)
        self.table.setColumnHidden(5, True)
        self.refresh_button.clicked.connect(self.rebuild_requested)
        self.search.textChanged.connect(self.refresh)
        for widget in (
            self.status_filter,
            self.model_filter,
            self.workflow_filter,
            self.study_filter,
            self.schema_filter,
            self.started_after,
            self.started_before,
        ):
            widget.textChanged.connect(self.refresh)
        self.table.itemActivated.connect(self._activated)
        self.table.currentCellChanged.connect(self._show_evidence)
        self.report_button.clicked.connect(self._report)
        self.report_button.setEnabled(False)
        self.refresh()
        self.setFocusProxy(self.search)

    def refresh(self) -> None:
        query = self.search.text().casefold()
        rows = []
        index = self.project_root / ".workbench" / "run_index.sqlite"
        if index.is_file():
            records = search_runs(
                index,
                text=query,
                status=self.status_filter.text().strip() or None,
                kinetic_model=self.model_filter.text().strip() or None,
                workflow_mode=self.workflow_filter.text().strip() or None,
                study_id=self.study_filter.text().strip() or None,
                output_schema_version=self.schema_filter.text().strip() or None,
                started_after=self.started_after.text().strip() or None,
                started_before=self.started_before.text().strip() or None,
                limit=500,
            )
            rows = [
                [
                    record.get("run_id"),
                    record.get("case_name"),
                    record.get("status"),
                    record.get("output_completeness"),
                    record.get("finished_at") or record.get("started_at"),
                    record.get("run_path"),
                ]
                for record in records
            ]
            _fill(self.table, rows)
            self._decorate_rows()
            return
        for path in sorted((self.project_root / "runs").rglob("run_record.json"), reverse=True):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                record = {"run_id": "unknown", "state": "indeterminate"}
            row = [
                record.get("run_id"),
                record.get("case_id") or record.get("case_name"),
                record.get("state") or record.get("termination_category"),
                (record.get("output_completeness") or {}).get("status"),
                record.get("updated_at_utc"),
                path.parent,
            ]
            if not self._fallback_matches(record, path.parent):
                continue
            if not query or query in " ".join(map(str, row)).casefold():
                rows.append(row)
                if len(rows) == 500:
                    break
        _fill(self.table, rows)
        self._decorate_rows()

    def _decorate_rows(self) -> None:
        self.result_count.setText(
            f"{self.table.rowCount()} saved run{'s' if self.table.rowCount() != 1 else ''}"
        )
        for row in range(self.table.rowCount()):
            run_item = self.table.item(row, 0)
            status_item = self.table.item(row, 2)
            if run_item:
                full_id = run_item.text()
                run_item.setText(_short_id(full_id))
                run_item.setToolTip(full_id)
                run_item.setData(Qt.ItemDataRole.UserRole, full_id)
            if status_item:
                raw_status = status_item.text()
                status = raw_status.casefold()
                status_item.setData(Qt.ItemDataRole.UserRole, raw_status)
                status_item.setText(_friendly(raw_status))
                icon = (
                    QStyle.StandardPixmap.SP_DialogApplyButton
                    if status in {"completed", "success", "ready"}
                    else QStyle.StandardPixmap.SP_MessageBoxWarning
                    if status not in {"queued", "running", "starting"}
                    else QStyle.StandardPixmap.SP_MediaPlay
                )
                status_item.setIcon(self.style().standardIcon(icon))
            updated_item = self.table.item(row, 4)
            completeness_item = self.table.item(row, 3)
            if completeness_item:
                exact = completeness_item.text()
                completeness_item.setData(Qt.ItemDataRole.UserRole, exact)
                completeness_item.setText(_friendly(exact))
                completeness_item.setToolTip(exact)
            if updated_item:
                exact = updated_item.text()
                updated_item.setData(Qt.ItemDataRole.UserRole, exact)
                updated_item.setText(_friendly_time(exact))
                updated_item.setToolTip(exact)

    def _fallback_matches(self, record: dict[str, Any], run_dir: Path) -> bool:
        manifest = _read_json(run_dir / "results" / "manifest.json")
        values = {
            self.status_filter: record.get("state") or record.get("termination_category"),
            self.model_filter: record.get("kinetic_model")
            or manifest.get("input_snapshot", {}).get("kinetics_setup", {}).get("model"),
            self.workflow_filter: record.get("workflow_mode")
            or manifest.get("solver_configuration", {}).get("workflow", {}).get("mode"),
            self.study_filter: record.get("study_id"),
            self.schema_filter: manifest.get("output_schema_version"),
        }
        if any(widget.text().strip() and widget.text().strip() != str(value or "") for widget, value in values.items()):
            return False
        started = str(record.get("started_at_utc") or "")
        return not (
            self.started_after.text().strip() and started < self.started_after.text().strip()
            or self.started_before.text().strip() and started > self.started_before.text().strip()
        )

    def _show_evidence(self, row: int, _column: int, _old_row: int, _old_column: int) -> None:
        if row < 0 or self.table.item(row, 5) is None:
            self.selected_run_heading.setText("Select a run to inspect its saved evidence")
            self.report_button.setEnabled(False)
            return
        run_dir = Path(self.table.item(row, 5).text())
        run = _read_json(run_dir / "run_record.json")
        diagnostics = _read_json(run_dir / "results" / "diagnostics.json")
        manifest = _read_json(run_dir / "results" / "manifest.json")
        run_id = run.get("run_id") or self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        case_name = run.get("case_id") or run.get("case_name") or self.table.item(row, 1).text()
        self.selected_run_heading.setText(f"{case_name} · run {_short_id(run_id)}")
        self.selected_run_heading.setToolTip(f"Run ID: {run_id}\nSaved package: {run_dir}")
        summary_rows = [
            ["Outcome", run.get("state") or run.get("termination_category")],
            ["Output completeness", (run.get("output_completeness") or {}).get("status")],
            ["Last accepted time (s)", diagnostics.get("last_accepted_time_s")],
            ["Requested duration (s)", diagnostics.get("requested_duration_s")],
            ["Output schema", manifest.get("output_schema_version") or diagnostics.get("output_schema_version")],
        ]
        diagnosis_rows = [
            ["Reason", run.get("status_reason") or diagnostics.get("termination_reason") or "None recorded"],
            ["Failure stage", run.get("failed_stage") or diagnostics.get("failed_stage") or "None recorded"],
            ["Warnings", "; ".join(map(str, diagnostics.get("warnings", []))) or "None"],
        ]
        provenance_rows = [
            ["Scientific fingerprint", run.get("scientific_fingerprint")],
            ["Source case SHA-256", (run.get("source_case") or {}).get("sha256")],
            ["Final snapshot SHA-256", run.get("snapshot_sha256")],
            ["Run directory", run_dir],
        ]
        _fill(self.summary_evidence, summary_rows)
        _fill(self.diagnosis_evidence, diagnosis_rows)
        _fill(self.provenance_evidence, provenance_rows)
        sources = [
            path
            for path in (
                run_dir / "run_record.json",
                run_dir / "results" / "diagnostics.json",
                run_dir / "results" / "manifest.json",
            )
            if path.is_file()
        ]
        _set_action_state(
            self.report_button,
            bool(sources),
            ready="Generate a report from the selected run's saved artifacts.",
            blocked="Select a run with saved run, diagnostics, or manifest artifacts.",
        )
        self.report_evidence.setText(
            f"{len(sources)} authoritative saved artifact{'s' if len(sources) != 1 else ''} available."
            if sources
            else "No authoritative report source artifacts are available for this selection."
        )

    def _activated(self, item: QTableWidgetItem) -> None:
        path = self.table.item(item.row(), 5)
        if path:
            self.run_selected.emit(path.text())

    def _report(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        run_dir = Path(self.table.item(row, 5).text())
        sources = [
            str(path)
            for path in (
                run_dir / "run_record.json",
                run_dir / "results" / "diagnostics.json",
                run_dir / "results" / "manifest.json",
            )
            if path.is_file()
        ]
        if not sources:
            self.report_button.setEnabled(False)
            return
        report_type = _combo_value(self.report_type)
        output = _new_output_directory(self, f"New {report_type} report directory", f"{report_type}-report")
        if output:
            self.report_requested.emit(
                {
                    "report_type": report_type,
                    "output_dir": output,
                    "sources": sources,
                }
            )


class ExplorePage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("Explore")
        self.package: ResultPackage | None = None
        self.package_path = QLineEdit()
        self.package_path.setAccessibleName("Result package path")
        self.package_path.setVisible(False)
        self.package_name = QLabel("No result package selected")
        self.package_name.setObjectName("cardTitle")
        self.open_button = QPushButton("Open result package")
        self.open_button.setAccessibleName("Open result package")
        self.export_button = QPushButton("Export displayed data")
        self.export_button.setAccessibleName("Export displayed plot data")
        self.figure_button = QPushButton("Save PNG or SVG")
        self.figure_button.setAccessibleName("Save the visible plot as PNG or SVG")
        self.reset_button = QPushButton("Canonical view")
        self.reset_button.setAccessibleName("Restore canonical axes and full data range")
        self.copy_button = QPushButton("Copy selected values")
        self.copy_button.setAccessibleName("Copy selected exact table values")
        self.variable_search = QLineEdit()
        self.variable_search.setPlaceholderText("Filter saved variables")
        self.variable_search.setAccessibleName("Filter saved result variables")
        self.result_group = QComboBox()
        self.result_group.addItems(["All groups", "Aqueous state", "Minerals", "Saturation", "Kinetics", "Other"])
        self.result_group.setAccessibleName("Result quantity group")
        self.quantity = QComboBox()
        self.quantity.setAccessibleName("Saved result quantity")
        self.time_unit = QComboBox()
        _set_combo_options(
            self.time_unit,
            [("Seconds", "seconds"), ("Days", "days")],
        )
        self.time_unit.setAccessibleName("Displayed time unit")
        self.y_log = QCheckBox("Logarithmic y-axis")
        self.y_log.setAccessibleName("Logarithmic y-axis")
        self.time_log = QCheckBox("Logarithmic time axis")
        self.time_log.setAccessibleName("Logarithmic time axis")
        self.incomplete = StatusLabel("Result completeness")
        self.summary = QLabel("Select a saved result package. No Reaktoro properties are recalculated here.")
        self.summary.setWordWrap(True)
        self.summary.setAccessibleName("Written result summary")
        self.data_notice = QLabel("The table contains the exact values displayed in the plot.")
        self.data_notice.setAccessibleName("Plot display and downsampling notice")
        self.data_notice.setVisible(False)
        self._visible_frame = None
        self._display_frame = None
        self._descriptors = {}
        self.plot = pg.PlotWidget()
        self.plot.setAccessibleName("Interactive saved-result plot")
        _configure_plot(self.plot)
        legend = self.plot.addLegend()
        legend.setBrush(pg.mkBrush("#ffffff"))
        legend.setPen(pg.mkPen("#a9b7c8"))
        self.plot_summary = QLabel("No plotted quantity")
        self.plot_summary.setWordWrap(True)
        self.plot_summary.setAccessibleName("Plot summary")
        self.cursor_value = QLabel("Cursor: move over the plot to inspect the nearest saved state")
        self.cursor_value.setAccessibleName("Nearest saved plot value")
        self.cursor_value.setVisible(False)
        self.table = _table("Accessible plot data", ["time_s", "value"])
        self.overview = _table("Run overview and provenance", ["Evidence", "Recorded value"])
        self.artifacts = _table("Raw result artifact inventory", ["Artifact", "Bytes", "SHA-256 evidence"])
        self.saved_table_select = QComboBox()
        self.saved_table_select.setAccessibleName("Saved result table")
        self.saved_table_notice = QLabel("Select a saved CSV table; source values are shown without recalculation.")
        self.saved_table_notice.setAccessibleName("Saved result table display notice")
        self.saved_table = _table("Accessible saved result table", ["No table loaded"])
        saved_table_page = QWidget()
        saved_table_layout = QVBoxLayout(saved_table_page)
        saved_table_layout.addWidget(self.saved_table_select)
        saved_table_layout.addWidget(self.saved_table_notice)
        saved_table_layout.addWidget(self.saved_table)
        self.numerical = _table("Saved solver attempts", ["No solver history loaded"])
        self.numerical_notice = QLabel("Numerical evidence is read from saved solver_history.csv; rejected attempts remain separate rows.")
        numerical_page = QWidget()
        numerical_layout = QVBoxLayout(numerical_page)
        numerical_layout.addWidget(self.numerical_notice)
        numerical_layout.addWidget(self.numerical)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.overview, "Overview")
        plot_page = QWidget()
        plot_layout = QVBoxLayout(plot_page)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        plot_layout.addWidget(self.plot_summary)
        plot_layout.addWidget(self.plot, 1)
        plot_layout.addWidget(self.cursor_value)
        self.tabs.addTab(plot_page, "Plot")
        self.tabs.addTab(self.table, "Data table")
        self.tabs.addTab(numerical_page, "Numerical")
        self.tabs.addTab(saved_table_page, "Audit tables")
        self.tabs.addTab(self.artifacts, "Artifacts")
        self.result_stack = QStackedWidget()
        self.empty_state = EmptyState(
            "Open a result package",
            "Choose a saved package to inspect written data and provenance. Nothing is recalculated.",
        )
        self.result_stack.addWidget(self.empty_state)
        self.result_stack.addWidget(self.tabs)
        browser, browser_layout = section_card(
            "Quantity browser", "Select only quantities written to the saved package."
        )
        browser.setMinimumWidth(230)
        browser.setMaximumWidth(310)
        browser_layout.addWidget(QLabel("Search"))
        browser_layout.addWidget(self.variable_search)
        browser_layout.addWidget(QLabel("Scientific group"))
        browser_layout.addWidget(self.result_group)
        browser_layout.addWidget(QLabel("Quantity"))
        browser_layout.addWidget(self.quantity)
        browser_layout.addWidget(QLabel("Time display"))
        browser_layout.addWidget(self.time_unit)
        axis_options = QWidget()
        axis_layout = QVBoxLayout(axis_options)
        axis_layout.setContentsMargins(0, 0, 0, 0)
        axis_layout.addWidget(self.y_log)
        axis_layout.addWidget(self.time_log)
        browser_layout.addWidget(Disclosure("Axis options", axis_options))
        browser_layout.addStretch(1)
        self.quantity_browser = QScrollArea()
        self.quantity_browser.setWidgetResizable(True)
        self.quantity_browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.quantity_browser.setFrameShape(QFrame.Shape.NoFrame)
        self.quantity_browser.setMinimumWidth(230)
        self.quantity_browser.setMaximumWidth(310)
        self.quantity_browser.setWidget(browser)
        self.quantity_browser.setVisible(False)
        result_splitter = QSplitter(Qt.Orientation.Horizontal)
        result_splitter.setChildrenCollapsible(False)
        result_splitter.addWidget(self.quantity_browser)
        result_splitter.addWidget(self.result_stack)
        result_splitter.setSizes([260, 900])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        summary_card, summary_layout = section_card("Selected run")
        package_row = QHBoxLayout()
        package_row.addWidget(self.package_name, 1)
        package_row.addWidget(self.open_button)
        summary_layout.addLayout(package_row)
        summary_layout.addWidget(self.incomplete)
        summary_layout.addWidget(self.summary)
        layout.addWidget(summary_card)
        self.view_actions = action_bar(self.reset_button)
        self.export_actions = action_bar(
            self.export_button, self.figure_button, self.copy_button
        )
        actions = QHBoxLayout()
        actions.addWidget(self.view_actions)
        actions.addStretch(1)
        actions.addWidget(self.export_actions)
        layout.addLayout(actions)
        layout.addWidget(self.data_notice)
        layout.addWidget(result_splitter, 1)
        for button in (
            self.reset_button,
            self.export_button,
            self.figure_button,
            self.copy_button,
        ):
            button.setEnabled(False)
        self.view_actions.setVisible(False)
        self.export_actions.setVisible(False)
        self.open_button.clicked.connect(self.choose_package)
        self.quantity.currentIndexChanged.connect(lambda _index: self.show_quantity())
        self.variable_search.textChanged.connect(self._refresh_quantity_choices)
        self.result_group.currentTextChanged.connect(self._refresh_quantity_choices)
        self.time_unit.currentIndexChanged.connect(lambda _index: self._redraw_current())
        self.export_button.clicked.connect(self.export_displayed_data)
        self.figure_button.clicked.connect(self.save_figure)
        self.copy_button.clicked.connect(self.copy_selected_values)
        self.reset_button.clicked.connect(self.canonical_view)
        self.saved_table_select.currentTextChanged.connect(self.show_saved_table)
        self.y_log.toggled.connect(lambda checked: self.plot.setLogMode(y=checked))
        self.time_log.toggled.connect(lambda checked: self.plot.setLogMode(x=checked))
        self.plot.scene().sigMouseMoved.connect(self._cursor_moved)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.setFocusProxy(self.open_button)

    def choose_package(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Open result package", self.package_path.text())
        if path:
            self.load_package(path)

    def load_package(self, path: str | Path) -> None:
        self.package = ResultPackage(path)
        self.package_path.setText(str(Path(path).resolve()))
        run = self.package.run_record
        case_name = run.get("case_id") or run.get("case_name") or Path(path).parent.name
        run_id = self.package.run_id
        self.package_name.setText(f"{case_name} · run {_short_id(run_id)}")
        self.package_name.setToolTip(f"Run ID: {run_id}\nPackage: {Path(path).resolve()}")
        self.result_stack.setCurrentIndex(1)
        self.quantity_browser.setVisible(True)
        self.data_notice.setVisible(False)
        self.view_actions.setVisible(False)
        self.export_actions.setVisible(False)
        for button in (
            self.reset_button,
            self.export_button,
            self.figure_button,
            self.copy_button,
        ):
            button.setEnabled(False)
        status = self.package.status
        text = f"{status.reason}; output {status.output_completeness}"
        icon = (
            QStyle.StandardPixmap.SP_DialogApplyButton
            if status.interpretation_supported
            else QStyle.StandardPixmap.SP_MessageBoxWarning
        )
        self.incomplete.set_status(text, icon)
        requested = self.package.manifest.get("time_semantics", {}).get("duration_s")
        accepted = self.package.diagnostics.get("final_time_reached_s")
        self.summary.setText(
            f"Completion: {_friendly(self.package.run_record.get('state'))} · "
            f"output {_friendly(status.output_completeness)} · accepted "
            f"{accepted if accepted is not None else 'not recorded'} s of "
            f"{requested if requested is not None else 'not recorded'} s requested."
        )
        self._load_evidence()
        try:
            self._descriptors = self.package.quantity_descriptors()
        except Exception as error:
            self.summary.setText(f"Result interpretation unavailable: {error}")
            self._descriptors = {}
        self._refresh_quantity_choices()
        if self.quantity.count():
            self.show_quantity()

    def show_quantity(self, _display_text: str = "") -> bool:
        column = _combo_value(self.quantity)
        if not self.package or not column:
            return False
        try:
            descriptors = self.package.quantity_descriptors()
            descriptor = descriptors[column]
            frame = next(
                self.package.iter_table(
                    "timeseries.csv",
                    columns=["time_s", column],
                    chunksize=10_000,
                    allow_incomplete=True,
                )
            )
        except (FileNotFoundError, KeyError, StopIteration, ValueError) as error:
            self.summary.setText(f"No plottable saved data are available: {error}")
            return False
        self._visible_frame = frame
        self._render_visible()
        available = self._display_frame is not None
        self.data_notice.setVisible(available)
        self.view_actions.setVisible(available)
        self.export_actions.setVisible(available)
        for button in (
            self.reset_button,
            self.export_button,
            self.figure_button,
            self.copy_button,
        ):
            button.setEnabled(available)
        return available

    def _render_visible(self) -> None:
        if self._visible_frame is None:
            return
        column = _combo_value(self.quantity)
        if not column or column not in self._visible_frame:
            return
        frame = self._visible_frame
        descriptor = self._descriptors[column]
        time_column = "time_days" if _combo_value(self.time_unit) == "days" else "time_s"
        displayed = frame[["time_s", column]].copy()
        if time_column == "time_days":
            displayed["time_s"] = displayed["time_s"] / 86_400.0
        displayed.columns = [time_column, "value"]
        self._display_frame = displayed
        _fill(self.table, displayed.astype(object).values.tolist())
        self.table.setHorizontalHeaderLabels([time_column, "value"])
        self.plot.clear()
        self.plot.plot(
            displayed[time_column].to_numpy(),
            displayed["value"].to_numpy(),
            name=descriptor.label,
            pen=pg.mkPen("#2563eb", width=2),
        )
        marker_step = max(1, (len(displayed) + 23) // 24)
        markers = displayed.iloc[::marker_step]
        if len(displayed) and markers.index[-1] != displayed.index[-1]:
            markers = pd.concat([markers, displayed.tail(1)])
        self.plot.plot(
            markers[time_column].to_numpy(),
            markers["value"].to_numpy(),
            pen=None,
            symbol="o",
            symbolSize=7,
            symbolPen=pg.mkPen("#1d4ed8", width=1.5),
            symbolBrush=pg.mkBrush("#ffffff"),
        )
        self.plot.setLabel(
            "bottom", "Time (days)" if time_column == "time_days" else "Time (seconds)"
        )
        self.plot.setLabel("left", descriptor.label, units=descriptor.unit)
        self.plot.getPlotItem().setTitle(_quantity_label(column, descriptor))
        self.y_log.setChecked(False)
        self.time_log.setChecked(False)
        self.y_log.setEnabled(y_log_allowed(descriptor, frame[column]))
        self.y_log.setToolTip("Disabled for pH, saturation index, signed, zero, or negative values")
        self.time_log.setEnabled(time_log_allowed(displayed[time_column]))
        self.data_notice.setText(
            "Displayed and tabulated the first 10,000 accepted-state rows; the source artifact is unchanged and this export is labelled as the displayed subset."
            if len(frame) == 10_000
            else f"Displayed and tabulated all {len(frame)} accepted-state rows."
        )
        values = pd.to_numeric(displayed["value"], errors="coerce").dropna()
        times = pd.to_numeric(displayed[time_column], errors="coerce").dropna()
        unit = descriptor.unit or "unitless"
        if len(values) and len(times):
            plot_summary = (
                f"{len(displayed)} saved states; {time_column} {times.min():.6g} to "
                f"{times.max():.6g}; {descriptor.label} {values.min():.6g} to "
                f"{values.max():.6g} {unit}."
            )
        else:
            plot_summary = f"{len(displayed)} saved states; no finite range is available."
        self.plot_summary.setText(plot_summary)
        self.plot.setAccessibleDescription(
            f"Line plot of {_quantity_label(column, descriptor)}. {plot_summary} "
            "Exact saved values are available in the Data table tab."
        )

    def _refresh_quantity_choices(self) -> None:
        query = self.variable_search.text().casefold()
        selected_group = self.result_group.currentText()
        current = _combo_value(self.quantity)
        names = []
        for name, descriptor in self._descriptors.items():
            if name in {"time_s", "time_days"}:
                continue
            group = _quantity_group(name)
            text = f"{name} {descriptor.label} {descriptor.scientific_meaning}".casefold()
            if (selected_group == "All groups" or selected_group == group) and query in text:
                names.append(name)
        self.quantity.blockSignals(True)
        self.quantity.clear()
        for name in names:
            self.quantity.addItem(_quantity_label(name, self._descriptors[name]), name)
        current_index = self.quantity.findData(current)
        if current_index >= 0:
            self.quantity.setCurrentIndex(current_index)
        self.quantity.blockSignals(False)
        if self.quantity.count():
            self.show_quantity()

    def _redraw_current(self) -> None:
        self._render_visible()

    def canonical_view(self) -> None:
        self.y_log.setChecked(False)
        self.time_log.setChecked(False)
        seconds = self.time_unit.findData("seconds", Qt.ItemDataRole.UserRole)
        if seconds >= 0:
            self.time_unit.setCurrentIndex(seconds)
        self.plot.enableAutoRange()

    def _cursor_moved(self, point: object) -> None:
        if self._display_frame is None or not self.plot.sceneBoundingRect().contains(point):
            return
        view_point = self.plot.plotItem.vb.mapSceneToView(point)
        time_column = self._display_frame.columns[0]
        times = self._display_frame[time_column]
        nearest = (times - view_point.x()).abs().idxmin()
        self.cursor_value.setText(
            f"Nearest saved state: {time_column}={times.loc[nearest]:.12g}; "
            f"{_combo_value(self.quantity)}={self._display_frame.loc[nearest, 'value']:.12g}"
        )

    def _tab_changed(self, index: int) -> None:
        self.cursor_value.setVisible(
            self.package is not None and self.tabs.tabText(index) == "Plot"
        )

    def _load_evidence(self) -> None:
        if self.package is None:
            return
        package = self.package
        trace = package.manifest.get("traceability", {})
        time = package.manifest.get("time_semantics", {})
        solver = package.manifest.get("solver_configuration", {})
        diagnostics = package.diagnostics
        _fill(
            self.overview,
            [
                ["Run ID", package.run_id],
                ["Managed state", package.run_record.get("state") or "unmanaged"],
                ["Interpretation supported", package.status.interpretation_supported],
                ["Output completeness", package.status.output_completeness],
                ["Output schema", package.schema_version],
                ["Scientific fingerprint", package.scientific_fingerprint],
                ["Source case SHA-256", (package.run_record.get("source_case") or {}).get("sha256")],
                ["Snapshot SHA-256", package.run_record.get("snapshot_sha256") or trace.get("source_config_sha256")],
                ["Database", trace.get("database_path") or diagnostics.get("database_value")],
                ["Database SHA-256", trace.get("database_sha256") or diagnostics.get("database_sha256")],
                ["Kinetic model", trace.get("kinetic_model") or diagnostics.get("kinetic_model")],
                ["Kinetic parameters SHA-256", trace.get("kinetic_parameter_sha256") or diagnostics.get("kinetic_parameter_sha256")],
                ["Workflow", (solver.get("workflow") or {}).get("mode") or diagnostics.get("workflow_mode")],
                ["Requested duration (s)", time.get("duration_s") or diagnostics.get("requested_duration_s")],
                ["Last accepted time (s)", diagnostics.get("final_time_reached_s")],
                ["Accepted attempts", diagnostics.get("number_of_accepted_steps")],
                ["Rejected attempts", diagnostics.get("number_of_rejected_steps")],
                ["Warnings", "; ".join(map(str, diagnostics.get("warnings", []))) or "None"],
                ["Scientific boundary", "Batch outputs are not reactive transport or fracture-sealing evidence."],
            ],
        )
        artifact_rows = []
        inventory = package.inventory()
        for relative in inventory:
            artifact = package.path / relative
            digest = package.artifact_sha256(relative) if relative in {"manifest.json", "diagnostics.json"} else "Available on explicit export"
            artifact_rows.append([relative, artifact.stat().st_size, digest])
        _fill(self.artifacts, artifact_rows)
        saved_tables = [relative for relative in inventory if relative.casefold().endswith(".csv")]
        self.saved_table_select.blockSignals(True)
        self.saved_table_select.clear()
        self.saved_table_select.addItems(saved_tables)
        self.saved_table_select.blockSignals(False)
        if saved_tables:
            self.show_saved_table(saved_tables[0])
        else:
            self.saved_table.setColumnCount(1)
            self.saved_table.setHorizontalHeaderLabels(["Saved table evidence"])
            _fill(self.saved_table, [["No saved CSV tables were written"]])
        available_audits = [
            name
            for name in saved_tables
            if name not in {"timeseries.csv", "solver_history.csv", "aqueous_summary.csv", "mineral_summary.csv"}
        ]
        self.overview.insertRow(self.overview.rowCount())
        row = self.overview.rowCount() - 1
        self.overview.setItem(row, 0, QTableWidgetItem("Available saved audit tables"))
        self.overview.setItem(row, 1, QTableWidgetItem(", ".join(available_audits) or "None configured"))
        solver_history = package.path / "solver_history.csv"
        if solver_history.is_file() and package.supported:
            frame = package.read_table("solver_history.csv", nrows=1_000, allow_incomplete=True)
            self.numerical.setColumnCount(len(frame.columns))
            self.numerical.setHorizontalHeaderLabels(list(frame.columns))
            _fill(self.numerical, frame.astype(object).values.tolist())
            self.numerical_notice.setText(
                "Showing the first 1,000 saved solver attempts; accepted and rejected attempts remain separate rows."
                if len(frame) == 1_000
                else f"Showing all {len(frame)} saved solver attempts; accepted and rejected attempts remain separate rows."
            )
        else:
            self.numerical.setColumnCount(1)
            self.numerical.setHorizontalHeaderLabels(["Numerical evidence"])
            _fill(self.numerical, [["solver_history.csv was not written"]])

    def show_saved_table(self, relative: str) -> None:
        if not self.package or not relative:
            return
        try:
            frame = self.package.read_table(relative, nrows=1_001, allow_incomplete=True)
        except (FileNotFoundError, ValueError) as error:
            self.saved_table.setColumnCount(1)
            self.saved_table.setHorizontalHeaderLabels(["Saved table evidence"])
            _fill(self.saved_table, [[str(error)]])
            self.saved_table_notice.setText("Saved table interpretation is unavailable; the raw artifact remains unchanged.")
            return
        truncated = len(frame) > 1_000
        frame = frame.head(1_000)
        self.saved_table.setColumnCount(len(frame.columns))
        self.saved_table.setHorizontalHeaderLabels(list(frame.columns))
        _fill(self.saved_table, frame.astype(object).values.tolist())
        self.saved_table_notice.setText(
            "Showing the first 1,000 saved rows; open the raw artifact for the complete table. No values were recalculated."
            if truncated
            else f"Showing all {len(frame)} saved rows without recalculation."
        )

    def export_displayed_data(self) -> None:
        if self._display_frame is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export displayed data", "displayed_data.csv", "CSV (*.csv)"
        )
        if path:
            self._display_frame.to_csv(path, index=False)

    def save_figure(self) -> None:
        path, selected_filter = QFileDialog.getSaveFileName(
            self, "Save visible plot", "result_plot.png", "PNG (*.png);;SVG (*.svg)"
        )
        if not path:
            return
        if selected_filter.startswith("SVG") and not path.casefold().endswith(".svg"):
            path += ".svg"
        elif selected_filter.startswith("PNG") and not path.casefold().endswith(".png"):
            path += ".png"
        exporter = (
            pg.exporters.SVGExporter(self.plot.plotItem)
            if path.casefold().endswith(".svg")
            else pg.exporters.ImageExporter(self.plot.plotItem)
        )
        exporter.export(path)

    def copy_selected_values(self) -> None:
        ranges = self.table.selectedRanges()
        if not ranges:
            return
        selected = ranges[0]
        lines = []
        for row in range(selected.topRow(), selected.bottomRow() + 1):
            lines.append(
                "\t".join(
                    self.table.item(row, column).text()
                    for column in range(selected.leftColumn(), selected.rightColumn() + 1)
                )
            )
        QApplication.clipboard().setText("\n".join(lines))


class ComparePage(QWidget):
    check_requested = Signal(dict)
    export_requested = Signal(dict)
    report_requested = Signal(dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("Compare")
        self.run_paths = QListWidget()
        self.run_paths.setAccessibleName("Runs selected for comparison")
        self.run_paths.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.run_paths.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.run_paths.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
        self.run_paths.setMinimumWidth(0)
        self.mode = QComboBox()
        _set_combo_options(
            self.mode,
            [
                ("Native accepted grids", "native_accepted_grids"),
                ("Initial state", "initial_state"),
                ("Final state", "final_state"),
                ("Exact common timestamps", "exact_common_timestamps"),
            ],
        )
        self.mode.setAccessibleName("Comparison time alignment")
        self.mode.setAccessibleDescription("Native grids are the default; interpolation is never implicit")
        self.quantity = QComboBox()
        self.quantity.setAccessibleName("Comparison quantity")
        self.tolerance = QDoubleSpinBox()
        self.tolerance.setRange(0.0, 1.0e30)
        self.tolerance.setDecimals(9)
        self.tolerance.setSuffix(" s tolerance")
        self.tolerance.setAccessibleName("Exact common timestamp tolerance")
        self.tolerance.setEnabled(False)
        self.tolerance.setVisible(False)
        self.add_button = QPushButton("Add package")
        self.add_button.setMinimumWidth(110)
        self.remove_button = QPushButton("Remove selected")
        self.load_button = QPushButton("Open saved")
        self.check_button = QPushButton("Check compatibility")
        self.save_button = QPushButton("Save comparison")
        self.report_button = QPushButton("Report")
        for button in (
            self.add_button,
            self.remove_button,
            self.load_button,
            self.check_button,
            self.save_button,
            self.report_button,
        ):
            button.setAccessibleName(button.text())
        self.status = StatusLabel("Comparison compatibility")
        self._compatible = False
        self._report_sources: list[str] = []
        self.table = _table("Comparison compatibility table", ["Run", "Quantity", "Unit", "Status"])
        self.evidence = _table(
            "Comparison provenance and native-domain evidence",
            ["Category", "Field", "Run", "Recorded value"],
        )
        self.cost = _table(
            "Comparison solver cost and QC matrix",
            ["Run", "Accepted", "Rejected", "Attempts", "Final time (s)", "Completeness"],
        )
        self.data = _table("Accessible comparison plot data", ["Run", "time_s", "Value"])
        self.plot = pg.PlotWidget()
        self.plot.setAccessibleName("Interactive comparison overlay")
        _configure_plot(self.plot)
        self.plot.addLegend()
        self.overlay_stack = QStackedWidget()
        self.overlay_empty = EmptyState(
            "No comparison data yet",
            "Select at least two compatible result packages, choose a shared quantity and alignment, then save or open a reproducible comparison.",
        )
        self.overlay_stack.addWidget(self.overlay_empty)
        self.overlay_stack.addWidget(self.plot)
        self.tabs = QTabWidget()
        self.tabs.setAccessibleName("Comparison views")
        self.tabs.addTab(self.overlay_stack, "Overlay")
        self.tabs.addTab(self.data, "Data table")
        self.tabs.addTab(self.table, "Compatibility")
        self.tabs.addTab(self.cost, "QC")
        self.tabs.addTab(self.evidence, "Provenance")
        self.comparison_summary = QLabel(
            "No comparison result is loaded. Exact values will remain available in the Data table tab."
        )
        self.comparison_summary.setWordWrap(True)
        self.comparison_summary.setAccessibleName("Comparison plot summary")
        result_pane = QWidget()
        result_layout = QVBoxLayout(result_pane)
        result_layout.setContentsMargins(0, 0, 0, 0)
        result_layout.addWidget(self.comparison_summary)
        result_layout.addWidget(self.tabs, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        workflow = QWidget()
        configuration = QVBoxLayout(workflow)
        configuration.setContentsMargins(0, 0, 0, 0)
        configuration.setSpacing(10)
        configuration.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        workflow.setMinimumSize(0, 0)
        workflow.setMaximumWidth(310)
        workflow.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        selected_card, selected_layout = section_card(
            "1 · Selected packages", "Full paths remain in tooltips and provenance."
        )
        selected_layout.addWidget(self.run_paths)
        selected_actions = QVBoxLayout()
        selected_actions.setContentsMargins(0, 0, 0, 0)
        selected_actions.setSpacing(8)
        selected_actions.addWidget(self.add_button)
        selected_actions.addWidget(self.load_button)
        selected_actions.addWidget(self.remove_button)
        selected_layout.addLayout(selected_actions)
        settings_card, settings_layout = section_card(
            "2 · Quantity and alignment", "Interpolation is never implicit."
        )
        form = QFormLayout()
        form.addRow("Shared quantity", self.quantity)
        form.addRow("Alignment", self.mode)
        self.tolerance_label = QLabel("Timestamp tolerance")
        form.addRow(self.tolerance_label, self.tolerance)
        settings_layout.addLayout(form)
        settings_layout.addWidget(self.check_button)
        compatibility_card, compatibility_layout = section_card(
            "3 · Compatibility", "Blocking evidence appears before export is enabled."
        )
        compatibility_layout.addWidget(self.status)
        export_card, export_layout = section_card(
            "4 · Export and report",
            "Exports are enabled only after compatibility is proven; reports use saved artifacts.",
        )
        export_layout.addWidget(action_bar(self.save_button, self.report_button))
        configuration.addWidget(selected_card)
        configuration.addWidget(settings_card)
        configuration.addWidget(compatibility_card)
        configuration.addWidget(export_card)
        configuration.addStretch(1)
        workflow_scroll = QScrollArea()
        workflow_scroll.setWidgetResizable(True)
        workflow_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        workflow_scroll.setFrameShape(QFrame.Shape.NoFrame)
        workflow_scroll.setMinimumWidth(330)
        workflow_scroll.setMaximumWidth(430)
        workflow_scroll.setWidget(workflow)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(workflow_scroll)
        splitter.addWidget(result_pane)
        splitter.setSizes([370, 850])
        layout.addWidget(splitter, 1)
        self.add_button.clicked.connect(self._add)
        self.remove_button.clicked.connect(self._remove_selected)
        self.load_button.clicked.connect(self._open_saved)
        self.check_button.clicked.connect(self.check_compatibility)
        self.save_button.clicked.connect(self.export)
        self.report_button.clicked.connect(self._report)
        self.mode.currentIndexChanged.connect(self._configuration_changed)
        self.quantity.currentIndexChanged.connect(self._configuration_changed)
        self.tolerance.valueChanged.connect(self._configuration_changed)
        self.run_paths.currentRowChanged.connect(lambda _row: self._refresh_actions())
        self.save_button.setEnabled(False)
        self.report_button.setEnabled(False)
        self._configuration_changed()
        self.setFocusProxy(self.add_button)

    def _add(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select result package")
        if path:
            self.add_package(path)

    def add_package(self, path: str | Path) -> None:
        resolved = Path(path).resolve()
        package = ResultPackage(resolved)
        if str(resolved) in self._package_paths():
            return
        list_item = QListWidgetItem(self._run_label(resolved))
        list_item.setData(Qt.ItemDataRole.UserRole, str(resolved))
        list_item.setToolTip(f"Run ID: {package.run_id}\nPackage: {resolved}")
        self.run_paths.addItem(list_item)
        self._refresh_quantities()
        self.run_paths.setCurrentItem(list_item)
        self._configuration_changed()

    def _remove_selected(self) -> None:
        row = self.run_paths.currentRow()
        if row >= 0:
            self.run_paths.takeItem(row)
            self._refresh_quantities()
            self._configuration_changed()

    @staticmethod
    def _run_label(path: str | Path) -> str:
        resolved = Path(path).resolve()
        try:
            package = ResultPackage(resolved)
            case_name = (
                package.run_record.get("case_id")
                or package.run_record.get("case_name")
                or resolved.parent.name
            )
            return f"{case_name} · {_short_id(package.run_id)}"
        except Exception:
            return resolved.parent.name

    def _package_paths(self) -> list[str]:
        return [
            str(self.run_paths.item(index).data(Qt.ItemDataRole.UserRole) or self.run_paths.item(index).text())
            for index in range(self.run_paths.count())
        ]

    def check_compatibility(self) -> None:
        quantity = _combo_value(self.quantity)
        packages = self._package_paths()
        if not quantity or len(packages) < 2:
            self.apply_compatibility(
                {"compatible": False, "errors": ["select a shared quantity and at least two runs"]}
            )
            return
        self._compatible = False
        self._refresh_actions()
        self.status.set_status(
            "Checking compatibility in a headless process",
            QStyle.StandardPixmap.SP_MessageBoxInformation,
        )
        self.check_requested.emit(
            {
                "quantity": quantity,
                "packages": packages,
                "mode": _combo_value(self.mode),
                "tolerance_s": self.tolerance.value(),
            }
        )

    def apply_compatibility(self, gate: dict[str, Any]) -> None:
        errors = list(gate.get("errors", []))
        sources = list(gate.get("sources", []))
        if any(str(source.get("run_id", "")).startswith("unmanaged:") for source in sources):
            errors.append("saved comparisons require durable managed run IDs")
        quantity = _combo_value(self.quantity)
        _fill(
            self.table,
            [
                [
                    _short_id(source.get("run_id")),
                    quantity,
                    source.get("unit") or "unavailable",
                    source.get("output_completeness"),
                ]
                for source in sources
            ],
        )
        domains = {item["source"]: item for item in gate.get("native_domains", [])}
        evidence_rows = []
        for source in sources:
            run_id = str(source.get("run_id"))
            domain = domains.get(run_id)
            evidence_rows.extend(
                [
                    ["Identity", "scientific_fingerprint", _short_id(run_id), gate.get("scientific_fingerprints", {}).get(run_id)],
                    ["Native domain", "time_s", _short_id(run_id), f"{domain['minimum_s']} to {domain['maximum_s']}" if domain else "Unavailable"],
                    ["Package", "path", _short_id(run_id), source.get("path")],
                ]
            )
        for category, differences in (
            ("Scientific input difference", gate.get("scientific_input_differences", {})),
            ("Provenance difference", gate.get("provenance_differences", {})),
        ):
            for field, by_run in differences.items():
                evidence_rows.extend([category, field, _short_id(run_id), value] for run_id, value in by_run.items())
        _fill(self.evidence, evidence_rows)
        _fill(
            self.cost,
            [
                [
                    _short_id(source.get("run_id")),
                    source.get("accepted_steps"),
                    source.get("rejected_steps"),
                    source.get("internal_attempts"),
                    source.get("final_time_s"),
                    source.get("output_completeness"),
                ]
                for source in sources
            ],
        )
        compatible = bool(gate.get("compatible")) and not errors
        self._compatible = compatible
        self.status.set_status(
            "Compatible; scientific-input and provenance differences are shown before export"
            if compatible
            else "Blocked: " + "; ".join(dict.fromkeys(errors)),
            QStyle.StandardPixmap.SP_DialogApplyButton
            if compatible
            else QStyle.StandardPixmap.SP_MessageBoxWarning,
        )
        self._refresh_actions()

    def export(self) -> None:
        if not self._compatible:
            return
        output = _new_output_directory(self, "New comparison output directory", "comparison")
        if output:
            self.export_requested.emit(
                {
                    "output_dir": output,
                    "quantity": _combo_value(self.quantity),
                    "packages": self._package_paths(),
                    "mode": _combo_value(self.mode),
                    "tolerance_s": self.tolerance.value(),
                }
            )

    def set_saved_artifacts(self, specification: str | Path, data: str | Path) -> bool:
        specification_path = Path(specification).resolve()
        data_path = Path(data).resolve()
        spec = _read_json(specification_path)
        quantity = (spec.get("selected_quantities") or [""])[0]
        if quantity:
            try:
                frame = pd.read_csv(data_path, nrows=100_000)
            except Exception as error:
                self.status.set_status(
                    f"Saved comparison exists, but its preview is unavailable: {error}",
                    QStyle.StandardPixmap.SP_MessageBoxWarning,
                )
                return False
            self.run_paths.blockSignals(True)
            self.run_paths.clear()
            for run_path in frame.get("run_path", pd.Series(dtype=str)).dropna().astype(str).unique():
                item = QListWidgetItem(self._run_label(run_path))
                item.setData(Qt.ItemDataRole.UserRole, run_path)
                item.setToolTip(f"Saved comparison source: {run_path}")
                self.run_paths.addItem(item)
            self.run_paths.blockSignals(False)
            self.quantity.blockSignals(True)
            _set_combo_options(self.quantity, [(quantity.replace("_", " "), quantity)], quantity)
            self.quantity.blockSignals(False)
            mode = str(spec.get("time_alignment_mode") or "native_accepted_grids")
            mode_index = self.mode.findData(mode, Qt.ItemDataRole.UserRole)
            if mode_index >= 0:
                self.mode.blockSignals(True)
                self.mode.setCurrentIndex(mode_index)
                self.mode.blockSignals(False)
            self._report_sources = [str(specification_path), str(data_path)]
            self._show_preview(frame, quantity, "recorded source unit")
            self.status.set_status(
                "Loaded saved comparison; recorded compatibility and provenance are available",
                QStyle.StandardPixmap.SP_DialogApplyButton,
            )
            self._refresh_actions()
        return True

    def _open_saved(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open comparison specification", "comparison_spec.json", "JSON (*.json)"
        )
        if not path:
            return
        spec = _read_json(Path(path))
        data = Path(path).with_name("comparison.csv")
        if not spec or not data.is_file():
            self.status.set_status(
                "Blocked: comparison specification or recorded CSV is unavailable",
                QStyle.StandardPixmap.SP_MessageBoxWarning,
            )
            return
        if not self.set_saved_artifacts(path, data):
            return
        self.status.set_status(
            f"Loaded saved comparison {spec.get('comparison_id', '')}",
            QStyle.StandardPixmap.SP_DialogApplyButton,
        )

    def _show_preview(self, frame: Any, quantity: str, unit: str) -> None:
        difference_columns = [
            name
            for name in (
                "absolute_difference_from_reference",
                "relative_difference_from_reference",
            )
            if name in frame.columns
        ]
        columns = ["run_path", "time_s", quantity, *difference_columns]
        self.data.setColumnCount(len(columns))
        self.data.setHorizontalHeaderLabels(
            ["Run", "time_s", "Value", *difference_columns]
        )
        displayed = frame[columns].head(10_000)
        rows = []
        for row in displayed.itertuples(index=False, name=None):
            values = list(row)
            values[0] = self._run_label(str(values[0]))
            rows.append(values)
        _fill(self.data, rows)
        self.data.setAccessibleDescription(
            f"Exact comparison values for {quantity} in {unit}; at most the first 10000 displayed rows"
        )
        self.plot.clear()
        self.overlay_stack.setCurrentIndex(1)
        symbols = ("o", "s", "t", "d", "+", "x", "star")
        colours = ("#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9", "#000000")
        styles = (
            Qt.PenStyle.SolidLine,
            Qt.PenStyle.DashLine,
            Qt.PenStyle.DotLine,
            Qt.PenStyle.DashDotLine,
            Qt.PenStyle.DashDotDotLine,
        )
        for index, (run_path, group) in enumerate(frame.groupby("run_path", sort=False)):
            plotted = group.iloc[:: max(1, len(group) // 5_000)]
            full_label = self._run_label(str(run_path))
            label = (
                full_label
                if len(full_label) <= 36
                else f"{full_label[:22]}…{full_label[-11:]}"
            )
            self.plot.plot(
                plotted["time_s"].to_numpy(),
                plotted[quantity].to_numpy(),
                name=label,
                pen=pg.mkPen(colours[index % len(colours)], width=2, style=styles[index % len(styles)]),
                symbol=symbols[index % len(symbols)],
                symbolSize=7,
            )
        self.plot.setLabel("bottom", "time", units="s")
        self.plot.setLabel("left", quantity, units=unit)
        self.plot.getPlotItem().setTitle(f"Comparison of {quantity}")
        run_count = frame["run_path"].nunique()
        time_values = pd.to_numeric(frame["time_s"], errors="coerce").dropna()
        summary = (
            f"{run_count} runs and {len(frame)} recorded rows; time {time_values.min():.6g} "
            f"to {time_values.max():.6g} s; quantity {quantity} in {unit}."
            if len(time_values)
            else f"{run_count} runs and {len(frame)} recorded rows; no finite time range is available."
        )
        self.comparison_summary.setText(summary)
        self.plot.setAccessibleDescription(
            f"Comparison overlay. {summary} Series use distinct line styles and symbols. "
            "Exact values are available in the Data table tab."
        )

    def _report(self) -> None:
        output = _new_output_directory(
            self, "New comparison report directory", "comparison-report"
        )
        if output and self._report_sources:
            self.report_requested.emit(
                {
                    "report_type": "comparison",
                    "output_dir": output,
                    "sources": self._report_sources,
                }
            )

    def _refresh_quantities(self) -> None:
        shared: set[str] | None = None
        for path in self._package_paths():
            package = ResultPackage(path)
            values = set(package.quantity_descriptors()) if package.supported else set()
            values -= {"time_s", "time_days"}
            shared = values if shared is None else shared & values
        current = _combo_value(self.quantity)
        descriptors: dict[str, Any] = {}
        paths = self._package_paths()
        if paths:
            package = ResultPackage(paths[0])
            if package.supported:
                descriptors = package.quantity_descriptors()
        options = [
            (_quantity_label(name, descriptors.get(name)), name)
            for name in sorted(shared or ())
        ]
        _set_combo_options(self.quantity, options, current)

    def _configuration_changed(self, *_args: Any) -> None:
        had_result = (
            self.overlay_stack.currentWidget() is self.plot
            or bool(self._report_sources)
            or self.data.rowCount() > 0
        )
        exact = _combo_value(self.mode) == "exact_common_timestamps"
        self.tolerance.setVisible(exact)
        self.tolerance_label.setVisible(exact)
        self.tolerance.setEnabled(exact)
        self._compatible = False
        self._report_sources = []
        self.overlay_stack.setCurrentWidget(self.overlay_empty)
        self.plot.clear()
        self.plot.setAccessibleDescription("No current comparison data; configure and check compatibility first.")
        self.comparison_summary.setText(
            "Configuration changed. Check compatibility again before using comparison data."
            if had_result
            else "No comparison result is loaded. Exact values will appear in the Data table tab."
        )
        self.status.set_status(
            "Not checked for the current configuration",
            QStyle.StandardPixmap.SP_MessageBoxInformation,
        )
        for table in (self.data, self.table, self.cost, self.evidence):
            table.setRowCount(0)
        self._refresh_actions()

    def _refresh_actions(self) -> None:
        eligible = self.run_paths.count() >= 2 and bool(_combo_value(self.quantity))
        _set_action_state(
            self.check_button,
            eligible,
            ready="Check the selected packages, quantity, and alignment for compatibility.",
            blocked="Select at least two packages with a shared quantity.",
        )
        self.check_button.setVisible(eligible)
        self.remove_button.setEnabled(self.run_paths.currentRow() >= 0)
        _set_action_state(
            self.save_button,
            self._compatible,
            ready="Save the compatible comparison and its provenance.",
            blocked="Run a successful compatibility check for the current selection first.",
        )
        _set_action_state(
            self.report_button,
            bool(self._report_sources),
            ready="Generate a report from saved comparison artifacts.",
            blocked="Save or open comparison artifacts before reporting.",
        )


class StudiesPage(QWidget):
    study_requested = Signal(str)
    prepare_sample_requested = Signal(dict)
    dataset_requested = Signal(dict)
    report_requested = Signal(dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("Studies")
        self.baseline = QLineEdit()
        self.baseline.setAccessibleName("Verified baseline case")
        self.baseline.setReadOnly(True)
        self.specification = QLineEdit()
        self.specification.setAccessibleName("Versioned study specification")
        self.specification.setReadOnly(True)
        self.spec_editor = QPlainTextEdit()
        self.spec_editor.setAccessibleName("Study specification YAML editor")
        self.spec_editor.setAccessibleDescription(
            "Versioned YAML validated by the strict Qt-free StudySpec schema before atomic save"
        )
        self.open_spec_button = QPushButton("Open study specification")
        self.save_spec_button = QPushButton("Save study specification")
        self.validate_spec_button = QPushButton("Validate study specification")
        self.generate_button = QPushButton("Generate and preflight cases")
        self.generate_button.setToolTip(
            "Generate deterministically and full-preflight every case before it can enter the queue"
        )
        self.open_manifest_button = QPushButton("Open study manifest")
        self.prepare_sample_button = QPushButton("Prepare selected ready sample for queue")
        self.dataset_button = QPushButton("Assemble leakage-safe dataset")
        self.report_type = QComboBox()
        _set_combo_options(
            self.report_type,
            [("Study report", "study"), ("Dataset report", "dataset")],
        )
        self.report_type.setAccessibleName("Study or dataset report type")
        self.report_button = QPushButton("Generate selected derived report")
        for button in (
            self.open_spec_button,
            self.save_spec_button,
            self.validate_spec_button,
            self.generate_button,
            self.open_manifest_button,
            self.prepare_sample_button,
            self.dataset_button,
            self.report_button,
        ):
            button.setAccessibleName(button.text())
        self.status = StatusLabel("Study status")
        self.parameters = _table(
            "Study parameter definitions",
            ["Parameter", "YAML path", "Type", "Entered unit", "Canonical unit", "Distribution", "Constraints"],
        )
        self.packages = QListWidget()
        self.packages.setAccessibleName("Result packages selected for dataset assembly")
        self.add_package_button = QPushButton("Add dataset result package")
        self.add_package_button.setAccessibleName("Add dataset result package")
        self.dataset_type = QComboBox()
        _set_combo_options(
            self.dataset_type,
            [
                ("Final state", "final_state"),
                ("Fixed time", "fixed_time"),
                ("Time-dependent table", "time_dependent_tabular"),
                ("Trajectory", "trajectory"),
                ("Failure classification", "failure"),
            ],
        )
        self.dataset_type.setAccessibleName("Dataset type")
        self.dataset_source = QComboBox()
        _set_combo_options(
            self.dataset_source,
            [
                ("Explicit selected runs", "explicit_run_set"),
                ("Loaded study manifest", "loaded_study_manifest"),
            ],
        )
        self.dataset_source.setAccessibleName("Dataset source lineage")
        self.fixed_time = QDoubleSpinBox()
        self.fixed_time.setRange(0.0, 1.0e30)
        self.fixed_time.setDecimals(9)
        self.fixed_time.setSuffix(" s")
        self.fixed_time.setAccessibleName("Fixed dataset time in seconds")
        self.fixed_time.setEnabled(False)
        self.fixed_time.setVisible(False)
        self.fixed_time_tolerance = QDoubleSpinBox()
        self.fixed_time_tolerance.setRange(0.0, 1.0e30)
        self.fixed_time_tolerance.setDecimals(9)
        self.fixed_time_tolerance.setSuffix(" s tolerance")
        self.fixed_time_tolerance.setAccessibleName("Fixed dataset time tolerance in seconds")
        self.fixed_time_tolerance.setEnabled(False)
        self.fixed_time_tolerance.setVisible(False)
        self.group_by = QComboBox()
        _set_combo_options(
            self.group_by,
            [("Run", "run_id"), ("Study", "study_id"), ("Scenario group", "scenario_group")],
        )
        self.group_by.setAccessibleName("Dataset split grouping")
        self.seed = QSpinBox()
        self.seed.setRange(0, 2_147_483_647)
        self.seed.setAccessibleName("Deterministic dataset split seed")
        self.split_train = _proportion_spin("Training split proportion", 0.70)
        self.split_validation = _proportion_spin("Validation split proportion", 0.15)
        self.split_test = _proportion_spin("Test split proportion", 0.15)
        self.duplicate_policy = QComboBox()
        _set_combo_options(
            self.duplicate_policy,
            [
                ("Block duplicates", "error"),
                ("Exclude duplicates", "exclude"),
                ("Allow declared replicates", "allow_replicates"),
            ],
        )
        self.duplicate_policy.setAccessibleName("Dataset duplicate policy")
        self.validity_required = QCheckBox("Require validity-domain metadata")
        self.validity_required.setAccessibleName("Require dataset validity domain")
        self.qc_requirements = QLineEdit()
        self.qc_requirements.setPlaceholderText("Optional QC requirements JSON file")
        self.qc_requirements.setAccessibleName("Dataset QC requirements JSON file")
        self.features = _QuantityChecklist("Dataset feature quantities")
        self.targets = _QuantityChecklist("Dataset target quantities")
        self.samples = _table(
            "Study sample quality control",
            ["Sample", "Generation", "Validation", "Duplicate", "Run", "Completeness"],
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self.status)
        self.study_tabs = QTabWidget()
        self.study_tabs.setAccessibleName("Study definition, quality control, dataset, and reports")

        definition_content = QWidget()
        definition_layout = QVBoxLayout(definition_content)
        definition_form = QFormLayout()
        definition_form.addRow("Verified baseline case", self.baseline)
        definition_form.addRow("Versioned specification", self.specification)
        definition_layout.addLayout(definition_form)
        definition_layout.addWidget(
            action_bar(
                self.open_spec_button,
                self.save_spec_button,
                self.validate_spec_button,
                self.generate_button,
            )
        )
        definition_layout.addWidget(self.spec_editor, 1)
        self.definition_stack = QStackedWidget()
        self.definition_empty = EmptyState(
            "Open a study specification",
            "Load a versioned YAML specification before parameters or generation actions are shown.",
        )
        self.definition_stack.addWidget(self.definition_empty)
        self.definition_stack.addWidget(definition_content)
        definition_page = QWidget()
        definition_page_layout = QVBoxLayout(definition_page)
        definition_page_layout.addWidget(self.open_spec_button, 0, Qt.AlignmentFlag.AlignLeft)
        definition_page_layout.addWidget(self.definition_stack, 1)
        self.study_tabs.addTab(definition_page, "Definition")

        parameters_page = QWidget()
        parameters_layout = QVBoxLayout(parameters_page)
        parameters_help = QLabel(
            "Parameter paths, units, distributions, and constraint memberships are read from the validated specification."
        )
        parameters_help.setWordWrap(True)
        parameters_help.setObjectName("mutedText")
        parameters_layout.addWidget(parameters_help)
        self.parameters_stack = QStackedWidget()
        self.parameters_empty = EmptyState(
            "No validated parameters",
            "Open and validate a study specification to inspect its parameter paths, units, and constraints.",
        )
        self.parameters_stack.addWidget(self.parameters_empty)
        self.parameters_stack.addWidget(self.parameters)
        parameters_layout.addWidget(self.parameters_stack, 1)
        self.study_tabs.addTab(parameters_page, "Parameters and Constraints")

        samples_page = QWidget()
        samples_layout = QVBoxLayout(samples_page)
        samples_layout.addWidget(action_bar(self.open_manifest_button, self.prepare_sample_button))
        self.samples_stack = QStackedWidget()
        self.samples_empty = EmptyState(
            "No study manifest loaded",
            "Generate a study or open a finalised study manifest to inspect sample quality control.",
        )
        self.samples_stack.addWidget(self.samples_empty)
        self.samples_stack.addWidget(self.samples)
        samples_layout.addWidget(self.samples_stack, 1)
        self.study_tabs.addTab(samples_page, "Samples / QC")

        dataset_page = QWidget()
        dataset_layout = QVBoxLayout(dataset_page)
        package_card, package_layout = section_card(
            "Source result packages", "Only saved packages with accepted provenance and completeness can be exported."
        )
        package_layout.addWidget(self.packages)
        package_layout.addWidget(self.add_package_button, 0, Qt.AlignmentFlag.AlignLeft)
        dataset_layout.addWidget(package_card)
        definition_card, dataset_definition_layout = section_card(
            "Dataset and time definition",
            "Dataset type controls whether a fixed saved time must be selected.",
        )
        definition_form = QFormLayout()
        definition_form.addRow("Dataset type", self.dataset_type)
        definition_form.addRow("Lineage", self.dataset_source)
        self.fixed_time_label = QLabel("Fixed time")
        self.fixed_tolerance_label = QLabel("Time tolerance")
        definition_form.addRow(self.fixed_time_label, self.fixed_time)
        definition_form.addRow(self.fixed_tolerance_label, self.fixed_time_tolerance)
        dataset_definition_layout.addLayout(definition_form)
        dataset_layout.addWidget(definition_card)

        quantity_card, quantity_layout = section_card(
            "Feature and target selection",
            "Selections come from quantities shared by every accepted source package; a quantity cannot be both a feature and a target.",
        )
        quantity_columns = QHBoxLayout()
        feature_column = QVBoxLayout()
        feature_column.addWidget(QLabel("Features"))
        feature_column.addWidget(self.features)
        target_column = QVBoxLayout()
        target_column.addWidget(QLabel("Targets"))
        target_column.addWidget(self.targets)
        quantity_columns.addLayout(feature_column, 1)
        quantity_columns.addLayout(target_column, 1)
        quantity_layout.addLayout(quantity_columns)
        dataset_layout.addWidget(quantity_card)

        split_card, split_layout = section_card(
            "Split policy", "Grouping is applied before deterministic train/validation/test assignment."
        )
        split_form = QFormLayout()
        split_form.addRow("Split grouping", self.group_by)
        split_form.addRow("Deterministic seed", self.seed)
        split_form.addRow("Training proportion", self.split_train)
        split_form.addRow("Validation proportion", self.split_validation)
        split_form.addRow("Test proportion", self.split_test)
        self.split_status = StatusLabel("Dataset split total")
        split_layout.addLayout(split_form)
        split_layout.addWidget(self.split_status)
        dataset_layout.addWidget(split_card)

        safety_card, safety_layout = section_card(
            "Safety and quality control",
            "Incomplete or scientifically ineligible packages remain excluded by the headless dataset assembler.",
        )
        safety_form = QFormLayout()
        safety_form.addRow("Duplicate policy", self.duplicate_policy)
        safety_form.addRow("QC requirements file", self.qc_requirements)
        safety_layout.addLayout(safety_form)
        safety_layout.addWidget(self.validity_required)
        dataset_layout.addWidget(safety_card)

        assembly_card, assembly_layout = section_card(
            "Final assembly", "Resolve every prerequisite before choosing a new output directory."
        )
        self.dataset_eligibility = StatusLabel("Dataset assembly eligibility")
        assembly_layout.addWidget(self.dataset_eligibility)
        assembly_layout.addWidget(self.dataset_button, 0, Qt.AlignmentFlag.AlignLeft)
        dataset_layout.addWidget(assembly_card)
        notice = QLabel(
            "Dataset export excludes blocked, failed, partial, cancelled, crashed, force-terminated, and output-incomplete runs; it never trains a model."
        )
        notice.setWordWrap(True)
        notice.setAccessibleName("AI dataset safety boundary")
        dataset_layout.addWidget(notice)
        dataset_layout.addStretch(1)
        dataset_scroll = QScrollArea()
        dataset_scroll.setWidgetResizable(True)
        dataset_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        dataset_scroll.setFrameShape(QFrame.Shape.NoFrame)
        dataset_scroll.setWidget(dataset_page)
        self.study_tabs.addTab(dataset_scroll, "Dataset Export")

        reports_page = QWidget()
        reports_layout = QVBoxLayout(reports_page)
        reports_card, reports_card_layout = section_card(
            "Derived reports",
            "Reports are generated from saved study or dataset artifacts and retain source references.",
        )
        report_form = QFormLayout()
        report_form.addRow("Report source", self.report_type)
        reports_card_layout.addLayout(report_form)
        reports_card_layout.addWidget(self.report_button, 0, Qt.AlignmentFlag.AlignLeft)
        reports_layout.addWidget(reports_card)
        reports_layout.addStretch(1)
        self.study_tabs.addTab(reports_page, "Reports")
        layout.addWidget(self.study_tabs, 1)
        self.open_spec_button.clicked.connect(self._open_study_spec)
        self.save_spec_button.clicked.connect(self._save_study_spec)
        self.validate_spec_button.clicked.connect(self._validate_study_spec)
        self.generate_button.clicked.connect(self._request_study)
        self.open_manifest_button.clicked.connect(self._open_manifest)
        self.prepare_sample_button.clicked.connect(self._prepare_selected_sample)
        self.add_package_button.clicked.connect(self._add_package)
        self.dataset_button.clicked.connect(self._request_dataset)
        self.report_button.clicked.connect(self._request_report)
        self._study_path: Path | None = None
        self._study_source_sha256: str | None = None
        self._report_sources: dict[str, list[str]] = {}
        self._study_manifest_path: Path | None = None
        self._samples_by_id: dict[str, dict[str, Any]] = {}
        self._spec_valid = False
        self._loading_spec = False
        self.report_type.currentIndexChanged.connect(self._refresh_report_button)
        self.dataset_type.currentIndexChanged.connect(self._dataset_type_changed)
        self.dataset_source.currentIndexChanged.connect(self._refresh_dataset_eligibility)
        self.group_by.currentIndexChanged.connect(self._refresh_dataset_eligibility)
        self.duplicate_policy.currentIndexChanged.connect(self._refresh_dataset_eligibility)
        self.fixed_time.valueChanged.connect(self._refresh_dataset_eligibility)
        self.fixed_time_tolerance.valueChanged.connect(self._refresh_dataset_eligibility)
        self.seed.valueChanged.connect(self._refresh_dataset_eligibility)
        for spin in (self.split_train, self.split_validation, self.split_test):
            spin.valueChanged.connect(self._refresh_dataset_eligibility)
        self.validity_required.stateChanged.connect(self._refresh_dataset_eligibility)
        self.qc_requirements.textChanged.connect(self._refresh_dataset_eligibility)
        self.features.changed.connect(self._quantity_selection_changed)
        self.targets.changed.connect(self._quantity_selection_changed)
        self.samples.currentCellChanged.connect(lambda *_args: self._refresh_sample_action())
        self.study_tabs.currentChanged.connect(self._update_focus_proxy)
        self.spec_editor.textChanged.connect(self._study_text_changed)
        self._dataset_type_changed()
        self._refresh_report_button()
        self._refresh_study_actions()
        self._refresh_dataset_eligibility()
        self._update_focus_proxy()

    def _update_focus_proxy(self, *_args: Any) -> None:
        focus_targets = (
            self.open_spec_button,
            self.parameters,
            self.samples if self.samples.rowCount() else self.open_manifest_button,
            self.add_package_button,
            self.report_type,
        )
        self.setFocusProxy(focus_targets[self.study_tabs.currentIndex()])

    def _open_study_spec(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open study specification", "study_spec.yaml", "YAML (*.yaml *.yml)"
        )
        if path:
            self.load_study_spec(path)

    def _study_text_changed(self) -> None:
        if self._loading_spec:
            return
        self._spec_valid = False
        self.parameters_stack.setCurrentIndex(0)
        self.status.set_status(
            "Study specification changed; validate and save this revision before generation",
            QStyle.StandardPixmap.SP_MessageBoxWarning,
        )
        self._refresh_study_actions()

    def _refresh_study_actions(self) -> None:
        has_text = bool(self.spec_editor.toPlainText().strip())
        saved_current = bool(
            self._study_path
            and self._study_path.is_file()
            and self._study_source_sha256
            and self._study_source_sha256 == sha256_file(self._study_path)
            and self._study_path.read_text(encoding="utf-8") == self.spec_editor.toPlainText()
        )
        _set_action_state(
            self.validate_spec_button,
            has_text,
            ready="Validate the current versioned study specification.",
            blocked="Open a study specification before validation.",
        )
        _set_action_state(
            self.save_spec_button,
            has_text,
            ready="Save the current specification atomically.",
            blocked="Open a study specification before saving.",
        )
        _set_action_state(
            self.generate_button,
            self._spec_valid and saved_current,
            ready="Generate deterministically and full-preflight each case.",
            blocked="Validate and save the unchanged specification before generation.",
        )

    def _refresh_sample_action(self) -> None:
        row = self.samples.currentRow()
        sample_id = self.samples.item(row, 0).text() if row >= 0 and self.samples.item(row, 0) else ""
        sample = self._samples_by_id.get(sample_id)
        eligible = bool(
            sample
            and self._study_manifest_path
            and sample.get("generation_outcome") == "generated"
            and sample.get("validation_status") == "ready"
            and not sample.get("run_id")
            and sample.get("case_path")
        )
        _set_action_state(
            self.prepare_sample_button,
            eligible,
            ready="Prepare the selected generated and validated sample.",
            blocked="Select a generated ready sample without an existing run.",
        )

    def load_study_spec(self, path: str | Path) -> None:
        source = Path(path).resolve()
        text = source.read_text(encoding="utf-8")
        self._study_path = source
        self._study_source_sha256 = sha256_file(source)
        self.specification.setText(str(source))
        self._loading_spec = True
        self.spec_editor.setPlainText(text)
        self._loading_spec = False
        self.definition_stack.setCurrentIndex(1)
        self._validate_study_spec()
        self._refresh_study_actions()

    def _validate_study_spec(self) -> bool:
        try:
            specification = validate_study_spec_text(self.spec_editor.toPlainText())
        except Exception as error:
            self._spec_valid = False
            self.parameters.setRowCount(0)
            self.parameters_empty.body.setText(
                "Open and validate a study specification to inspect its parameter paths, units, and constraints."
            )
            self.parameters_stack.setCurrentIndex(0)
            self.status.set_status(
                f"Blocked study specification: {error}",
                QStyle.StandardPixmap.SP_MessageBoxWarning,
            )
            self._refresh_study_actions()
            return False
        self.baseline.setText(specification.baseline_case_path)
        memberships = {
            parameter_id: [] for parameter_id in (item.parameter_id for item in specification.parameters)
        }
        for constraint in (
            *specification.constraint_groups,
            *specification.cross_parameter_constraints,
        ):
            for parameter_id in constraint.parameter_ids:
                memberships[parameter_id].append(constraint.constraint_id)
        _fill(
            self.parameters,
            [
                [
                    parameter.parameter_id,
                    ".".join(map(str, parameter.yaml_path)),
                    parameter.data_type,
                    parameter.entered_unit or "unitless",
                    parameter.canonical_unit or "unitless",
                    parameter.sampling_distribution,
                    ", ".join(memberships[parameter.parameter_id]) or "None",
                ]
                for parameter in specification.parameters
            ],
        )
        self._spec_valid = True
        if specification.parameters:
            self.parameters_stack.setCurrentIndex(1)
        else:
            self.parameters_empty.body.setText(
                "This valid specification defines no variable parameters; there are no parameter rows to display."
            )
            self.parameters_stack.setCurrentIndex(0)
        self.status.set_status(
            f"Study specification valid: {specification.sample_count} samples, seed {specification.seed}",
            QStyle.StandardPixmap.SP_DialogApplyButton,
        )
        self._refresh_study_actions()
        return True

    def _save_study_spec(self) -> None:
        if not self._validate_study_spec():
            return
        path = self._study_path
        if path is None:
            selected, _ = QFileDialog.getSaveFileName(
                self, "Save study specification", "study_spec.yaml", "YAML (*.yaml *.yml)"
            )
            if not selected:
                return
            path = Path(selected).resolve()
        try:
            save_study_spec_text(
                path,
                self.spec_editor.toPlainText(),
                expected_sha256=self._study_source_sha256 if path == self._study_path else None,
            )
        except Exception as error:
            self.status.set_status(
                f"Study specification not saved: {error}",
                QStyle.StandardPixmap.SP_MessageBoxWarning,
            )
            return
        self._study_path = path
        self._study_source_sha256 = sha256_file(path)
        self.specification.setText(str(path))
        self.status.set_status(
            "Study specification saved atomically",
            QStyle.StandardPixmap.SP_DialogApplyButton,
        )
        self._spec_valid = True
        self._refresh_study_actions()

    def _open_manifest(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open study manifest", "study_manifest.json", "JSON (*.json)"
        )
        if path:
            self.load_manifest(path)

    def load_manifest(self, path: str | Path) -> None:
        manifest = _read_json(Path(path))
        self._study_manifest_path = Path(path).resolve()
        self._samples_by_id = {
            str(sample.get("sample_id")): sample for sample in manifest.get("samples", [])
        }
        _fill(
            self.samples,
            [
                [
                    sample.get("sample_id"),
                    sample.get("generation_outcome"),
                    sample.get("validation_status"),
                    sample.get("duplicate_of_sample_id") or "None",
                    sample.get("run_id") or "Not run",
                    sample.get("completion_state") or "Not run",
                ]
                for sample in manifest.get("samples", [])
            ],
        )
        self.samples_stack.setCurrentIndex(1)
        self._report_sources["study"] = [str(Path(path).resolve())]
        self._refresh_report_button()
        self._refresh_sample_action()
        self._update_focus_proxy()
        self._refresh_dataset_eligibility()

    @property
    def study_manifest_path(self) -> Path | None:
        return self._study_manifest_path

    def _prepare_selected_sample(self) -> None:
        row = self.samples.currentRow()
        sample_id = self.samples.item(row, 0).text() if row >= 0 else ""
        sample = self._samples_by_id.get(sample_id)
        if (
            sample
            and self._study_manifest_path
            and sample.get("generation_outcome") == "generated"
            and sample.get("validation_status") == "ready"
            and not sample.get("run_id")
            and sample.get("case_path")
        ):
            case_path = Path(str(sample["case_path"]))
            if not case_path.is_absolute():
                case_path = self._study_manifest_path.parent / case_path
            self.prepare_sample_requested.emit(
                {
                    "manifest": str(self._study_manifest_path),
                    "sample_id": sample_id,
                    "case": str(case_path.resolve()),
                }
            )
            return
        self.status.set_status(
            "Blocked: select one generated, ready study sample without an existing run ID",
            QStyle.StandardPixmap.SP_MessageBoxWarning,
        )

    def load_dataset_manifest(self, path: str | Path) -> None:
        manifest = Path(path).resolve()
        self._report_sources["dataset"] = [str(manifest)]
        index = self.report_type.findData("dataset")
        if index >= 0:
            self.report_type.setCurrentIndex(index)
        self._refresh_report_button()

    def _refresh_report_button(self) -> None:
        eligible = _combo_value(self.report_type) in self._report_sources
        _set_action_state(
            self.report_button,
            eligible,
            ready="Generate the selected report from saved artifacts.",
            blocked="Load the matching saved study or dataset artifact first.",
        )

    def _request_report(self) -> None:
        report_type = _combo_value(self.report_type)
        sources = self._report_sources.get(report_type, [])
        if not sources:
            self._refresh_report_button()
            return
        output = _new_output_directory(
            self, f"New {report_type} report directory", f"{report_type}-report"
        )
        if output and sources:
            self.report_requested.emit(
                {"report_type": report_type, "output_dir": output, "sources": sources}
            )

    def _request_study(self) -> None:
        path = self._study_path or Path(self.specification.text())
        saved_text = path.read_text(encoding="utf-8") if path.is_file() else None
        if (
            saved_text == self.spec_editor.toPlainText()
            and self._validate_study_spec()
            and self._study_source_sha256 == sha256_file(path)
        ):
            self.study_requested.emit(str(path))
        else:
            self.status.set_status(
                "Blocked: validate and save the current versioned study specification first",
                QStyle.StandardPixmap.SP_MessageBoxWarning,
            )

    def _add_package(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select result package")
        if not path:
            return
        resolved = Path(path).resolve()
        if str(resolved) in self._dataset_package_paths():
            return
        try:
            package = ResultPackage(resolved)
            if not package.supported or not package.status.interpretation_supported:
                raise ValueError(
                    f"{package.status.reason}; output {package.status.output_completeness}"
                )
            descriptors = package.quantity_descriptors()
        except Exception as error:
            self.status.set_status(
                f"Dataset package blocked: {error}",
                QStyle.StandardPixmap.SP_MessageBoxWarning,
            )
            return
        item = QListWidgetItem(self._dataset_package_label(package, resolved))
        item.setData(Qt.ItemDataRole.UserRole, str(resolved))
        item.setToolTip(f"Run ID: {package.run_id}\nPackage: {resolved}")
        self.packages.addItem(item)
        item.setData(Qt.ItemDataRole.UserRole + 1, descriptors)
        self._refresh_dataset_quantities()
        self._refresh_dataset_eligibility()

    @staticmethod
    def _dataset_package_label(package: ResultPackage, path: Path) -> str:
        case_name = (
            package.run_record.get("case_id")
            or package.run_record.get("case_name")
            or path.parent.name
        )
        return f"{case_name} · {_short_id(package.run_id)}"

    def _dataset_package_paths(self) -> list[str]:
        return [
            str(self.packages.item(index).data(Qt.ItemDataRole.UserRole))
            for index in range(self.packages.count())
        ]

    def _refresh_dataset_quantities(self) -> None:
        shared: set[str] | None = None
        descriptors: dict[str, Any] = {}
        for index in range(self.packages.count()):
            item_descriptors = self.packages.item(index).data(Qt.ItemDataRole.UserRole + 1) or {}
            names = set(item_descriptors) - {"time_s", "time_days"}
            shared = names if shared is None else shared & names
            if not descriptors:
                descriptors = item_descriptors
        accepted = {name: descriptors[name] for name in sorted(shared or ()) if name in descriptors}
        self.features.set_quantities(accepted)
        self.targets.set_quantities(accepted)

    def _dataset_type_changed(self, *_args: Any) -> None:
        fixed = _combo_value(self.dataset_type) == "fixed_time"
        for widget in (
            self.fixed_time,
            self.fixed_time_tolerance,
            self.fixed_time_label,
            self.fixed_tolerance_label,
        ):
            widget.setVisible(fixed)
        self.fixed_time.setEnabled(fixed)
        self.fixed_time_tolerance.setEnabled(fixed)
        quantity_dataset = _combo_value(self.dataset_type) != "failure"
        if not quantity_dataset:
            self.features.clear_checks()
            self.targets.clear_checks()
        self.features.setEnabled(quantity_dataset)
        self.targets.setEnabled(quantity_dataset)
        self._refresh_dataset_eligibility()

    def _quantity_selection_changed(self) -> None:
        overlap = set(self.features.values()) & set(self.targets.values())
        if overlap:
            self.targets.list.blockSignals(True)
            for index in range(self.targets.list.count()):
                item = self.targets.list.item(index)
                if item.data(Qt.ItemDataRole.UserRole) in overlap:
                    item.setCheckState(Qt.CheckState.Unchecked)
            self.targets.list.blockSignals(False)
            self.status.set_status(
                "A saved quantity cannot be both a feature and a target; the duplicate target selection was removed",
                QStyle.StandardPixmap.SP_MessageBoxWarning,
            )
        self._refresh_dataset_eligibility()

    def _dataset_eligibility_result(self) -> tuple[bool, str]:
        packages = self._dataset_package_paths()
        dataset_type = _combo_value(self.dataset_type)
        features = self.features.values()
        targets = self.targets.values()
        proportions = (
            self.split_train.value(),
            self.split_validation.value(),
            self.split_test.value(),
        )
        if not packages:
            return False, "Add at least one accepted complete result package"
        if _combo_value(self.dataset_source) == "loaded_study_manifest" and not self._study_manifest_path:
            return False, "Load a finalised study manifest or use explicit selected-run lineage"
        if abs(sum(proportions) - 1.0) > 1e-12:
            return False, "Train, validation, and test proportions must sum to 1"
        if dataset_type == "failure":
            if features or targets:
                return False, "Failure datasets require no feature or target quantities"
        elif not features or not targets:
            return False, "Select at least one feature and one distinct target quantity"
        elif set(features) & set(targets):
            return False, "Feature and target quantities must be distinct"
        qc_path = self.qc_requirements.text().strip()
        if qc_path:
            qc_file = Path(qc_path)
            if not qc_file.is_file():
                return False, "The optional QC requirements file does not exist"
            try:
                qc_requirements = json.loads(qc_file.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                return False, f"The QC requirements file is not valid JSON: {error}"
            if not isinstance(qc_requirements, dict):
                return False, "The QC requirements JSON must contain an object"
        return True, "All dataset prerequisites are satisfied"

    def _refresh_dataset_eligibility(self, *_args: Any) -> None:
        total = self.split_train.value() + self.split_validation.value() + self.split_test.value()
        split_valid = abs(total - 1.0) <= 1e-12
        self.split_status.set_status(
            f"Split total: {total:.4f} ({'valid' if split_valid else 'must equal 1.0000'})",
            QStyle.StandardPixmap.SP_DialogApplyButton
            if split_valid
            else QStyle.StandardPixmap.SP_MessageBoxWarning,
        )
        eligible, reason = self._dataset_eligibility_result()
        _set_action_state(
            self.dataset_button,
            eligible,
            ready="Choose an output directory and assemble the validated dataset request.",
            blocked=reason,
        )
        self.dataset_eligibility.set_status(
            ("Ready: " if eligible else "Blocked: ") + reason,
            QStyle.StandardPixmap.SP_DialogApplyButton
            if eligible
            else QStyle.StandardPixmap.SP_MessageBoxWarning,
        )

    def _request_dataset(self) -> None:
        eligible, reason = self._dataset_eligibility_result()
        if not eligible:
            self.status.set_status(
                f"Dataset assembly blocked: {reason}",
                QStyle.StandardPixmap.SP_MessageBoxWarning,
            )
            self._refresh_dataset_eligibility()
            return
        features = self.features.values()
        targets = self.targets.values()
        packages = self._dataset_package_paths()
        proportions = {
            "train": self.split_train.value(),
            "validation": self.split_validation.value(),
            "test": self.split_test.value(),
        }
        output = _new_output_directory(self, "New dataset output directory", "dataset")
        if output:
            self.dataset_requested.emit(
                {
                    "output_dir": output,
                    "packages": packages,
                    "dataset_type": _combo_value(self.dataset_type),
                    "features": features,
                    "targets": targets,
                    "fixed_time_s": self.fixed_time.value()
                    if _combo_value(self.dataset_type) == "fixed_time"
                    else None,
                    "fixed_time_tolerance_s": self.fixed_time_tolerance.value(),
                    "group_by": _combo_value(self.group_by),
                    "seed": self.seed.value(),
                    "split_proportions": proportions,
                    "duplicate_policy": _combo_value(self.duplicate_policy),
                    "validity_domain_required": self.validity_required.isChecked(),
                    "qc_requirements_json": self.qc_requirements.text().strip() or None,
                    "source_study_manifest": (
                        str(self._study_manifest_path)
                        if _combo_value(self.dataset_source) == "loaded_study_manifest"
                        else None
                    ),
                }
            )


def _proportion_spin(name: str, value: float) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(0.0, 1.0)
    spin.setDecimals(4)
    spin.setSingleStep(0.05)
    spin.setValue(value)
    spin.setAccessibleName(name)
    return spin

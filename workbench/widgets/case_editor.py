"""Round-trip YAML editor with curated scientific form views."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import QFileSystemWatcher, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QAbstractItemView,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from batch_runner.config import CaseConfig
from workbench_core.documents import (
    CaseDocument,
    CaseDocumentError,
    ExternalModificationError,
)
from workbench.widgets.presentation import Disclosure, action_bar


SECTION_NAMES = (
    "Overview",
    "Physical and Brine",
    "CO₂ and Redox",
    "Minerals and Kinetics",
    "Solver",
    "Post-processing",
    "Validation Targets",
    "Outputs",
    "YAML",
    "Validation",
)


def _value(data: Any, *path: str, default: Any = None) -> Any:
    current = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def _scalar_items(value: Any, path: tuple[str | int, ...] = ()):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _scalar_items(child, (*path, str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _scalar_items(child, (*path, index))
    else:
        yield path, value


def _structured_items(value: Any, path: tuple[str | int, ...] = ()):
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = (*path, str(key))
            if isinstance(child, (dict, list)):
                yield child_path, "<mapping>" if isinstance(child, dict) else "<list>", "container"
                yield from _structured_items(child, child_path)
            else:
                yield child_path, child, "scalar"
    elif isinstance(value, list):
        for index, child in enumerate(value):
            child_path = (*path, index)
            if isinstance(child, (dict, list)):
                yield child_path, "<mapping>" if isinstance(child, dict) else "<list>", "container"
                yield from _structured_items(child, child_path)
            else:
                yield child_path, child, "scalar"


def _path_text(path: tuple[str | int, ...]) -> str:
    text = ""
    for part in path:
        text += f"[{part}]" if isinstance(part, int) else ("." if text else "") + part
    return text


def _display_unit(path: tuple[str | int, ...], value: Any) -> str:
    name = str(path[-1]) if path else ""
    if name == "temperature_c":
        return "degC"
    if name.endswith("_bar") or name == "pressure_bar":
        return "bar"
    if name.endswith("_mol"):
        return "mol"
    if name.endswith("_cm3"):
        return "cm3"
    if name in {"enabled", "include_initial", "include_final", "manifest", "diagnostics", "timeseries", "summaries", "solver_history", "plots", "debug"}:
        return "dimensionless boolean"
    if name == "unit":
        return "entered unit label"
    return "dimensionless or defined by adjacent unit field" if isinstance(value, (int, float)) else "text/categorical"


class _ExplicitValueTree(QTreeWidget):
    def keyPressEvent(self, event: Any) -> None:
        item = self.currentItem()
        if event.key() == Qt.Key.Key_F2 and item is not None and item.data(0, Qt.ItemDataRole.UserRole + 2) == "scalar":
            self.setCurrentItem(item, 1)
            self.editItem(item, 1)
            return
        super().keyPressEvent(event)


class _ExplicitValueDelegate(QStyledItemDelegate):
    def createEditor(self, parent: QWidget, option: Any, index: Any) -> QWidget | None:
        if index.column() != 1 or index.siblingAtColumn(0).data(Qt.ItemDataRole.UserRole + 2) != "scalar":
            return None
        return super().createEditor(parent, option, index)


class CaseEditor(QWidget):
    document_state_changed = Signal(str)
    validation_state_changed = Signal(str)
    case_saved = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("Hybrid case editor")
        self.document: CaseDocument | None = None
        self._last_valid_model: CaseConfig | None = None
        self._updating = False
        self._external_conflict = False
        self._schema_valid = False
        self._template_mode = False
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._external_change)

        self.sections = QComboBox()
        self.sections.setObjectName("caseSectionSelector")
        for name in SECTION_NAMES:
            self.sections.addItem(name, name)
        self.sections.setAccessibleName("Case section navigator")
        self.sections.setAccessibleDescription("Navigate case sections; this is not a linear wizard")

        self.name_edit = QLineEdit()
        self.name_edit.setAccessibleName("Case name")
        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(-273.15, 1_000_000.0)
        self.temperature.setDecimals(8)
        self.temperature.setAccessibleName("Temperature in degrees Celsius")
        self.pressure = QDoubleSpinBox()
        self.pressure.setRange(0.00000001, 1_000_000.0)
        self.pressure.setDecimals(8)
        self.pressure.setAccessibleName("Pressure in bar")
        self.kinetic_model = QComboBox()
        self.kinetic_model.addItems(["disabled", "palandri_kharaka", "kinec"])
        self.kinetic_model.setAccessibleName("Kinetic model")

        form_widget = QWidget()
        form = QFormLayout(form_widget)
        form.addRow("Case name", self.name_edit)
        form.addRow("Temperature (°C)", self.temperature)
        form.addRow("Pressure (bar)", self.pressure)
        form.addRow("Kinetic model", self.kinetic_model)
        assistance = QLabel(
            "Values and units are read from the case. The workbench never supplies numerical recommendations. "
            "Conditional fields are preserved only when schema-valid; coupled removals must be reviewed in YAML."
        )
        assistance.setWordWrap(True)
        assistance.setAccessibleName("Scientific editing boundary")
        form.addRow(assistance)

        self.resolved = QPlainTextEdit()
        self.resolved.setReadOnly(True)
        self.resolved.setAccessibleName("Resolved configuration preview")
        self.resolved.setAccessibleDescription(
            "Schema-resolved values including defaults; these are not silently written to YAML"
        )
        self.diff = QPlainTextEdit()
        self.diff.setReadOnly(True)
        self.diff.setAccessibleName("Source-to-save case diff")
        self.diff.setAccessibleDescription(
            "Unified diff from the last saved or imported bytes to the current round-trip document"
        )
        self.structured = QTabWidget()
        self.structured.setAccessibleName("Structured case views")
        core_scroll = QScrollArea()
        core_scroll.setWidgetResizable(True)
        core_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        core_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        core_scroll.setWidget(form_widget)
        self.structured.addTab(core_scroll, "Core fields")
        explicit_page = QWidget()
        explicit_layout = QVBoxLayout(explicit_page)
        explicit_help = QLabel(
            "Edit one or more explicit source values, then apply them together. Values are schema-validated as one transaction; defaults remain read-only and are never inserted."
        )
        explicit_help.setWordWrap(True)
        self.explicit_search = QLineEdit()
        self.explicit_search.setPlaceholderText("Search YAML paths, values, or units")
        self.explicit_search.setAccessibleName("Search explicit case values")
        self.explicit_values = _ExplicitValueTree()
        self.explicit_values.setColumnCount(4)
        self.explicit_values.setHeaderLabels(["YAML path", "Explicit source value", "Unit", "Value origin"])
        self.explicit_values.setAccessibleName("Editable explicit case values")
        self.explicit_values.setAccessibleDescription(
            "Transactional editor for every scalar already present in the source case"
        )
        self.explicit_values.setItemDelegate(_ExplicitValueDelegate(self.explicit_values))
        self.explicit_values.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.apply_structured_button = QPushButton("Apply structured edits")
        self.apply_structured_button.setAccessibleName("Apply all structured case edits")
        self.remove_structured_button = QPushButton("Remove selected field or list item")
        self.remove_structured_button.setAccessibleName("Remove selected structured field or list item")
        self.rename_key_edit = QLineEdit()
        self.rename_key_edit.setPlaceholderText("Replacement for a selected placeholder mapping key")
        self.rename_key_edit.setAccessibleName("Replacement mapping key")
        self.rename_key_button = QPushButton("Rename selected placeholder key")
        self.rename_key_button.setAccessibleName("Rename selected placeholder mapping key")
        self.reset_structured_button = QPushButton("Reset unapplied structured edits")
        explicit_layout.addWidget(explicit_help)
        explicit_layout.addWidget(self.explicit_search)
        explicit_layout.addWidget(self.explicit_values)
        explicit_layout.addWidget(
            action_bar(
                self.apply_structured_button,
                self.remove_structured_button,
                self.reset_structured_button,
            )
        )
        rename_row = QHBoxLayout()
        rename_row.addWidget(self.rename_key_edit, 1)
        rename_row.addWidget(self.rename_key_button)
        explicit_layout.addLayout(rename_row)
        self.structured.addTab(explicit_page, "Advanced values")
        self.structured.addTab(self.resolved, "Section values")
        self.structured.addTab(self.diff, "Diff")

        self.yaml_text = QPlainTextEdit()
        self.yaml_text.setAccessibleName("Raw YAML editor")
        self.yaml_text.setAccessibleDescription(
            "Raw round-trip YAML; press Control Enter to apply after parsing and schema validation"
        )
        self.apply_button = QPushButton("Apply YAML")
        self.apply_button.setAccessibleName("Apply YAML changes")
        self.save_button = QPushButton("Save")
        self.save_button.setAccessibleName("Save case atomically")
        self.save_as_button = QPushButton("Save As")
        self.save_as_button.setAccessibleName("Save case to a new file")
        self.undo_button = QPushButton("Undo")
        self.undo_button.setAccessibleName("Undo document edit")
        self.redo_button = QPushButton("Redo")
        self.redo_button.setAccessibleName("Redo document edit")
        self.diff_button = QPushButton("Show source-to-save diff")
        self.diff_button.setAccessibleName("Show source-to-save case diff")
        buttons = QHBoxLayout()
        for button in (
            self.apply_button,
            self.save_button,
            self.save_as_button,
            self.undo_button,
            self.redo_button,
            self.diff_button,
        ):
            buttons.addWidget(button)
        yaml_widget = QWidget()
        yaml_layout = QVBoxLayout(yaml_widget)
        yaml_layout.addWidget(self.yaml_text)
        yaml_layout.addLayout(buttons)

        self.tabs = QTabWidget()
        self.tabs.setAccessibleName("Case form and YAML")
        self.tabs.addTab(self.structured, "Form")
        self.tabs.addTab(yaml_widget, "YAML")

        self.error_list = QListWidget()
        self.error_list.setAccessibleName("Validation error navigator")
        self.error_list.setAccessibleDescription("Activate an error to navigate to its case field")
        self.error_list.itemActivated.connect(self._navigate_error)
        self.validation_evidence = QPlainTextEdit()
        self.validation_evidence.setReadOnly(True)
        self.validation_evidence.setAccessibleName("Layered preflight and mineral mapping evidence")
        self.section_completeness = QLabel("No case loaded")
        self.section_completeness.setObjectName("caseSectionStatus")
        self.section_completeness.setWordWrap(False)
        self.section_completeness.setMaximumHeight(34)
        self.section_completeness.setAccessibleName("Case section completeness")
        error_box = QWidget()
        error_layout = QVBoxLayout(error_box)
        error_layout.addWidget(QLabel("Validation errors"))
        error_layout.addWidget(self.error_list)
        error_layout.addWidget(QLabel("Preflight layers and kinetic mappings"))
        error_layout.addWidget(self.validation_evidence)

        self.validation_drawer = Disclosure("Validation evidence", error_box, expanded=False)
        self.validation_drawer.setObjectName("caseValidationDrawer")
        self.validation_drawer.toggle.setObjectName("caseValidationToggle")
        self.validation_drawer.toggle.setText("Validation (0)")
        self.validation_drawer.toggle.setAccessibleName("Validation evidence, 0 issues")
        self.validation_drawer.setMaximumWidth(130)
        self.validation_drawer.setVisible(False)
        self.validation_drawer.toggle.toggled.connect(self._validation_toggled)
        outer = QSplitter(Qt.Orientation.Horizontal)
        outer.setChildrenCollapsible(False)
        outer.addWidget(self.tabs)
        outer.addWidget(self.validation_drawer)
        outer.setSizes([760, 230])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.section_completeness)
        layout.addWidget(self.sections)
        layout.addWidget(outer)

        self.name_edit.editingFinished.connect(
            lambda: self._patch(("case", "name"), self.name_edit.text())
        )
        self.temperature.editingFinished.connect(
            lambda: self._patch(("physical", "temperature_c"), self.temperature.value())
        )
        self.pressure.editingFinished.connect(
            lambda: self._patch(("physical", "pressure_bar"), self.pressure.value())
        )
        self.kinetic_model.currentTextChanged.connect(self._patch_model)
        self.apply_button.clicked.connect(self.apply_yaml)
        self.save_button.clicked.connect(self.save)
        self.save_as_button.clicked.connect(self.choose_save_as)
        self.undo_button.clicked.connect(self.undo)
        self.redo_button.clicked.connect(self.redo)
        self.diff_button.clicked.connect(self.show_diff)
        self.apply_structured_button.clicked.connect(self.apply_structured_edits)
        self.remove_structured_button.clicked.connect(self._mark_structured_removal)
        self.rename_key_button.clicked.connect(self._mark_placeholder_key_rename)
        self.reset_structured_button.clicked.connect(self._refresh_explicit_values)
        self.explicit_search.textChanged.connect(self._filter_explicit_values)
        self.sections.currentIndexChanged.connect(
            lambda index: self._section_selected(str(self.sections.itemData(index) or ""))
        )
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.apply_yaml)
        QShortcut(QKeySequence.StandardKey.Save, self, activated=self.save)
        QShortcut(QKeySequence.StandardKey.Undo, self, activated=self.undo)
        QShortcut(QKeySequence.StandardKey.Redo, self, activated=self.redo)
        QShortcut(QKeySequence("F6"), self, activated=self._focus_validation)
        QWidget.setTabOrder(self.sections, self.name_edit)
        QWidget.setTabOrder(self.name_edit, self.temperature)
        QWidget.setTabOrder(self.temperature, self.pressure)
        QWidget.setTabOrder(self.pressure, self.kinetic_model)
        QWidget.setTabOrder(self.kinetic_model, self.yaml_text)
        QWidget.setTabOrder(self.yaml_text, self.apply_button)
        QWidget.setTabOrder(self.apply_button, self.save_button)
        QWidget.setTabOrder(self.save_button, self.error_list)

    @property
    def form_is_stale(self) -> bool:
        return not self._schema_valid or self._external_conflict

    def load_path(self, path: str | Path) -> None:
        self.load_document(CaseDocument.load(path))
        self._watch(Path(path))

    def import_path(self, path: str | Path) -> None:
        """Preview imported bytes as an unsaved copy; never bind saving to the source."""
        self.load_document(CaseDocument.from_text(Path(path).read_text(encoding="utf-8")))
        for watched in self._watcher.files():
            self._watcher.removePath(watched)

    def load_document(self, document: CaseDocument) -> None:
        self._external_conflict = False
        self._clear_validation_evidence()
        if document.sentinel_paths():
            self.document = document
            self._last_valid_model = None
            self._schema_valid = False
            self._template_mode = True
            self._refresh_explicit_values()
            self.yaml_text.setPlainText(document.to_text())
            self.tabs.setTabEnabled(0, True)
            self.tabs.setCurrentIndex(0)
            self.structured.setTabEnabled(0, False)
            self.structured.setCurrentIndex(1)
            self.error_list.clear()
            self.error_list.addItem(
                "Schema template is intentionally non-runnable; replace placeholders in Advanced values or YAML."
            )
            self._set_validation_available(1)
            self.document_state_changed.emit("template_placeholders")
            self.validation_state_changed.emit("stale")
            self._show_section_completeness(document.sentinel_paths())
            return
        model = CaseConfig.model_validate(document.data)
        self._schema_valid = True
        self._template_mode = False
        self.structured.setTabEnabled(0, True)
        self.document = document
        self._last_valid_model = model
        self.yaml_text.setPlainText(document.to_text())
        self._refresh_form(model)
        self.error_list.clear()
        self.tabs.setTabEnabled(0, True)
        self.document_state_changed.emit("modified" if document.is_dirty else "clean")
        self.validation_state_changed.emit("not_checked")
        self._show_section_completeness(())

    def apply_yaml(self) -> bool:
        text = self.yaml_text.toPlainText()
        try:
            candidate = CaseDocument.from_text(text)
            model = CaseConfig.model_validate(candidate.data)
        except Exception as error:
            self._show_error(error)
            self._schema_valid = False
            self._template_mode = False
            self.document_state_changed.emit(
                "invalid_yaml" if isinstance(error, CaseDocumentError) else "schema_invalid"
            )
            self.validation_state_changed.emit("stale")
            self.section_completeness.setText(
                "Section completeness is stale until the YAML and schema errors are corrected."
            )
            return False
        if self.document is not None:
            self.document.apply_text(text)
        else:
            self.document = candidate
        self._last_valid_model = model
        self._schema_valid = True
        self._template_mode = False
        self._refresh_form(model)
        self._clear_validation_evidence()
        self.tabs.setTabEnabled(0, True)
        self.document_state_changed.emit("modified")
        self.validation_state_changed.emit("stale")
        self._show_section_completeness(())
        return True

    def save(self) -> bool:
        if not self.apply_yaml() or self.document is None:
            return False
        if self.document.source_path is None:
            return self.choose_save_as()
        source = self.document.source_path
        self._watch(None)
        try:
            revision = self.document.save()
        except ExternalModificationError as error:
            self._show_error(error)
            self.document_state_changed.emit("external_conflict")
            self._watch(source)
            return False
        except CaseDocumentError as error:
            self._show_error(error)
            self._watch(source)
            return False
        self._watch(revision.path)
        self._external_conflict = False
        self.document_state_changed.emit("clean")
        self.validation_state_changed.emit("stale")
        self.case_saved.emit(str(revision.path))
        return True

    def choose_save_as(self) -> bool:
        if self.document is None:
            return False
        path, _ = QFileDialog.getSaveFileName(
            self, "Save case as", "case.yaml", "YAML (*.yaml *.yml)"
        )
        return bool(path) and self.save_as(path)

    def save_as(self, path: str | Path) -> bool:
        if not self.apply_yaml() or self.document is None:
            return False
        try:
            revision = self.document.save(path)
        except CaseDocumentError as error:
            self._show_error(error)
            return False
        self.document_state_changed.emit("clean")
        self.validation_state_changed.emit("stale")
        self.case_saved.emit(str(revision.path))
        self.load_path(revision.path)
        return True

    def undo(self) -> None:
        if self.document and self.document.undo():
            self._sync_after_document_edit()

    def redo(self) -> None:
        if self.document and self.document.redo():
            self._sync_after_document_edit()

    def show_diff(self) -> None:
        if self.document is None:
            self.diff.setPlainText("No case document is loaded.")
        else:
            self.diff.setPlainText(self.document.diff_from_saved() or "No unsaved changes.")
        self.tabs.setCurrentIndex(0)
        self.structured.setCurrentWidget(self.diff)

    def show_validation_receipt(self, receipt: dict[str, Any]) -> None:
        self.error_list.clear()
        lines = []
        has_blocking_errors = False
        for stage in receipt.get("preflight_stage_results", []):
            lines.append(f"{stage.get('stage')}: {stage.get('status')}")
            for error in stage.get("errors", []):
                has_blocking_errors = True
                self.error_list.addItem(str(error))
            for warning in stage.get("warnings", []):
                lines.append(f"  warning: {warning}")
        for mapping in receipt.get("kinetic_mapping_summary", []):
            lines.append(
                f"mapping {mapping.get('mineral_name')}: "
                f"{'mapped' if mapping.get('mapped') else 'blocked'}; "
                f"model={mapping.get('kinetic_model')}; "
                f"record={mapping.get('parameter_record')}; "
                f"surface_area_present={mapping.get('surface_area_present')}"
            )
            if not mapping.get("mapped") and mapping.get("reason"):
                has_blocking_errors = True
                self.error_list.addItem(str(mapping["reason"]))
        for error in receipt.get("errors", []):
            has_blocking_errors = True
            if not self.error_list.findItems(str(error), Qt.MatchFlag.MatchExactly):
                self.error_list.addItem(str(error))
        if self.error_list.count() == 0:
            self.error_list.addItem("No blocking preflight errors.")
        self.validation_evidence.setPlainText("\n".join(lines) or "No preflight evidence returned.")
        self._set_validation_available(
            self.error_list.count() if has_blocking_errors else 0,
            evidence_available=True,
        )
        self.validation_drawer.toggle.setChecked(has_blocking_errors)

    def _show_section_completeness(self, sentinel_paths: tuple = ()) -> None:
        unresolved = {str(path[0]) for path in sentinel_paths if path}
        groups = {
            "Overview": {"case"},
            "Physical and Brine": {"physical", "brine", "activity_models"},
            "CO₂ and Redox": {"co2", "redox"},
            "Minerals and Kinetics": {"minerals", "kinetics", "database"},
            "Solver": {"solver"},
            "Post-processing": {"postprocessing"},
            "Validation Targets": {"validation"},
            "Outputs": {"outputs", "paths"},
        }
        statuses = [
            (name, "Needs input" if keys & unresolved else "Complete")
            for name, keys in groups.items()
        ]
        unresolved_count = sum(bool(keys & unresolved) for keys in groups.values())
        summary = (
            f"{len(groups) - unresolved_count} of {len(groups)} required sections complete; "
            f"{unresolved_count} need input"
            if unresolved_count
            else f"{len(groups)} of {len(groups)} required sections complete"
        )
        self.section_completeness.setText(summary)
        self.section_completeness.setToolTip(
            "Section completeness: " + "; ".join(f"{name}: {state}" for name, state in statuses)
        )
        selected = self.sections.currentData()
        status_by_name = dict(statuses)
        self.sections.blockSignals(True)
        for index in range(self.sections.count()):
            name = str(self.sections.itemData(index))
            state = status_by_name.get(name)
            self.sections.setItemText(index, f"{name} — {state}" if state else name)
            if state:
                icon = (
                    QStyle.StandardPixmap.SP_DialogApplyButton
                    if state == "Complete"
                    else QStyle.StandardPixmap.SP_MessageBoxWarning
                )
                self.sections.setItemIcon(index, self.style().standardIcon(icon))
        if selected:
            match = self.sections.findData(selected)
            if match >= 0:
                self.sections.setCurrentIndex(match)
        self.sections.blockSignals(False)

    def _validation_toggled(self, expanded: bool) -> None:
        self.validation_drawer.setMaximumWidth(420 if expanded else 130)

    def _set_validation_available(
        self,
        issue_count: int,
        *,
        evidence_available: bool = True,
    ) -> None:
        self.validation_drawer.setVisible(evidence_available)
        self.validation_drawer.toggle.setText(f"Validation ({issue_count})")
        self.validation_drawer.toggle.setAccessibleName(
            f"Validation evidence, {issue_count} blocking issue"
            + ("s" if issue_count != 1 else "")
        )

    def _clear_validation_evidence(self) -> None:
        self.validation_drawer.toggle.setChecked(False)
        self.error_list.clear()
        self.validation_evidence.clear()
        self._set_validation_available(0, evidence_available=False)

    def _patch(self, path: tuple[str, ...], value: Any) -> None:
        if self._updating or self.document is None or self.form_is_stale:
            return
        try:
            candidate = CaseDocument.from_text(self.document.to_text())
            candidate.patch(path, value)
            model = CaseConfig.model_validate(candidate.data)
        except Exception as error:
            self._show_error(error)
            return
        self.document.apply_text(candidate.to_text())
        self._last_valid_model = model
        self.yaml_text.setPlainText(self.document.to_text())
        self._refresh_form(model)
        self._clear_validation_evidence()
        self.document_state_changed.emit("modified")
        self.validation_state_changed.emit("stale")

    def _patch_model(self, value: str) -> None:
        if self._updating or self.document is None:
            return
        current = self.document.data.get("kinetics", {})
        current_value = current.get("model") if current.get("enabled") else "disabled"
        if value != current_value:
            self._show_error(
                CaseDocumentError(
                    "Change kinetics model, parameter path, mineral roles, surface areas, and timestep fields together in Advanced values or YAML; nothing was changed."
                )
            )
            if self._last_valid_model:
                self._refresh_form(self._last_valid_model)

    def apply_structured_edits(self) -> bool:
        if self.document is None or self._external_conflict or not (self._schema_valid or self._template_mode):
            return False
        candidate = CaseDocument.from_text(self.document.to_text())
        try:
            rows = [
                self.explicit_values.topLevelItem(index)
                for index in range(self.explicit_values.topLevelItemCount())
            ]
            for item in rows:
                if item.data(0, Qt.ItemDataRole.UserRole + 2) != "scalar":
                    continue
                original = item.data(1, Qt.ItemDataRole.UserRole + 1)
                text = item.text(1)
                if text == self._scalar_text(original):
                    continue
                candidate.patch(tuple(item.data(0, Qt.ItemDataRole.UserRole)), self._parse_scalar(text, original))
            for item in sorted(rows, key=lambda value: len(tuple(value.data(0, Qt.ItemDataRole.UserRole))), reverse=True):
                replacement = item.data(0, Qt.ItemDataRole.UserRole + 4)
                if replacement:
                    candidate.rename_mapping_key(tuple(item.data(0, Qt.ItemDataRole.UserRole)), str(replacement))
            def removal_order(item: QTreeWidgetItem) -> tuple[int, int]:
                path = tuple(item.data(0, Qt.ItemDataRole.UserRole))
                return len(path), path[-1] if path and isinstance(path[-1], int) else -1

            for item in sorted(rows, key=removal_order, reverse=True):
                if item.data(0, Qt.ItemDataRole.UserRole + 3):
                    candidate.remove(tuple(item.data(0, Qt.ItemDataRole.UserRole)))
            if candidate.sentinel_paths():
                self.document.apply_text(candidate.to_text())
                self.yaml_text.setPlainText(self.document.to_text())
                self._refresh_explicit_values()
                self._show_section_completeness(candidate.sentinel_paths())
                self.error_list.clear()
                self.error_list.addItem("Unresolved template placeholders remain.")
                self._set_validation_available(1)
                self.document_state_changed.emit("template_placeholders")
                self.validation_state_changed.emit("stale")
                return True
            model = CaseConfig.model_validate(candidate.data)
        except Exception as error:
            self._show_error(error)
            return False
        self.document.apply_text(candidate.to_text())
        self._last_valid_model = model
        self._schema_valid = True
        self._template_mode = False
        self.structured.setTabEnabled(0, True)
        self.yaml_text.setPlainText(self.document.to_text())
        self._refresh_form(model)
        self._clear_validation_evidence()
        self.document_state_changed.emit("modified")
        self.validation_state_changed.emit("stale")
        return True

    def _mark_structured_removal(self) -> None:
        item = self.explicit_values.currentItem()
        if item is None:
            return
        item.setData(0, Qt.ItemDataRole.UserRole + 3, True)
        item.setText(3, "will be removed when structured edits are applied")

    def _mark_placeholder_key_rename(self) -> None:
        item = self.explicit_values.currentItem()
        replacement = self.rename_key_edit.text().strip()
        sentinel_paths = set(self.document.sentinel_paths()) if self.document else set()
        path = tuple(item.data(0, Qt.ItemDataRole.UserRole)) if item else ()
        if item is None or path not in sentinel_paths or not isinstance(path[-1] if path else None, str):
            self._show_error(CaseDocumentError("select an unresolved placeholder mapping-key row"))
            return
        if not replacement:
            self._show_error(CaseDocumentError("enter a non-empty replacement mapping key"))
            return
        item.setData(0, Qt.ItemDataRole.UserRole + 4, replacement)
        item.setText(3, f"placeholder mapping key will be renamed to {replacement}")

    @staticmethod
    def _scalar_text(value: Any) -> str:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    @staticmethod
    def _parse_scalar(text: str, original: Any) -> Any:
        value = text.strip()
        if original is None:
            if value.casefold() in {"null", "none", "~"}:
                return None
            return value
        if isinstance(original, bool):
            if value.casefold() not in {"true", "false"}:
                raise CaseDocumentError("boolean structured values must be true or false")
            return value.casefold() == "true"
        if isinstance(original, int) and not isinstance(original, bool):
            return int(value)
        if isinstance(original, float):
            return float(value)
        return value

    def _refresh_explicit_values(self) -> None:
        self.explicit_values.clear()
        if self.document is None:
            return
        for path, value, kind in _structured_items(self.document.data):
            item = QTreeWidgetItem(
                [
                    _path_text(path),
                    self._scalar_text(value),
                    _display_unit(path, value) if kind == "scalar" else "structure",
                    "explicit source input",
                ]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, path)
            item.setData(1, Qt.ItemDataRole.UserRole + 1, value)
            item.setData(0, Qt.ItemDataRole.UserRole + 2, kind)
            if kind == "scalar":
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            self.explicit_values.addTopLevelItem(item)
        for column in range(4):
            self.explicit_values.resizeColumnToContents(column)
        self._filter_explicit_values(self.explicit_search.text())

    def _filter_explicit_values(self, text: str) -> None:
        query = text.casefold().strip()
        for index in range(self.explicit_values.topLevelItemCount()):
            item = self.explicit_values.topLevelItem(index)
            haystack = " ".join(item.text(column) for column in range(4)).casefold()
            item.setHidden(bool(query and query not in haystack))

    def _focus_validation(self) -> None:
        if not self.validation_drawer.isVisible():
            return
        self.validation_drawer.toggle.setChecked(True)
        self.error_list.setFocus()

    def _refresh_form(self, model: CaseConfig) -> None:
        data = model.model_dump(mode="json")
        self._resolved_data = data
        self._updating = True
        self.name_edit.setText(str(_value(data, "case", "name", default="")))
        self.temperature.setValue(float(_value(data, "physical", "temperature_c", default=0.0)))
        self.pressure.setValue(float(_value(data, "physical", "pressure_bar", default=1.0)))
        enabled = bool(_value(data, "kinetics", "enabled", default=False))
        model_name = _value(data, "kinetics", "model", default=None) if enabled else "disabled"
        self.kinetic_model.setCurrentText(str(model_name or "disabled"))
        explicit_paths = {path for path, _value_item in _scalar_items(self.document.data)} if self.document else set()
        default_paths = sorted(
            _path_text(path) for path, _value_item in _scalar_items(data) if path not in explicit_paths
        )
        self.resolved.setPlainText(
            "Value origins: paths present in source YAML are explicit source inputs; "
            "the following absent paths are approved software defaults:\n"
            + ("\n".join(default_paths) or "(none)")
            + "\n\nResolved configuration (read-only):\n"
            + json.dumps(data, indent=2, ensure_ascii=False)
        )
        self._refresh_explicit_values()
        self._updating = False

    def _sync_after_document_edit(self) -> None:
        if self.document is None:
            return
        try:
            model = CaseConfig.model_validate(self.document.data)
        except Exception as error:
            self._show_error(error)
            return
        self._last_valid_model = model
        self._schema_valid = True
        self._template_mode = False
        self.yaml_text.setPlainText(self.document.to_text())
        self._refresh_form(model)
        self.tabs.setTabEnabled(0, True)
        self._clear_validation_evidence()
        self.document_state_changed.emit("modified" if self.document.is_dirty else "clean")
        self.validation_state_changed.emit("stale")

    def _show_error(self, error: Exception) -> None:
        self.error_list.clear()
        errors = getattr(error, "errors", lambda: [])()
        if errors:
            for item in errors:
                path = ".".join(map(str, item.get("loc", ())))
                self.error_list.addItem(f"{path}: {item.get('msg', str(error))}")
        else:
            self.error_list.addItem(str(error))
        self._set_validation_available(self.error_list.count())
        self.validation_drawer.toggle.setChecked(True)
        self.error_list.setFocus()

    def _navigate_error(self) -> None:
        item = self.error_list.currentItem()
        path = item.text().split(":", 1)[0] if item else ""
        if path.startswith("physical.temperature"):
            self.tabs.setCurrentIndex(0)
            self.temperature.setFocus()
        elif path.startswith("physical.pressure"):
            self.tabs.setCurrentIndex(0)
            self.pressure.setFocus()
        elif path.startswith("case.name"):
            self.tabs.setCurrentIndex(0)
            self.name_edit.setFocus()
        else:
            self.tabs.setCurrentIndex(1)
            self.yaml_text.setFocus()

    def _section_selected(self, section: str) -> None:
        if section == "YAML":
            self.tabs.setCurrentIndex(1)
        elif section == "Validation":
            self._set_validation_available(
                self.error_list.count() if self.error_list.count() else 0,
                evidence_available=True,
            )
            self.validation_drawer.toggle.setChecked(True)
            self.error_list.setFocus()
        else:
            self.tabs.setCurrentIndex(0)
            if section == "Overview":
                self.structured.setCurrentIndex(0)
                return
            keys = {
                "Physical and Brine": ("physical", "brine", "activity_models"),
                "CO₂ and Redox": ("co2", "redox"),
                "Minerals and Kinetics": ("minerals", "kinetics", "database"),
                "Solver": ("solver",),
                "Post-processing": ("postprocessing",),
                "Validation Targets": ("validation",),
                "Outputs": ("outputs", "paths"),
            }.get(section, ())
            data = getattr(self, "_resolved_data", {})
            selected = {key: data.get(key) for key in keys if key in data}
            explicit_paths = {
                path for path, _value_item in _scalar_items(self.document.data)
            } if self.document else set()
            defaults = sorted(
                _path_text(path)
                for path, _value_item in _scalar_items(selected)
                if path not in explicit_paths
            )
            self.resolved.setPlainText(
                "Value origins: source paths are explicit source inputs; approved software defaults in this view:\n"
                + ("\n".join(defaults) or "(none)")
                + "\n\n"
                + json.dumps(selected, indent=2, ensure_ascii=False)
            )
            self.structured.setCurrentIndex(1)

    def _external_change(self, path: str) -> None:
        if self.document and self.document.source_path and Path(path).exists():
            self._external_conflict = True
            self.document_state_changed.emit("external_conflict")
            self.validation_state_changed.emit("stale")
            self.error_list.clear()
            self.error_list.addItem("Source file changed outside the workbench; reload or save elsewhere.")
            self._set_validation_available(1)
            self.validation_drawer.toggle.setChecked(True)

    def _watch(self, path: Path | None) -> None:
        for watched in self._watcher.files():
            self._watcher.removePath(watched)
        if path is not None and path.exists():
            self._watcher.addPath(str(path.resolve()))

"""Case browsing and editing page."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from workbench.widgets.case_editor import CaseEditor
from workbench.widgets.presentation import EmptyState, action_bar, section_card
from workbench.widgets.status import StatusLabel
from workbench_core.documents import CaseDocument

from .common import _friendly, _primary, _set_action_state

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

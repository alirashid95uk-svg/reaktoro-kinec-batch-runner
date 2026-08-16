"""Study design and dataset assembly page."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QStyle,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from workbench.widgets.presentation import EmptyState, action_bar, section_card
from workbench.widgets.status import StatusLabel
from workbench_core.fingerprints import sha256_file
from workbench_core.result_readers import ResultPackage
from workbench_core.studies import save_study_spec_text, validate_study_spec_text

from .common import (
    _combo_value,
    _fill,
    _new_output_directory,
    _quantity_label,
    _read_json,
    _set_action_state,
    _set_combo_options,
    _short_id,
    _table,
)

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

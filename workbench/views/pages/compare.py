"""Saved-result comparison page."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QLabel,
    QLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStyle,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from workbench.widgets.presentation import EmptyState, action_bar, section_card
from workbench.widgets.status import StatusLabel
from workbench_core.result_readers import ResultPackage

from .common import (
    _combo_value,
    _configure_plot,
    _fill,
    _new_output_directory,
    _quantity_label,
    _read_json,
    _set_action_state,
    _set_combo_options,
    _short_id,
    _table,
)

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

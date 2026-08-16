"""Single-result exploration page."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyqtgraph as pg
import pyqtgraph.exporters
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QStyle,
    QTabWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from workbench.widgets.presentation import Disclosure, EmptyState, action_bar, section_card
from workbench.widgets.status import StatusLabel
from workbench_core.result_readers import ResultPackage, time_log_allowed, y_log_allowed

from .common import (
    _combo_value,
    _configure_plot,
    _fill,
    _friendly,
    _quantity_label,
    _set_combo_options,
    _short_id,
    _table,
)

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

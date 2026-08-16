"""Saved-run search and reporting page."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QStyle,
    QTabWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from workbench.widgets.presentation import Disclosure, action_bar, section_card
from workbench_core.run_index import search_runs

from .common import (
    _combo_value,
    _fill,
    _friendly,
    _friendly_time,
    _new_output_directory,
    _read_json,
    _set_action_state,
    _set_combo_options,
    _short_id,
    _table,
)

def _run_filter(name: str, placeholder: str) -> QLineEdit:
    field = QLineEdit()
    field.setAccessibleName(f"Run filter: {name}")
    field.setPlaceholderText(placeholder)
    return field

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

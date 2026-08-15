from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QBrush, QCloseEvent, QColor, QKeyEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QStatusBar,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from workbench.controllers.processes import HeadlessTaskController, ProcessController
from workbench_core.operations import (
    begin_external_queue_entry,
    fail_external_run_controller,
    finalise_external_run,
    finish_external_queue_entry,
    mark_external_queue_entry_running,
    mark_external_run_running,
    mark_external_run_unresponsive,
    recover_queue_record,
    request_queue_cancel_after_current,
    request_queue_pause,
    synchronise_study_sample,
)
from workbench.views.pages import (
    CasesPage,
    ComparePage,
    EnvironmentPage,
    ExplorePage,
    QueuePage,
    RunsPage,
    StudiesPage,
)
from workbench.widgets.status import StatusLabel


PAGE_NAMES = ("Home", "Cases", "Queue", "Runs", "Explore", "Compare", "Studies")
PAGE_DESCRIPTIONS = {
    "Home": "Readiness and activity",
    "Cases": "Edit, validate, and prepare",
    "Queue": "Run validated snapshots",
    "Runs": "Find saved run evidence",
    "Explore": "Inspect one saved package",
    "Compare": "Compare saved results",
    "Studies": "Study and dataset design",
}

OPERATION_OUTCOMES = {
    "validate": "Case validation completed",
    "prepare_run": "Run snapshot prepared",
    "queue_create": "Queue created",
    "authorise_run": "Run authorised",
    "finalise_run": "Run evidence finalised",
    "compare": "Comparison saved",
    "compare_check": "Compatibility check completed",
    "study": "Study generated",
    "dataset": "Dataset assembled",
    "report": "Report generated",
    "rebuild_index": "Run index rebuilt",
    "recover": "Startup recovery completed",
}


def _identify(widget: QWidget, identifier: str, *, set_object_name: bool = True) -> None:
    """Give persistent controls a stable UIA identifier and a readable Qt name."""

    if set_object_name:
        widget.setObjectName(identifier)
    widget.setAccessibleIdentifier(identifier)
    if not widget.accessibleName():
        text = widget.text() if hasattr(widget, "text") and callable(widget.text) else ""
        widget.setAccessibleName(str(text or identifier.replace("_", " ")))


class MainWindow(QMainWindow):
    def __init__(self, project_root: Path, solver_prefix: Path) -> None:
        super().__init__()
        self.project_root, self.solver_prefix = project_root.resolve(), solver_prefix.resolve()
        self.setWindowTitle("Reaktoro Scientific Workbench")
        self.setMinimumSize(960, 600)
        available = QApplication.primaryScreen().availableGeometry()
        self.resize(
            min(1320, max(960, available.width() - 48)),
            min(820, max(600, available.height() - 72)),
        )
        self.setAccessibleName("Reaktoro Scientific Workbench")

        self.navigation = QListWidget()
        self.navigation.setObjectName("sidebar")
        icon_names = (
            QStyle.StandardPixmap.SP_ComputerIcon,
            QStyle.StandardPixmap.SP_FileIcon,
            QStyle.StandardPixmap.SP_MediaPlay,
            QStyle.StandardPixmap.SP_DirOpenIcon,
            QStyle.StandardPixmap.SP_FileDialogDetailedView,
            QStyle.StandardPixmap.SP_FileDialogListView,
            QStyle.StandardPixmap.SP_FileDialogContentsView,
        )
        for name, icon_name in zip(PAGE_NAMES, icon_names):
            item = QListWidgetItem(name)
            item.setForeground(QBrush(QColor(0, 0, 0, 0)))
            item.setToolTip(PAGE_DESCRIPTIONS[name])
            item.setSizeHint(QSize(196, 56))
            self.navigation.addItem(item)
            row = QWidget()
            row.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(8, 4, 8, 4)
            icon = QLabel()
            icon.setPixmap(self.style().standardIcon(icon_name).pixmap(20, 20))
            text = QWidget()
            text_layout = QVBoxLayout(text)
            text_layout.setContentsMargins(0, 0, 0, 0)
            text_layout.setSpacing(0)
            title = QLabel(name)
            title.setObjectName("cardTitle")
            description = QLabel(PAGE_DESCRIPTIONS[name])
            description.setObjectName("mutedText")
            description.setStyleSheet("font-size: 9pt;")
            text_layout.addWidget(title)
            text_layout.addWidget(description)
            row_layout.addWidget(icon)
            row_layout.addWidget(text, 1)
            self.navigation.setItemWidget(item, row)
        self.navigation.setAccessibleName("Primary workbench navigation")
        self.navigation.setAccessibleDescription("Seven permanent workspaces; use Control 1 through Control 7")
        self.navigation.setFixedWidth(206)
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.pages = QStackedWidget()
        self.pages.setObjectName("workspaceStack")
        self.pages.setAccessibleName("Current workbench page")
        self.home = EnvironmentPage(self.project_root, self.solver_prefix)
        self.cases = CasesPage(self.project_root)
        self.queue = QueuePage()
        self.runs = RunsPage(self.project_root)
        self.explore = ExplorePage()
        self.compare = ComparePage()
        self.studies = StudiesPage()
        for page in (
            self.home,
            self.cases,
            self.queue,
            self.runs,
            self.explore,
            self.compare,
            self.studies,
        ):
            self.pages.addWidget(page)
        central = QWidget()
        central.setObjectName("workbenchRoot")
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.navigation)
        workspace = QWidget()
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(16, 12, 16, 12)
        workspace_layout.setSpacing(12)
        header = QFrame()
        header.setObjectName("pageHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 12, 16, 12)
        heading = QWidget()
        heading_layout = QVBoxLayout(heading)
        heading_layout.setContentsMargins(0, 0, 0, 0)
        heading_layout.setSpacing(2)
        self.page_title = QLabel(PAGE_NAMES[0])
        self.page_title.setObjectName("pageTitle")
        self.page_subtitle = QLabel(PAGE_DESCRIPTIONS[PAGE_NAMES[0]])
        self.page_subtitle.setObjectName("pageSubtitle")
        heading_layout.addWidget(self.page_title)
        heading_layout.addWidget(self.page_subtitle)
        self.operation_status = StatusLabel("Current workbench operation")
        self.operation_status.set_status(
            "Ready · Workbench opened",
            QStyle.StandardPixmap.SP_DialogApplyButton,
            tone="success",
        )
        self._last_successful_operation = "Workbench opened"
        self._operation_reset = QTimer(self)
        self._operation_reset.setSingleShot(True)
        self._operation_reset.setInterval(8_000)
        self._operation_reset.timeout.connect(self._reset_operation_status)
        header_layout.addWidget(heading, 1)
        header_layout.addWidget(self.operation_status)
        workspace_layout.addWidget(header)
        workspace_layout.addWidget(self.pages, 1)
        layout.addWidget(workspace, 1)
        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())
        self.statusBar().setAccessibleName("Workbench status messages")
        self._assign_accessibility_identifiers()

        self.task_controller = HeadlessTaskController(self)
        self.queue_controller = ProcessController(self)
        self._active_run_record_path: Path | None = None
        self._close_when_idle = False
        self._queue_path = self.project_root / ".workbench" / "queues" / "active_queue.json"
        self.queue.restore_queue(self._queue_path)
        self.navigation.currentRowChanged.connect(self._select_page)
        self.navigation.setCurrentRow(0)
        self.queue.graceful_cancel_requested.connect(self.queue_controller.request_cancel)
        self.queue.force_terminate_requested.connect(self.queue_controller.force_terminate)
        self.queue.run_requested.connect(self._create_queue)
        self.queue.pause_after_current_requested.connect(self._pause_queue)
        self.queue.cancel_after_current_requested.connect(self._cancel_after_current)
        self.queue.resume_requested.connect(self._resume_queue)
        self.cases.validation_requested.connect(lambda _path: self.validate_current_case())
        self.cases.prepare_requested.connect(self.prepare_current_case)
        self.compare.check_requested.connect(self._compare_check)
        self.compare.export_requested.connect(self._compare)
        self.compare.report_requested.connect(self._report)
        self.studies.study_requested.connect(self._study)
        self.studies.prepare_sample_requested.connect(self._prepare_study_sample)
        self.studies.dataset_requested.connect(self._dataset)
        self.studies.report_requested.connect(self._report)
        self.runs.report_requested.connect(self._report)
        self.runs.rebuild_requested.connect(
            lambda: self._start_task("rebuild_index", ["rebuild-index"])
        )
        self.task_controller.succeeded.connect(self._task_succeeded)
        self.task_controller.failed.connect(self._task_failed)
        self.task_controller.status_changed.connect(
            self._set_operation_working
        )
        self.task_controller.log_received.connect(
            lambda text: self.statusBar().showMessage(text.strip(), 5000)
        )
        self.queue_controller.event_received.connect(self._event)
        self.queue_controller.started.connect(self._solver_started)
        self.queue_controller.finished.connect(self._solver_finished)
        self.queue_controller.cancel_unresponsive.connect(self._solver_unresponsive)
        self.queue_controller.status_changed.connect(
            self._set_operation_working
        )
        self.queue_controller.protocol_problem.connect(self._protocol_problem)
        self.queue_controller.log_received.connect(
            lambda text: self.statusBar().showMessage(text.strip(), 5000)
        )
        self.runs.run_selected.connect(self._open_run)
        self._actions()
        QWidget.setTabOrder(self.navigation, self.pages)

    def _select_page(self, index: int) -> None:
        if not 0 <= index < len(PAGE_NAMES):
            return
        self.pages.setCurrentIndex(index)
        name = PAGE_NAMES[index]
        self.page_title.setText(name)
        self.page_title.setAccessibleName(name)
        self.page_subtitle.setText(PAGE_DESCRIPTIONS[name])
        self.pages.setAccessibleDescription(f"Current workspace: {name}")
        self.pages.widget(index).setFocus(Qt.FocusReason.OtherFocusReason)

    def _assign_accessibility_identifiers(self) -> None:
        _identify(self, "workbenchMainWindow")
        _identify(self.navigation, "primaryNavigation", set_object_name=False)
        _identify(self.pages, "workspaceStack")
        _identify(self.page_title, "pageTitle")
        _identify(self.page_subtitle, "pageSubtitle")
        _identify(self.operation_status, "operationStatus", set_object_name=False)
        _identify(self.statusBar(), "workbenchStatusBar")

        for page, identifier in (
            (self.home, "homePage"),
            (self.cases, "casesPage"),
            (self.queue, "queuePage"),
            (self.runs, "runsPage"),
            (self.explore, "explorePage"),
            (self.compare, "comparePage"),
            (self.studies, "studiesPage"),
        ):
            _identify(page, identifier)

        targets = (
            (self.home.refresh_button, "homeEnvironmentDoctor"),
            (self.cases.search, "casesSearch"),
            (self.cases.case_list, "casesList"),
            (self.cases.open_button, "casesOpen"),
            (self.cases.new_button, "casesNew"),
            (self.cases.import_button, "casesImport"),
            (self.cases.duplicate_button, "casesDuplicate"),
            (self.cases.archive_button, "casesArchive"),
            (self.cases.editor_stack, "casesDocumentStack"),
            (self.cases.save_button, "casesSave"),
            (self.cases.validate_button, "casesValidate"),
            (self.cases.prepare_button, "casesPrepare"),
            (self.queue.policy, "queuePolicy"),
            (self.queue.workers, "queueWorkers"),
            (self.queue.table, "queueTable"),
            (self.queue.start_button, "queueStart"),
            (self.queue.move_up_button, "queueMoveUp"),
            (self.queue.move_down_button, "queueMoveDown"),
            (self.queue.pause_button, "queuePause"),
            (self.queue.cancel_after_button, "queueCancelAfter"),
            (self.queue.graceful_button, "queueGracefulCancel"),
            (self.queue.force_button, "queueForceTerminate"),
            (self.queue.execution_controls, "queueExecutionControls"),
            (self.queue.progress, "queueProgress"),
            (self.queue.monitor_details.toggle, "queueMonitorToggle"),
            (self.runs.search, "runsSearch"),
            (self.runs.table, "runsTable"),
            (self.runs.refresh_button, "runsRebuild"),
            (self.runs.result_count, "runsResultCount"),
            (self.runs.filter_details.toggle, "runsFiltersToggle"),
            (self.runs.detail_tabs, "runsEvidence"),
            (self.runs.report_type, "runsReportType"),
            (self.runs.report_button, "runsReport"),
            (self.explore.open_button, "exploreOpen"),
            (self.explore.package_name, "explorePackageTitle"),
            (self.explore.summary, "exploreSummary"),
            (self.explore.variable_search, "exploreQuantitySearch"),
            (self.explore.result_group, "exploreQuantityGroup"),
            (self.explore.quantity, "exploreQuantity"),
            (self.explore.time_unit, "exploreTimeUnit"),
            (self.explore.y_log, "exploreYLog"),
            (self.explore.time_log, "exploreTimeLog"),
            (self.explore.plot, "explorePlot"),
            (self.explore.tabs, "exploreTabs"),
            (self.explore.table, "exploreExactData"),
            (self.explore.reset_button, "exploreReset"),
            (self.explore.export_button, "exploreExport"),
            (self.explore.figure_button, "exploreSaveFigure"),
            (self.explore.copy_button, "exploreCopy"),
            (self.compare.run_paths, "compareRunList"),
            (self.compare.add_button, "compareAdd"),
            (self.compare.remove_button, "compareRemove"),
            (self.compare.load_button, "compareOpenSaved"),
            (self.compare.quantity, "compareQuantity"),
            (self.compare.mode, "compareMode"),
            (self.compare.tolerance, "compareTolerance"),
            (self.compare.check_button, "compareCheck"),
            (self.compare.save_button, "compareSave"),
            (self.compare.report_button, "compareReport"),
            (self.compare.tabs, "compareTabs"),
            (self.compare.plot, "comparePlot"),
            (self.compare.data, "compareExactData"),
            (self.studies.study_tabs, "studiesTabs"),
            (self.studies.spec_editor, "studiesSpecEditor"),
            (self.studies.open_spec_button, "studiesOpenSpec"),
            (self.studies.validate_spec_button, "studiesValidateSpec"),
            (self.studies.save_spec_button, "studiesSaveSpec"),
            (self.studies.generate_button, "studiesGenerate"),
            (self.studies.parameters, "studiesParameters"),
            (self.studies.open_manifest_button, "studiesOpenManifest"),
            (self.studies.samples, "studiesSamples"),
            (self.studies.prepare_sample_button, "studiesPrepareSample"),
            (self.studies.packages, "studiesPackages"),
            (self.studies.add_package_button, "studiesAddPackage"),
            (self.studies.features.list, "studiesFeatureChecklist"),
            (self.studies.targets.list, "studiesTargetChecklist"),
            (self.studies.dataset_button, "studiesAssembleDataset"),
            (self.studies.report_type, "studiesReportType"),
            (self.studies.report_button, "studiesGenerateReport"),
        )
        for widget, identifier in targets:
            _identify(widget, identifier)

        for status, identifier in (
            (self.cases.document_status, "casesDocumentStatus"),
            (self.cases.validation_status, "casesValidationStatus"),
            (self.queue.status, "queueStatus"),
            (self.compare.status, "compareStatus"),
            (self.studies.status, "studiesStatus"),
        ):
            _identify(status, identifier, set_object_name=False)
        _identify(self.cases.editor_empty, "casesNoDocumentState", set_object_name=False)

        compare_stages = {
            "1": "compareStagePackages",
            "2": "compareStageConfiguration",
            "3": "compareStageCompatibility",
            "4": "compareStageOutput",
        }
        for frame in self.compare.findChildren(QFrame):
            title = frame.accessibleName().strip()
            identifier = compare_stages.get(title[:1]) if title else None
            if identifier:
                _identify(frame, identifier, set_object_name=False)

    def _set_operation_working(self, operation: str) -> None:
        self._operation_reset.stop()
        self.operation_status.set_status(
            f"Working · {operation}",
            QStyle.StandardPixmap.SP_BrowserReload,
            tone="busy",
        )

    def _set_operation_ready(self, operation: str) -> None:
        outcome = OPERATION_OUTCOMES.get(operation, operation.replace("_", " ").title())
        self._last_successful_operation = outcome
        self.operation_status.set_status(
            f"Ready · {outcome}",
            QStyle.StandardPixmap.SP_DialogApplyButton,
            tone="success",
        )
        self._operation_reset.start()

    def _set_operation_attention(self, operation: str) -> None:
        label = operation.replace("_", " ").title()
        self.operation_status.set_status(
            f"Attention · {label} failed",
            QStyle.StandardPixmap.SP_MessageBoxCritical,
            tone="failure",
        )
        self._operation_reset.start()

    def _reset_operation_status(self) -> None:
        self.operation_status.set_status(
            f"Ready · {self._last_successful_operation}",
            QStyle.StandardPixmap.SP_DialogApplyButton,
            tone="success",
        )

    def _protocol_problem(self, detail: str) -> None:
        self.operation_status.set_status(
            "Attention · Solver protocol warning",
            QStyle.StandardPixmap.SP_MessageBoxWarning,
            tone="warning",
        )
        self._operation_reset.start()
        self.queue.status.set_status(
            f"Protocol warning: {detail}", QStyle.StandardPixmap.SP_MessageBoxWarning
        )

    def _actions(self) -> None:
        self._navigation_shortcuts = []
        file_menu = self.menuBar().addMenu("&File")
        open_action = QAction("Open case", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self._open_case)
        file_menu.addAction(open_action)
        save_action = QAction("Save case", self)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self.cases.editor.save)
        file_menu.addAction(save_action)
        case_menu = self.menuBar().addMenu("&Case")
        self.validate_case_action = QAction("Validate current case", self)
        self.validate_case_action.setObjectName("validateCurrentCaseAction")
        self.validate_case_action.setShortcut(QKeySequence("Ctrl+Shift+V"))
        self.validate_case_action.triggered.connect(self.validate_current_case)
        case_menu.addAction(self.validate_case_action)
        self.cases.editor.document_state_changed.connect(
            lambda _state: self._refresh_case_menu_action()
        )
        self.cases.editor.validation_state_changed.connect(
            lambda _state: self._refresh_case_menu_action()
        )
        self.cases.editor.case_saved.connect(
            lambda _path: self._refresh_case_menu_action()
        )
        self._refresh_case_menu_action()
        navigation_menu = self.menuBar().addMenu("&Navigate")
        for index, name in enumerate(PAGE_NAMES):
            action = QAction(name, self)
            action.triggered.connect(lambda _checked=False, row=index: self.navigation.setCurrentRow(row))
            navigation_menu.addAction(action)
            shortcut = QShortcut(QKeySequence(f"Ctrl+{index + 1}"), self)
            shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            shortcut.activated.connect(lambda row=index: self.navigation.setCurrentRow(row))
            self._navigation_shortcuts.append(shortcut)

    def _refresh_case_menu_action(self) -> None:
        self.validate_case_action.setEnabled(self.cases.validate_button.isEnabled())

    def validate_current_case(self) -> None:
        document = self.cases.editor.document
        if (
            document is None
            or document.source_path is None
            or document.is_dirty
            or self.cases.editor.form_is_stale
        ):
            self.statusBar().showMessage("Save a current, valid case before validation", 6000)
            return
        self._start_task(
            "validate",
            [
                "validate",
                str(document.source_path),
                "--solver-prefix",
                str(self.solver_prefix),
            ],
        )
        self.cases.editor.validation_state_changed.emit("checking")

    def prepare_current_case(self, path: str | None = None) -> None:
        document = self.cases.editor.document
        source = Path(path) if path else document.source_path if document else None
        if source is None or document is None or document.is_dirty or self.cases.editor.form_is_stale:
            self.statusBar().showMessage("Save a current, valid case before preparing a run", 6000)
            return
        self._start_task(
            "prepare_run",
            ["prepare-run", str(source), "--solver-prefix", str(self.solver_prefix)],
        )

    def _create_queue(self, request: dict) -> None:
        self._queue_path.parent.mkdir(parents=True, exist_ok=True)
        self._start_task(
            "queue_create",
            [
                "queue-create",
                str(self._queue_path),
                *request["run_records"],
                "--failure-policy",
                request["failure_policy"],
            ],
        )

    def _pause_queue(self) -> None:
        if not self._queue_path.is_file():
            return
        try:
            record = request_queue_pause(self._queue_path)
        except Exception as error:
            self._queue_failed("queue_pause", str(error))
            return
        self.queue.status.set_status(
            "Pause after current persisted; active solver call continues",
            QStyle.StandardPixmap.SP_MediaPause,
        )
        self._apply_queue_entries(record.model_dump(mode="json"))

    def _cancel_after_current(self) -> None:
        if not self._queue_path.is_file():
            return
        try:
            record = request_queue_cancel_after_current(self._queue_path)
        except Exception as error:
            self._queue_failed("queue_cancel", str(error))
            return
        self._apply_queue_entries(record.model_dump(mode="json"))

    def _resume_queue(self) -> None:
        if not self._queue_path.is_file() or self.queue_controller.is_active:
            return
        self._start_next_queue_entry()

    def _event(self, event: dict) -> None:
        self.queue.update_monitor(event, self.queue_controller.elapsed_seconds)
        event_type = event.get("event_type", "event")
        payload = event.get("payload", {})
        if event_type == "process_started":
            self.queue.mark_running(str(event.get("run_id", "")))
        if event_type == "progress_summary":
            accepted = payload.get("accepted_time_s", payload.get("accepted_simulation_time_s", "unknown"))
            requested = payload.get("requested_duration_s", "unknown")
            self.statusBar().showMessage(f"Accepted simulation time {accepted} of {requested} seconds")
        elif event_type == "validation_issue":
            self.cases.editor.validation_state_changed.emit("blocked")
        elif event_type == "stage_completed" and payload.get("stage") == "preflight":
            self.cases.editor.validation_state_changed.emit("ready")

    def _task_succeeded(self, operation: str, result: object) -> None:
        if not isinstance(result, dict) and operation != "recover":
            self._task_failed(operation, "headless operation returned an unexpected record type")
            return
        self._set_operation_ready(operation)
        if operation == "validate":
            ready = result.get("ready") is True
            self.cases.editor.show_validation_receipt(result)
            self.cases.editor.validation_state_changed.emit("ready" if ready else "blocked")
            self.cases.validation_status.set_status(
                "Ready" if ready else f"Blocked: {result.get('failed_stage', 'preflight failed')}",
                QStyle.StandardPixmap.SP_DialogApplyButton
                if ready
                else QStyle.StandardPixmap.SP_MessageBoxWarning,
            )
        elif operation == "prepare_run":
            try:
                self.queue.add_prepared_run(result)
            except Exception as error:
                self._task_failed(operation, str(error))
                return
            self.navigation.setCurrentRow(PAGE_NAMES.index("Queue"))
            self.queue.status.set_status(
                "Ready snapshot added to queue",
                QStyle.StandardPixmap.SP_DialogApplyButton,
            )
            if result.get("study_id") and self.studies.study_manifest_path:
                self.studies.load_manifest(self.studies.study_manifest_path)
        elif operation == "queue_create":
            self._apply_queue_entries(result)
            self._start_next_queue_entry()
        elif operation == "authorise_run":
            if result.get("state") != "starting":
                self._finish_active_entry(result)
                return
            self.queue_controller.launch_solver(
                project_root=self.project_root,
                solver_prefix=self.solver_prefix,
                case_path=result["snapshot_path"],
                run_dir=Path(result["snapshot_path"]).parent,
                run_id=result["run_id"],
                case_id=result["case_id"],
            )
        elif operation == "finalise_run":
            self._finish_active_entry(result)
        elif operation == "compare":
            preview_ready = self.compare.set_saved_artifacts(
                result["specification"], result["data"]
            )
            self.compare.status.set_status(
                (
                    f"Saved: {result.get('specification')}"
                    if preview_ready
                    else f"Saved, but preview unavailable: {result.get('specification')}"
                ),
                QStyle.StandardPixmap.SP_DialogApplyButton
                if preview_ready
                else QStyle.StandardPixmap.SP_MessageBoxWarning,
            )
        elif operation == "compare_check":
            self.compare.apply_compatibility(result)
        elif operation == "study":
            self.studies.load_manifest(result["manifest"])
            self.studies.status.set_status(
                f"Study generated: {result.get('manifest')}",
                QStyle.StandardPixmap.SP_DialogApplyButton,
            )
        elif operation == "dataset":
            self.studies.load_dataset_manifest(result["manifest"])
            self.studies.status.set_status(
                f"Dataset assembled: {result.get('manifest')}",
                QStyle.StandardPixmap.SP_DialogApplyButton,
            )
        elif operation == "report":
            self.statusBar().showMessage(f"Report generated: {result.get('markdown')}", 10000)
        elif operation == "rebuild_index":
            self.runs.refresh()
            self.home.refresh_activity()
            self.statusBar().showMessage(
                f"Run index rebuilt from {result.get('run_count', 0)} artifact records", 10000
            )
        elif operation == "recover":
            count = len(result) if isinstance(result, list) else 0
            study_sync_errors = [
                item["study_manifest_sync_error"]
                for item in result
                if isinstance(item, dict) and item.get("study_manifest_sync_error")
            ] if isinstance(result, list) else []
            if self._queue_path.is_file():
                try:
                    recovered_queue = recover_queue_record(self._queue_path)
                    self._apply_queue_entries(recovered_queue.model_dump(mode="json"))
                    self.queue.set_persisted_state(recovered_queue.queue_state.value)
                except Exception as error:
                    self.statusBar().showMessage(f"Queue recovery warning: {error}", 10000)
            recovery_message = f"Startup recovery classified {count} interrupted run record(s)"
            if study_sync_errors:
                recovery_message += f"; {len(study_sync_errors)} study manifest sync warning(s)"
            self.statusBar().showMessage(recovery_message, 10000)
            self._start_task("rebuild_index", ["rebuild-index"])
        self._close_if_requested()

    def _task_failed(self, operation: str, detail: str) -> None:
        self._set_operation_attention(operation)
        self.statusBar().showMessage(f"{operation.replace('_', ' ').title()} failed: {detail}")
        if operation == "finalise_run" and self._active_run_record_path is not None:
            try:
                record = finalise_external_run(
                    self._active_run_record_path,
                    self.queue_controller.event_path or self._active_run_record_path.parent / "events.jsonl",
                    return_code=self.queue_controller.process.exitCode(),
                    force_requested=self.queue_controller.force_confirmed,
                )
            except Exception as error:
                record = fail_external_run_controller(
                    self._active_run_record_path,
                    f"run finalisation failed: {detail}; fallback failed: {error}",
                )
            self._finish_active_entry(record.model_dump(mode="json"))
        elif operation == "authorise_run" and self._active_run_record_path is not None:
            record = fail_external_run_controller(
                self._active_run_record_path, f"prelaunch controller failed: {detail}"
            )
            self._finish_active_entry(record.model_dump(mode="json"))
        elif operation == "validate":
            self.cases.editor.validation_state_changed.emit("validation_process_failed")
        elif operation in {"study", "dataset"}:
            self.studies.status.set_status(
                f"Blocked: {detail}",
                QStyle.StandardPixmap.SP_MessageBoxWarning,
            )
        elif operation in {"compare", "compare_check"}:
            self.compare.status.set_status(
                f"Blocked: {detail}",
                QStyle.StandardPixmap.SP_MessageBoxWarning,
            )
        elif operation in {"report", "rebuild_index"}:
            self.statusBar().showMessage(f"{operation.replace('_', ' ').title()} failed: {detail}")
        else:
            self.queue.finish_queue("blocked")
            self.queue.status.set_status(
                f"Blocked: {detail}",
                QStyle.StandardPixmap.SP_MessageBoxWarning,
            )
        self._close_if_requested()

    def _apply_queue_entries(self, result: dict) -> None:
        entries = {entry["run_id"]: entry for entry in result.get("entries", [])}
        for row in range(self.queue.table.rowCount()):
            run_id = self.queue._run_id_at(row)
            if run_id in entries:
                self.queue.table.item(row, 2).setText(entries[run_id]["entry_state"])
                self.queue.table.item(row, 4).setText(
                    entries[run_id].get("status_reason") or "None"
                )

    def _queue_failed(self, _operation: str, detail: str) -> None:
        self._set_operation_attention("queue")
        self.queue.status.set_status(
            f"Queue interrupted: {detail}",
            QStyle.StandardPixmap.SP_MessageBoxWarning,
        )
        self.queue.finish_queue("interrupted")
        self.runs.refresh()

    def _start_next_queue_entry(self) -> None:
        try:
            queue, record = begin_external_queue_entry(self._queue_path)
        except Exception as error:
            self._queue_failed("queue", str(error))
            return
        self._apply_queue_entries(queue.model_dump(mode="json"))
        if record is None:
            self.queue.set_persisted_state(queue.queue_state.value)
            self._close_if_requested()
            return
        self._active_run_record_path = Path(record.snapshot_path).parent / "run_record.json"
        self.queue.begin_execution()
        self._start_task(
            "authorise_run",
            [
                "authorise-run",
                str(self._active_run_record_path),
                "--solver-prefix",
                str(self.solver_prefix),
            ],
        )

    def _solver_started(self, pid: int) -> None:
        if self._active_run_record_path is None:
            self.queue_controller.force_terminate()
            return
        try:
            record = mark_external_run_running(
                self._active_run_record_path,
                child_pid=pid,
                executable=self.queue_controller.command[0],
                command=self.queue_controller.command,
            )
            queue = mark_external_queue_entry_running(self._queue_path, record.run_id)
        except Exception as error:
            self.queue.status.set_status(
                f"Controller evidence failure: {error}",
                QStyle.StandardPixmap.SP_MessageBoxCritical,
            )
            self.queue_controller.force_terminate()
            return
        self._apply_queue_entries(queue.model_dump(mode="json"))
        self.queue.mark_running(record.run_id)

    def _solver_unresponsive(self) -> None:
        self.operation_status.set_status(
            "Attention · Solver is not responding",
            QStyle.StandardPixmap.SP_MessageBoxWarning,
            tone="warning",
        )
        if self._active_run_record_path is None:
            return
        try:
            mark_external_run_unresponsive(self._active_run_record_path)
        except Exception as error:
            self.statusBar().showMessage(f"Could not persist unresponsive state: {error}", 10000)

    def _solver_finished(self, exit_code: int, _classification: str) -> None:
        if self._active_run_record_path is None or self.queue_controller.event_path is None:
            self._queue_failed("solver", "active run evidence path is missing")
            return
        arguments = [
            "finalise-run",
            str(self._active_run_record_path),
            str(self.queue_controller.event_path),
            "--return-code",
            str(exit_code),
        ]
        if self.queue_controller.force_confirmed:
            arguments.append("--force-requested")
        self._start_task("finalise_run", arguments)

    def _finish_active_entry(self, result: dict) -> None:
        try:
            from workbench_core.schemas.run_record import RunRecord, RunState

            queue = finish_external_queue_entry(
                self._queue_path, str(result["run_id"]), RunState(str(result["state"]))
            )
        except Exception as error:
            self._queue_failed("queue finalisation", str(error))
            return
        try:
            manifest = synchronise_study_sample(
                self.project_root, RunRecord.model_validate(result)
            )
            if manifest:
                self.studies.load_manifest(manifest)
        except Exception as error:
            self.statusBar().showMessage(
                f"Run classified, but study manifest synchronisation failed: {error}", 10000
            )
        self._apply_queue_entries(queue.model_dump(mode="json"))
        self.queue.mark_finished(str(result["run_id"]), str(result["state"]))
        self._active_run_record_path = None
        if queue.queue_state.value == "running":
            self._start_next_queue_entry()
        else:
            self.queue.set_persisted_state(queue.queue_state.value)
            self.runs.refresh()
            self.home.refresh_activity()
            self._close_if_requested()

    def _close_if_requested(self) -> None:
        if self._close_when_idle and not self.queue_controller.is_active and not self.task_controller.is_active:
            self._close_when_idle = False
            self.close()

    def _compare(self, request: dict) -> None:
        self._start_task(
            "compare",
            [
                "compare",
                request["output_dir"],
                request["quantity"],
                *request["packages"],
                "--mode",
                request["mode"],
                "--tolerance-s",
                str(request["tolerance_s"]),
            ],
        )

    def _compare_check(self, request: dict) -> None:
        self._start_task(
            "compare_check",
            [
                "compare-check",
                request["quantity"],
                *request["packages"],
                "--mode",
                request["mode"],
                "--tolerance-s",
                str(request["tolerance_s"]),
            ],
        )

    def _study(self, specification: str) -> None:
        self._start_task(
            "study",
            ["study-generate", specification, "--solver-prefix", str(self.solver_prefix)],
        )

    def _prepare_study_sample(self, request: dict) -> None:
        self._start_task(
            "prepare_run",
            [
                "prepare-run",
                request["case"],
                "--study-manifest",
                request["manifest"],
                "--sample-id",
                request["sample_id"],
                "--solver-prefix",
                str(self.solver_prefix),
            ],
        )

    def _dataset(self, request: dict) -> None:
        arguments = [
            "dataset-assemble",
            request["output_dir"],
            *request["packages"],
            "--dataset-type",
            request["dataset_type"],
        ]
        for feature in request["features"]:
            arguments.extend(["--feature", feature])
        for target in request["targets"]:
            arguments.extend(["--target", target])
        arguments.extend(
            [
                "--group-by",
                request["group_by"],
                "--seed",
                str(request["seed"]),
                "--duplicate-policy",
                request["duplicate_policy"],
                "--split-train",
                str(request["split_proportions"]["train"]),
                "--split-validation",
                str(request["split_proportions"]["validation"]),
                "--split-test",
                str(request["split_proportions"]["test"]),
            ]
        )
        if request["fixed_time_s"] is not None:
            arguments.extend(
                [
                    "--fixed-time-s",
                    str(request["fixed_time_s"]),
                    "--fixed-time-tolerance-s",
                    str(request["fixed_time_tolerance_s"]),
                ]
            )
        if request["validity_domain_required"]:
            arguments.append("--validity-domain-required")
        if request["qc_requirements_json"]:
            arguments.extend(["--qc-requirements-json", request["qc_requirements_json"]])
        if request["source_study_manifest"]:
            arguments.extend(
                ["--source-study-manifest", request["source_study_manifest"]]
            )
        self._start_task("dataset", arguments)

    def _start_task(self, operation: str, arguments: list[str]) -> None:
        try:
            self.task_controller.start(operation, self.project_root, arguments)
        except RuntimeError as error:
            self._set_operation_attention(operation)
            self.statusBar().showMessage(str(error), 6000)

    def startup_recovery(self) -> None:
        self._start_task("recover", ["recover"])

    def _report(self, request: dict) -> None:
        self._start_task(
            "report",
            [
                "report",
                request["report_type"],
                request["output_dir"],
                *request["sources"],
            ],
        )

    def _open_run(self, directory: str) -> None:
        results = Path(directory) / "results"
        self.navigation.setCurrentRow(PAGE_NAMES.index("Explore"))
        self.explore.load_package(results)

    def _open_case(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open case", str(self.project_root / "cases"), "YAML (*.yaml *.yml)")
        if path:
            self.navigation.setCurrentRow(PAGE_NAMES.index("Cases"))
            self.cases.editor.load_path(path)

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self.queue_controller.is_active and not self.task_controller.is_active:
            event.accept()
            return
        box = QMessageBox(self)
        solver_active = self.queue_controller.is_active
        box.setWindowTitle("A solver process is active" if solver_active else "A workbench operation is active")
        box.setText("The workbench does not leave workers running after the interface closes.")
        return_button = box.addButton("Return to application", QMessageBox.ButtonRole.RejectRole)
        cancel_after = (
            box.addButton("Cancel after current and close when idle", QMessageBox.ButtonRole.ActionRole)
            if solver_active
            else box.addButton("Close when the current operation finishes", QMessageBox.ButtonRole.ActionRole)
        )
        graceful = (
            box.addButton("Request graceful cancellation", QMessageBox.ButtonRole.ActionRole)
            if solver_active or self.task_controller.can_request_cancel
            else None
        )
        force = box.addButton("Force terminate and close", QMessageBox.ButtonRole.DestructiveRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is force:
            self._close_when_idle = True
            if self.queue_controller.is_active:
                self.queue_controller.force_terminate()
            if self.task_controller.is_active:
                self.task_controller.force_terminate()
            event.ignore()
        elif graceful is not None and clicked is graceful:
            if self.queue_controller.is_active:
                self.queue_controller.request_cancel()
            if self.task_controller.is_active:
                self.task_controller.request_cancel()
            event.ignore()
        elif clicked is cancel_after:
            self._close_when_idle = True
            if solver_active:
                self.queue._cancel_pending()
            event.ignore()
        else:
            assert clicked is return_button
            event.ignore()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            index = event.key() - int(Qt.Key.Key_1)
            if 0 <= index < len(PAGE_NAMES):
                self.navigation.setCurrentRow(index)
                event.accept()
                return
        super().keyPressEvent(event)

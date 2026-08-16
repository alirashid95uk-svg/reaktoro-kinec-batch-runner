"""Run queue page."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QStyle,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from workbench.widgets.presentation import Disclosure, action_bar, section_card
from workbench.widgets.status import StatusLabel

from .common import (
    _combo_value,
    _fill,
    _friendly,
    _primary,
    _set_action_state,
    _set_combo_options,
    _short_id,
    _table,
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

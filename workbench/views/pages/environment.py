"""Home and Environment page."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from workbench.controllers.processes import HeadlessTaskController
from workbench.widgets.presentation import Disclosure, section_card
from workbench.widgets.status import StatusLabel
from workbench_core.run_index import search_runs

from .common import (
    _fill,
    _friendly,
    _friendly_time,
    _primary,
    _short_id,
    _table,
)

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

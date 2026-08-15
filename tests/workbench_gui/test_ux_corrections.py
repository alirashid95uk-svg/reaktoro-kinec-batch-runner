from __future__ import annotations

import json
import shutil
from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QPushButton, QScrollArea, QWidget

from workbench.app import create_application
from workbench.main_window import MainWindow, PAGE_NAMES
from workbench.views import pages as page_module
from workbench.widgets.presentation import EmptyState
from workbench_core.comparison import compatibility_gate
from workbench_core.result_readers import ResultPackage
from workbench_core.run_index import rebuild_index


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_CASE = PROJECT_ROOT / "cases" / "source_supported_kinetic_case.yaml"


def _package(root: Path, run_id: str, fingerprint: str = "0" * 64) -> Path:
    results = root / "runs" / run_id / "results"
    results.mkdir(parents=True)
    (results.parent / "run_record.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "case_id": "UX test fixture",
                "state": "completed",
                "scientific_fingerprint": fingerprint,
                "output_completeness": {"status": "complete"},
            }
        ),
        encoding="utf-8",
    )
    (results / "manifest.json").write_text(
        json.dumps(
            {
                "output_schema_version": "objective1_audit_v4",
                "run_identity": {"simulation_completed": True, "run_id": run_id},
                "time_semantics": {"duration_s": 99.0},
                "output_files": ["manifest.json", "diagnostics.json", "timeseries.csv"],
            }
        ),
        encoding="utf-8",
    )
    (results / "diagnostics.json").write_text(
        json.dumps(
            {
                "output_schema_version": "objective1_audit_v4",
                "simulation_completed": True,
                "output_completeness": {"status": "complete"},
                "final_time_reached_s": 99.0,
            }
        ),
        encoding="utf-8",
    )
    rows = [
        f"{index},{7.0 - index / 1000.0},{1.0 + index / 100.0}"
        for index in range(100)
    ]
    (results / "timeseries.csv").write_text(
        "time_s,pH,mineral_amount_mol::Calcite\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )
    return results


def _check(checklist, quantity: str) -> None:
    for index in range(checklist.list.count()):
        item = checklist.list.item(index)
        if item.data(Qt.ItemDataRole.UserRole) == quantity:
            item.setCheckState(Qt.CheckState.Checked)
            return
    raise AssertionError(f"quantity not available: {quantity}")


def test_cases_empty_dirty_clean_validated_and_stale_action_matrix(qtbot, tmp_path: Path) -> None:
    (tmp_path / "cases").mkdir()
    shutil.copy2(SOURCE_CASE, tmp_path / "cases" / SOURCE_CASE.name)
    create_application([])
    window = MainWindow(tmp_path, Path("C:/missing-solver-prefix"))
    qtbot.addWidget(window)

    assert window.cases.editor_stack.currentWidget() is window.cases.editor_empty
    assert not window.cases.editor.isVisibleTo(window.cases)
    assert not any(
        button.isEnabled()
        for button in (
            window.cases.open_button,
            window.cases.duplicate_button,
            window.cases.archive_button,
            window.cases.save_button,
            window.cases.validate_button,
            window.cases.prepare_button,
        )
    )

    window.cases.case_list.setCurrentRow(0)
    assert window.cases.open_button.isEnabled()
    assert window.cases.archive_button.isEnabled()
    assert not window.cases.validate_button.isEnabled()
    window.cases.open_selected()
    assert window.cases.editor_stack.currentWidget() is window.cases.editor
    assert not window.cases.save_button.isEnabled()
    assert window.cases.validate_button.isEnabled()
    assert not window.cases.prepare_button.isEnabled()

    window.cases._validation_state_changed("ready")
    assert window.cases.prepare_button.isEnabled()
    window.cases.editor.name_edit.setText("ux_fixture_changed")
    window.cases.editor.name_edit.editingFinished.emit()
    assert window.cases.save_button.isEnabled()
    assert not window.cases.validate_button.isEnabled()
    assert not window.cases.prepare_button.isEnabled()
    assert window.cases.editor.validation_drawer.isHidden()


def test_queue_and_compare_action_matrices_use_internal_values(qtbot, tmp_path: Path) -> None:
    first = _package(tmp_path, "run-a")
    second = _package(tmp_path, "run-b", "1" * 64)
    create_application([])
    window = MainWindow(tmp_path, Path("C:/missing-solver-prefix"))
    qtbot.addWidget(window)

    assert window.queue.policy.itemText(0) == "Stop after a failure"
    assert window.queue.policy.itemData(0) == "stop_after_failure"
    assert not window.queue.start_button.isEnabled()
    for path, run_id in ((first.parent, "queue-a"), (second.parent, "queue-b")):
        window.queue.add_prepared_run(
            {
                "state": "ready",
                "run_id": run_id,
                "snapshot_path": str(path / "run_case.yaml"),
                "validation_receipt_path": str(path / "validation_receipt.json"),
            }
        )
    window.queue.table.selectRow(0)
    assert window.queue.start_button.isEnabled()
    assert not window.queue.move_up_button.isEnabled()
    assert window.queue.move_down_button.isEnabled()
    window.queue.begin_execution()
    assert not window.queue.start_button.isEnabled()
    assert window.queue.execution_controls.isVisibleTo(window.queue)
    assert not window.queue.move_up_button.isVisibleTo(window.queue)
    window.queue.finish_queue("completed")
    assert not window.queue.execution_controls.isVisibleTo(window.queue)
    assert not window.queue.live_area.isVisibleTo(window.queue)
    window.queue.set_persisted_state("paused")
    assert window.queue.pause_button.text() == "Resume queue"
    assert window.queue.pause_button.isEnabled()
    assert window.queue.execution_controls.isVisibleTo(window.queue)
    window.queue.pause_button.click()
    assert window.queue.start_button.isEnabled()
    assert not window.queue.execution_controls.isVisibleTo(window.queue)

    assert window.compare.mode.itemText(0) == "Native accepted grids"
    assert window.compare.mode.itemData(0) == "native_accepted_grids"
    window.compare.add_package(first)
    assert not window.compare.check_button.isEnabled()
    window.compare.add_package(second)
    window.compare.quantity.setCurrentIndex(window.compare.quantity.findData("pH"))
    assert window.compare.check_button.isEnabled()
    window.compare.apply_compatibility(
        compatibility_gate([ResultPackage(first), ResultPackage(second)], "pH")
    )
    assert window.compare.save_button.isEnabled()
    window.compare._show_preview(
        page_module.pd.DataFrame(
            {
                "run_path": [str(first), str(second)],
                "time_s": [0.0, 0.0],
                "pH": [7.0, 6.9],
            }
        ),
        "pH",
        "unitless",
    )
    window.compare._report_sources = ["comparison_spec.json", "comparison.csv"]
    window.compare.mode.setCurrentIndex(window.compare.mode.findData("final_state"))
    assert not window.compare.save_button.isEnabled()
    assert window.compare.overlay_stack.currentWidget() is window.compare.overlay_empty
    assert window.compare.data.rowCount() == 0
    assert not window.compare._report_sources
    assert "configuration changed" in window.compare.comparison_summary.text().casefold()


def test_runs_explore_and_plot_accessibility(qtbot, tmp_path: Path) -> None:
    package = _package(tmp_path, "run-with-100-states")
    rebuild_index(tmp_path / ".workbench" / "run_index.sqlite", tmp_path / "runs")
    create_application([])
    window = MainWindow(tmp_path, Path("C:/missing-solver-prefix"))
    qtbot.addWidget(window)
    window.show()

    assert not window.runs.report_button.isEnabled()
    window.runs.table.setCurrentCell(0, 0)
    assert "run" in window.runs.selected_run_heading.text().casefold()
    assert window.runs.report_button.isEnabled()
    assert [window.runs.detail_tabs.tabText(i) for i in range(4)] == [
        "Summary",
        "Failure / diagnosis",
        "Provenance",
        "Report",
    ]

    window.explore.load_package(package)
    assert window.explore.result_stack.currentWidget() is window.explore.tabs
    assert window.explore.time_unit.itemText(1) == "Days"
    assert window.explore.time_unit.itemData(1) == "days"
    assert window.explore.table.rowCount() == 100
    lengths = sorted(len(item.getData()[0]) for item in window.explore.plot.listDataItems())
    assert lengths[-1] == 100
    assert lengths[0] <= 24
    assert "saved states" in window.explore.plot_summary.text()
    assert "pH" in window.explore.plot.getPlotItem().titleLabel.text
    assert "Exact saved values" in window.explore.plot.accessibleDescription()
    window.explore.time_unit.setCurrentIndex(window.explore.time_unit.findData("days"))
    assert window.explore.table.horizontalHeaderItem(0).text() == "time_days"
    assert window.explore.plot.getAxis("bottom").labelText == "Time (days)"


def test_studies_dataset_prerequisites_precede_output_dialog(qtbot, monkeypatch, tmp_path: Path) -> None:
    package = _package(tmp_path, "dataset-run")
    create_application([])
    window = MainWindow(tmp_path, Path("C:/missing-solver-prefix"))
    qtbot.addWidget(window)
    studies = window.studies

    assert studies.definition_stack.currentWidget() is studies.definition_empty
    assert studies.parameters_stack.currentWidget() is studies.parameters_empty
    assert studies.samples_stack.currentWidget() is studies.samples_empty
    assert not studies.dataset_button.isEnabled()
    dialogs: list[str] = []
    monkeypatch.setattr(
        page_module,
        "_new_output_directory",
        lambda *_args: dialogs.append("opened") or str(tmp_path / "dataset"),
    )
    studies._request_dataset()
    assert not dialogs

    monkeypatch.setattr(
        page_module.QFileDialog,
        "getExistingDirectory",
        lambda *_args, **_kwargs: str(package),
    )
    studies._add_package()
    assert studies.features.list.count() >= 2
    _check(studies.features, "pH")
    _check(studies.targets, "mineral_amount_mol::Calcite")
    invalid_qc = tmp_path / "invalid-qc.json"
    invalid_qc.write_text("{not valid json", encoding="utf-8")
    studies.qc_requirements.setText(str(invalid_qc))
    assert not studies.dataset_button.isEnabled()
    studies._request_dataset()
    assert not dialogs
    studies.qc_requirements.clear()
    assert studies.dataset_button.isEnabled()
    studies.dataset_requested.disconnect(window._dataset)
    requested = QSignalSpy(studies.dataset_requested)
    studies.dataset_button.click()
    assert dialogs == ["opened"]
    assert requested.count() == 1
    payload = requested.at(0)[0]
    assert payload["dataset_type"] == "final_state"
    assert payload["group_by"] == "run_id"
    assert payload["duplicate_policy"] == "error"
    assert payload["features"] == ["pH"]
    assert payload["targets"] == ["mineral_amount_mol::Calcite"]
    studies.dataset_type.setCurrentIndex(studies.dataset_type.findData("failure"))
    assert studies.features.values() == []
    assert studies.targets.values() == []
    assert not studies.features.isEnabled()
    assert not studies.targets.isEnabled()
    assert studies.dataset_button.isEnabled()


def test_operation_status_resets_without_overwriting_new_work(qtbot, tmp_path: Path) -> None:
    (tmp_path / "cases").mkdir()
    create_application([])
    window = MainWindow(tmp_path, Path("C:/missing-solver-prefix"))
    qtbot.addWidget(window)

    window._set_operation_ready("rebuild_index")
    assert window.operation_status.text() == "Ready · Run index rebuilt"
    window._set_operation_attention("dataset")
    assert window.operation_status.text().startswith("Attention ·")
    assert window._operation_reset.isActive()
    window._reset_operation_status()
    assert window.operation_status.text() == "Ready · Run index rebuilt"
    window._set_operation_attention("report")
    window._set_operation_working("compare")
    assert window.operation_status.text() == "Working · compare"
    assert not window._operation_reset.isActive()


def test_explore_actions_require_plottable_saved_quantity(qtbot, tmp_path: Path) -> None:
    package = _package(tmp_path, "no-plottable-quantity")
    (package / "timeseries.csv").write_text("time_s\n0\n", encoding="utf-8")
    create_application([])
    window = MainWindow(tmp_path, Path("C:/missing-solver-prefix"))
    qtbot.addWidget(window)
    window.show()

    window.explore.load_package(package)
    assert window.explore.quantity.count() == 0
    assert not window.explore.view_actions.isVisibleTo(window.explore)
    assert not window.explore.export_actions.isVisibleTo(window.explore)
    assert not window.explore.figure_button.isEnabled()


def test_valid_zero_parameter_study_uses_explanatory_empty_state(qtbot, tmp_path: Path) -> None:
    study = tmp_path / "study_spec.yaml"
    study.write_text(
        json.dumps(
            {
                "study_schema_version": "1.0",
                "study_id": "existing-cases",
                "study_name": "Existing cases",
                "baseline_case_path": "baseline.yaml",
                "baseline_case_sha256": "0" * 64,
                "baseline_scientific_fingerprint": "1" * 64,
                "sampling_method": "existing_cases",
                "seed": 0,
                "sample_count": 1,
                "parameters": [],
                "constraint_groups": [],
                "cross_parameter_constraints": [],
                "generated_case_directory": "generated",
                "execution_policy": {
                    "max_workers": 1,
                    "failure_policy": "stop_after_failure",
                    "allow_replicates": False,
                },
                "required_outputs": ["timeseries.csv"],
                "validity_domain": {"purpose": "test"},
                "provenance": [
                    {
                        "subject": "existing case selection",
                        "origin": "user_decision",
                        "reference": "test fixture",
                    }
                ],
                "existing_case_paths": ["case.yaml"],
            }
        ),
        encoding="utf-8",
    )
    create_application([])
    window = MainWindow(tmp_path, Path("C:/missing-solver-prefix"))
    qtbot.addWidget(window)
    window.studies.load_study_spec(study)

    assert window.studies.parameters_stack.currentWidget() is window.studies.parameters_empty
    assert "no variable parameters" in window.studies.parameters_empty.body.text().casefold()


def test_initial_focus_identifiers_and_tab_order_are_accessible(qtbot, tmp_path: Path) -> None:
    (tmp_path / "cases").mkdir()
    create_application([])
    window = MainWindow(tmp_path, Path("C:/missing-solver-prefix"))
    qtbot.addWidget(window)
    window.show()
    window.activateWindow()

    expected = {
        "primaryNavigation",
        "pageTitle",
        "operationStatus",
        "casesSearch",
        "casesOpen",
        "queueStart",
        "runsSearch",
        "exploreOpen",
        "compareAdd",
        "studiesOpenSpec",
    }
    identified = {
        widget.accessibleIdentifier(): widget
        for widget in window.findChildren(QWidget)
        if widget.accessibleIdentifier()
    }
    assert expected <= set(identified)
    for identifier in expected:
        assert identified[identifier].accessibleName(), identifier

    forbidden = {"casesSave", "casesValidate", "casesPrepare", "queueStart"}
    for index, _name in enumerate(PAGE_NAMES):
        window.navigation.setCurrentRow(index)
        QApplication.processEvents()
        focused = QApplication.focusWidget()
        assert focused is not None and focused.isVisible() and focused.isEnabled()
        for _ in range(8):
            QTest.keyClick(focused, Qt.Key.Key_Tab)
            QApplication.processEvents()
            focused = QApplication.focusWidget()
            assert focused is not None and focused.isVisible() and focused.isEnabled()
            assert focused.accessibleIdentifier() not in forbidden


def test_visible_actions_fit_supported_sizes_without_root_horizontal_scroll(qtbot) -> None:
    create_application([])
    window = MainWindow(PROJECT_ROOT, Path("C:/missing-solver-prefix"))
    qtbot.addWidget(window)
    window.show()
    for size in ((1024, 600), (1440, 900)):
        window.resize(*size)
        for index, page_name in enumerate(PAGE_NAMES):
            window.navigation.setCurrentRow(index)
            QApplication.processEvents()
            page = window.pages.currentWidget()
            assert not any(
                scroll.horizontalScrollBar().isVisible()
                for scroll in page.children()
                if isinstance(scroll, QScrollArea)
            ), page_name
            visible_buttons = [
                button
                for button in page.findChildren(QPushButton)
                if button.isVisible() and not button.visibleRegion().isEmpty()
            ]
            for button in visible_buttons:
                assert button.visibleRegion().boundingRect().contains(button.rect()), (
                    page_name,
                    button.text(),
                    "partially obscured",
                )
                assert button.width() >= button.fontMetrics().horizontalAdvance(button.text()) + 24, (
                    page_name,
                    button.text(),
                    "text clipped",
                )
                top_left = button.mapTo(page, button.rect().topLeft())
                bottom_right = button.mapTo(page, button.rect().bottomRight())
                assert page.rect().contains(top_left), (page_name, button.text())
                assert page.rect().contains(bottom_right), (page_name, button.text())
            for button_index, button in enumerate(visible_buttons):
                button_rect = button.rect().translated(button.mapTo(page, button.rect().topLeft()))
                for other in visible_buttons[button_index + 1 :]:
                    other_rect = other.rect().translated(other.mapTo(page, other.rect().topLeft()))
                    assert not button_rect.intersects(other_rect), (
                        page_name,
                        button.text(),
                        other.text(),
                        "overlapping actions",
                    )
            for empty_state in page.findChildren(EmptyState):
                if not empty_state.isVisible():
                    continue
                body = empty_state.body
                required = body.fontMetrics().boundingRect(
                    QRect(0, 0, body.width(), 10_000),
                    Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                    body.text(),
                ).height()
                assert body.height() >= required, (page_name, body.text())

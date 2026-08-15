from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton

from workbench.app import create_application, create_splash
from workbench.main_window import MainWindow, PAGE_NAMES
from workbench.controllers.processes import ProcessController
from workbench.widgets.case_editor import CaseEditor
from workbench_core.comparison import compatibility_gate
from workbench_core.documents import CaseDocument
from workbench_core.result_readers import ResultPackage
from workbench_core.run_index import rebuild_index


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_CASE = PROJECT_ROOT / "cases" / "source_supported_kinetic_case.yaml"


def test_cmd_launcher_normalizes_trailing_project_backslash() -> None:
    launcher = (PROJECT_ROOT / "Run Workbench.cmd").read_text(encoding="utf-8")
    assert 'for %%I in ("%~dp0.") do set "PROJECT_ROOT=%%~fI"' in launcher
    assert '--project-root "%PROJECT_ROOT%"' in launcher
    assert '--project-root "%~dp0"' not in launcher


def test_fixed_light_theme_and_startup_splash_are_available_before_main_window(qtbot) -> None:
    app = create_application([])
    assert app.property("workbenchStyle") == "Fusion"
    assert "#2563eb" in app.styleSheet()
    splash = create_splash()
    qtbot.addWidget(splash)
    assert not splash.pixmap().isNull()
    assert splash.accessibleName() == "Workbench startup status"


@pytest.mark.parametrize("size", [(1024, 600), (1440, 900)])
def test_all_workspaces_fit_supported_logical_sizes_without_clipped_buttons(qtbot, size) -> None:
    create_application([])
    window = MainWindow(PROJECT_ROOT, Path("C:/missing-solver-prefix"))
    qtbot.addWidget(window)
    window.resize(*size)
    window.show()
    QApplication.processEvents()

    assert window.minimumWidth() == 960
    assert window.minimumHeight() == 600
    for index in range(len(PAGE_NAMES)):
        window.navigation.setCurrentRow(index)
        QApplication.processEvents()
        page = window.pages.currentWidget()
        assert page.width() <= window.pages.width()
        assert not window.grab().isNull()
        for button in page.findChildren(QPushButton):
            if button.isVisible():
                required = button.fontMetrics().horizontalAdvance(button.text()) + 22
                assert button.width() >= required, (PAGE_NAMES[index], button.text())


def test_progressive_empty_running_completed_failed_and_tab_states(qtbot) -> None:
    create_application([])
    window = MainWindow(PROJECT_ROOT, Path("C:/missing-solver-prefix"))
    qtbot.addWidget(window)
    window.show()

    assert window.explore.result_stack.currentWidget() is window.explore.empty_state
    assert window.compare.overlay_stack.currentWidget() is window.compare.overlay_empty
    assert window.cases.editor.validation_drawer.isHidden()
    assert [window.studies.study_tabs.tabText(i) for i in range(5)] == [
        "Definition",
        "Parameters and Constraints",
        "Samples / QC",
        "Dataset Export",
        "Reports",
    ]

    window.queue.begin_execution()
    assert not window.queue.execution_controls.isHidden()
    assert window.queue.queue_state_summary.text() == "Running"
    window.queue.finish_queue("completed")
    assert window.queue.execution_controls.isHidden()
    assert window.queue.queue_state_summary.text() == "Completed"
    window.queue.finish_queue("failed")
    assert window.queue.queue_state_summary.text() == "Failed"
    window.cases.validate_case()
    assert window.cases.validation_status.text().startswith("Blocked:")


def test_primary_workflow_controls_are_keyboard_operable(qtbot, tmp_path: Path) -> None:
    package = _result_package(tmp_path, "keyboard-run", "0" * 64, model="kinec")
    rebuild_index(tmp_path / ".workbench" / "run_index.sqlite", tmp_path / "runs")
    create_application([])
    window = MainWindow(tmp_path, Path("C:/missing-solver-prefix"))
    qtbot.addWidget(window)
    window.show()

    window.navigation.setCurrentRow(1)
    assert not window.cases.validate_button.isEnabled()
    window.cases.search.setFocus()
    QTest.keyClick(window.cases.validate_button, Qt.Key.Key_Space)
    assert window.cases.validation_status.text() == "Not checked"
    window.cases.editor._show_error(ValueError("Keyboard validation evidence"))
    window.cases.editor.validation_drawer.toggle.setFocus()
    QTest.keyClick(window.cases.editor.validation_drawer.toggle, Qt.Key.Key_Space)
    assert not window.cases.editor.validation_drawer.toggle.isChecked()

    window.navigation.setCurrentRow(3)
    window.runs.run_selected.disconnect(window._open_run)
    selected = QSignalSpy(window.runs.run_selected)
    window.runs.table.setCurrentCell(0, 0)
    window.runs.table.setFocus()
    QTest.keyClick(window.runs.table, Qt.Key.Key_Return)
    assert selected.count() == 1
    window.runs.filter_details.toggle.setFocus()
    QTest.keyClick(window.runs.filter_details.toggle, Qt.Key.Key_Space)
    assert window.runs.filter_details.toggle.isChecked()

    queued = tmp_path / "queued"
    queued.mkdir()
    snapshot = queued / "run_case.yaml"
    snapshot.write_text("case: {}\n", encoding="utf-8")
    (queued / "run_record.json").write_text("{}", encoding="utf-8")
    window.queue.add_prepared_run(
        {
            "state": "ready",
            "run_id": "12345678-1234-1234-1234-123456789abc",
            "snapshot_path": str(snapshot),
            "validation_receipt_path": str(queued / "receipt.json"),
        }
    )
    assert window.queue.table.item(0, 3).text() == "12345678"
    assert window.queue.table.item(0, 3).toolTip().endswith("56789abc")
    window.queue.run_requested.disconnect(window._create_queue)
    requested = QSignalSpy(window.queue.run_requested)
    window.navigation.setCurrentRow(2)
    window.queue.start_button.setFocus()
    QTest.keyClick(window.queue.start_button, Qt.Key.Key_Space)
    assert requested.count() == 1

    window.navigation.setCurrentRow(6)
    window.studies.study_tabs.setCurrentIndex(0)
    window.studies.study_tabs.tabBar().setFocus()
    QTest.keyClick(window.studies.study_tabs.tabBar(), Qt.Key.Key_Right)
    assert window.studies.study_tabs.currentIndex() == 1
    assert package.is_dir()


def test_seven_pages_are_keyboard_navigable_and_accessible(qtbot) -> None:
    create_application([])
    window = MainWindow(PROJECT_ROOT, Path("C:/missing-solver-prefix"))
    qtbot.addWidget(window)
    window.show()

    assert window.navigation.count() == 7
    assert [window.navigation.item(i).text() for i in range(7)] == list(PAGE_NAMES)
    assert all(window.pages.widget(i).accessibleName() for i in range(7))
    assert window.navigation.accessibleName()
    assert window.statusBar().accessibleName()

    window.activateWindow()
    window.setFocus()
    QTest.keySequence(window, QKeySequence("Ctrl+5"))
    qtbot.waitUntil(lambda: window.pages.currentIndex() == 4)
    assert window.explore.table.accessibleName() == "Accessible plot data"
    assert window.explore.summary.accessibleName() == "Written result summary"


def test_editor_preserves_comment_and_raw_error_keeps_last_valid_form(qtbot, tmp_path: Path) -> None:
    path = tmp_path / "case.yaml"
    shutil.copy2(SOURCE_CASE, path)
    original = path.read_bytes()
    editor = CaseEditor()
    qtbot.addWidget(editor)
    editor.load_path(path)
    original_temperature = editor.temperature.value()

    editor.name_edit.setText("changed_name")
    editor.name_edit.editingFinished.emit()
    assert "# Source:" in editor.yaml_text.toPlainText()
    assert editor.document is not None and editor.document.is_dirty
    editor.show_diff()
    assert "-  name:" in editor.diff.toPlainText()
    assert "+  name: changed_name" in editor.diff.toPlainText()
    assert editor.section_completeness.text() == "8 of 8 required sections complete"
    assert "Overview: Complete" in editor.section_completeness.toolTip()

    editor.yaml_text.setPlainText("physical: [not valid")
    assert not editor.apply_yaml()
    assert editor.form_is_stale
    assert editor.temperature.value() == original_temperature
    assert path.read_bytes() == original
    assert editor.error_list.count() == 1
    assert editor.error_list.accessibleName() == "Validation error navigator"


def test_editor_applies_multiple_structured_values_as_one_valid_transaction(qtbot, tmp_path: Path) -> None:
    path = tmp_path / "case.yaml"
    shutil.copy2(SOURCE_CASE, path)
    editor = CaseEditor()
    qtbot.addWidget(editor)
    editor.load_path(path)
    items = {
        editor.explicit_values.topLevelItem(index).text(0): editor.explicit_values.topLevelItem(index)
        for index in range(editor.explicit_values.topLevelItemCount())
    }
    items["physical.temperature_c"].setText(1, "41.5")
    items["physical.pressure_bar"].setText(1, "125.0")

    assert editor.apply_structured_edits()
    assert editor.document is not None and editor.document.data["physical"] == {
        "temperature_c": 41.5,
        "pressure_bar": 125.0,
    }
    assert "# Source:" in editor.yaml_text.toPlainText()
    assert editor.document.is_dirty


def test_template_structured_view_replaces_values_renames_keys_and_removes_fields(qtbot) -> None:
    editor = CaseEditor()
    qtbot.addWidget(editor)
    editor.load_document(CaseDocument.load(PROJECT_ROOT / "cases" / "schema_template.yaml"))
    assert editor.tabs.isTabEnabled(0)
    assert editor.form_is_stale

    def item(path: str):
        return next(
            editor.explicit_values.topLevelItem(index)
            for index in range(editor.explicit_values.topLevelItemCount())
            if editor.explicit_values.topLevelItem(index).text(0) == path
        )

    item("case.name").setText(1, "new_source_supported_case")
    assert editor.apply_structured_edits()
    assert editor.document is not None
    assert editor.document.data["case"]["name"] == "new_source_supported_case"
    assert "name: new_source_supported_case" in editor.yaml_text.toPlainText()

    key_row = item("brine.species_amounts.REQUIRED_SPECIES_NAME")
    editor.explicit_values.setCurrentItem(key_row)
    editor.rename_key_edit.setText("Na+")
    editor._mark_placeholder_key_rename()
    assert editor.apply_structured_edits()
    assert "Na+" in editor.document.data["brine"]["species_amounts"]

    gas_row = item("activity_models.gas")
    editor.explicit_values.setCurrentItem(gas_row)
    editor._mark_structured_removal()
    assert editor.apply_structured_edits()
    assert "gas" not in editor.document.data["activity_models"]


def test_template_structured_failure_is_transactional_and_values_are_keyboard_editable(qtbot) -> None:
    editor = CaseEditor()
    qtbot.addWidget(editor)
    editor.load_document(CaseDocument.load(PROJECT_ROOT / "cases" / "schema_template.yaml"))
    name = next(
        editor.explicit_values.topLevelItem(index)
        for index in range(editor.explicit_values.topLevelItemCount())
        if editor.explicit_values.topLevelItem(index).text(0) == "case.name"
    )
    original = editor.document.to_text()
    name.setText(1, "changed")
    name.setData(0, Qt.ItemDataRole.UserRole + 4, "name")
    assert not editor.apply_structured_edits()
    assert editor.document.to_text() == original

    editor._refresh_explicit_values()
    editor.show()
    name = next(
        editor.explicit_values.topLevelItem(index)
        for index in range(editor.explicit_values.topLevelItemCount())
        if editor.explicit_values.topLevelItem(index).text(0) == "case.name"
    )
    editor.explicit_values.setCurrentItem(name, 1)
    editor.explicit_values.setFocus()
    qtbot.waitUntil(editor.explicit_values.hasFocus)
    existing_editors = set(editor.explicit_values.findChildren(QLineEdit))
    qtbot.keyClick(editor.explicit_values, Qt.Key.Key_F2)
    inline_editor = next(widget for widget in editor.explicit_values.findChildren(QLineEdit) if widget not in existing_editors)
    inline_editor.selectAll()
    qtbot.keyClicks(inline_editor, "keyboard_case")
    qtbot.keyClick(inline_editor, Qt.Key.Key_Tab)
    QApplication.processEvents()
    assert name.text(1) == "keyboard_case"


def test_structured_list_removals_use_descending_indices(qtbot) -> None:
    editor = CaseEditor()
    qtbot.addWidget(editor)
    editor.load_document(CaseDocument.load(SOURCE_CASE))
    original = list(editor.document.data["brine"]["aqueous_elements"])
    for index in (0, 1):
        row = next(
            editor.explicit_values.topLevelItem(row_index)
            for row_index in range(editor.explicit_values.topLevelItemCount())
            if editor.explicit_values.topLevelItem(row_index).text(0) == f"brine.aqueous_elements[{index}]"
        )
        row.setData(0, Qt.ItemDataRole.UserRole + 3, True)
    assert editor.apply_structured_edits()
    assert editor.document.data["brine"]["aqueous_elements"] == original[2:]


def test_editor_schema_error_is_stale_and_external_change_is_not_overwritten(qtbot, tmp_path: Path) -> None:
    path = tmp_path / "case.yaml"
    shutil.copy2(SOURCE_CASE, path)
    editor = CaseEditor()
    qtbot.addWidget(editor)
    editor.load_path(path)

    editor.yaml_text.setPlainText(editor.yaml_text.toPlainText() + "\nunknown_field: 1\n")
    assert not editor.apply_yaml()
    assert editor.form_is_stale

    editor.load_path(path)
    editor.name_edit.setText("local_change")
    editor.name_edit.editingFinished.emit()
    path.write_text(path.read_text(encoding="utf-8") + "\n# external\n", encoding="utf-8")
    editor._external_change(str(path))
    assert editor.form_is_stale
    assert not editor.save()
    assert "# external" in path.read_text(encoding="utf-8")


def test_editor_does_not_treat_its_own_atomic_save_as_external(qtbot, tmp_path: Path) -> None:
    path = tmp_path / "case.yaml"
    shutil.copy2(SOURCE_CASE, path)
    editor = CaseEditor()
    qtbot.addWidget(editor)
    states: list[str] = []
    editor.document_state_changed.connect(states.append)
    editor.load_path(path)
    editor.name_edit.setText("saved_by_workbench")
    editor.name_edit.editingFinished.emit()
    assert editor.save()
    qtbot.wait(100)
    assert states[-1] == "clean"
    assert "external_conflict" not in states[-2:]


def test_environment_doctor_button_starts_one_headless_task(qtbot, monkeypatch) -> None:
    create_application([])
    window = MainWindow(PROJECT_ROOT, Path("C:/missing-solver-prefix"))
    qtbot.addWidget(window)
    calls = []
    monkeypatch.setattr(window.home._doctor, "start", lambda *args, **kwargs: calls.append((args, kwargs)))
    window.home.refresh()
    assert len(calls) == 1


def test_import_is_unsaved_and_save_as_never_modifies_source(qtbot, tmp_path: Path) -> None:
    source = tmp_path / "imported.yaml"
    target = tmp_path / "copied.yaml"
    shutil.copy2(SOURCE_CASE, source)
    original = source.read_bytes()
    editor = CaseEditor()
    qtbot.addWidget(editor)

    editor.import_path(source)
    assert editor.document is not None and editor.document.source_path is None
    editor.name_edit.setText("copied_case")
    editor.name_edit.editingFinished.emit()
    assert editor.save_as(target)
    assert source.read_bytes() == original
    assert "name: copied_case" in target.read_text(encoding="utf-8")


def test_rejected_form_patch_is_transactional(qtbot) -> None:
    editor = CaseEditor()
    qtbot.addWidget(editor)
    editor.load_path(SOURCE_CASE)
    assert editor.document is not None
    before = editor.document.to_text()

    editor.name_edit.setText("")
    editor.name_edit.editingFinished.emit()
    assert editor.document.to_text() == before
    assert editor.error_list.count() == 1


def test_layered_preflight_and_mapping_evidence_is_navigable(qtbot) -> None:
    editor = CaseEditor()
    qtbot.addWidget(editor)
    editor.load_path(SOURCE_CASE)
    editor.show_validation_receipt(
        {
            "preflight_stage_results": [
                {
                    "stage": "mapping",
                    "status": "failed",
                    "errors": ["minerals.0.name: kinetic record missing"],
                    "warnings": [],
                }
            ],
            "kinetic_mapping_summary": [
                {
                    "mineral_name": "Calcite",
                    "kinetic_model": "kinec",
                    "parameter_record": None,
                    "surface_area_present": True,
                    "mapped": False,
                    "reason": "Calcite mapping failed",
                }
            ],
            "errors": ["minerals.0.name: kinetic record missing"],
        }
    )
    assert "mapping: failed" in editor.validation_evidence.toPlainText()
    assert "mapping Calcite: blocked" in editor.validation_evidence.toPlainText()
    assert editor.error_list.count() == 2


def test_case_sections_and_schema_template_expose_values_without_inventing_them(qtbot, tmp_path: Path) -> None:
    editor = CaseEditor()
    qtbot.addWidget(editor)
    editor.load_path(SOURCE_CASE)
    editor.sections.setCurrentIndex(editor.sections.findData("Solver"))
    assert '"solver"' in editor.resolved.toPlainText()

    (tmp_path / "cases").mkdir()
    shutil.copy2(PROJECT_ROOT / "cases" / "schema_template.yaml", tmp_path / "cases" / "schema_template.yaml")
    window = MainWindow(tmp_path, Path("C:/missing-solver-prefix"))
    qtbot.addWidget(window)
    window.cases.new_from_template()
    assert window.cases.editor.document is not None
    assert window.cases.editor.document.source_path is None
    assert window.cases.editor.form_is_stale
    assert "advanced values or yaml" in window.cases.editor.error_list.item(0).text().casefold()
    assert "need input" in window.cases.editor.section_completeness.text()


def test_queue_rejects_unvalidated_snapshots_and_accepts_ready_records(qtbot, tmp_path: Path) -> None:
    create_application([])
    window = MainWindow(tmp_path, Path("C:/missing-solver-prefix"))
    qtbot.addWidget(window)
    snapshot = tmp_path / "run_case.yaml"
    snapshot.write_text("case: {}\n", encoding="utf-8")
    (tmp_path / "run_record.json").write_text("{}", encoding="utf-8")

    with pytest.raises(RuntimeError):
        window.queue.add_snapshot(snapshot)
    with pytest.raises(ValueError):
        window.queue.add_prepared_run({"state": "blocked_preflight"})
    window.queue.finish_queue("completed")
    assert not window.queue.start_button.isEnabled()
    run_id = window.queue.add_prepared_run(
        {
            "state": "ready",
            "run_id": "ready-run",
            "snapshot_path": str(snapshot),
            "validation_receipt_path": str(tmp_path / "validation_receipt.json"),
        }
    )
    assert run_id == "ready-run"
    assert window.queue.table.item(0, 2).text() == "Queued"
    assert window.queue.table.item(0, 2).data(Qt.ItemDataRole.UserRole) == "queued"
    assert window.queue.start_button.isEnabled()


def test_queue_pause_and_cancel_after_current_are_explicit_requests(qtbot, tmp_path: Path) -> None:
    create_application([])
    window = MainWindow(tmp_path, Path("C:/missing-solver-prefix"))
    qtbot.addWidget(window)
    pause = QSignalSpy(window.queue.pause_after_current_requested)
    cancel = QSignalSpy(window.queue.cancel_after_current_requested)
    window.queue.begin_execution()

    window.queue.pause_button.click()
    assert pause.count() == 1
    assert "active solver call continues" not in window.queue.status.text()
    assert not window.queue.pause_button.isEnabled()
    window.queue.cancel_after_button.click()
    assert cancel.count() == 1
    assert "active solver is not paused" in window.queue.status.text()


def test_main_window_wires_authorised_run_to_exact_solver_qprocess(qtbot, monkeypatch, tmp_path: Path) -> None:
    create_application([])
    window = MainWindow(PROJECT_ROOT, Path("C:/solver-prefix"))
    qtbot.addWidget(window)
    assert isinstance(window.queue_controller, ProcessController)
    launched = []
    monkeypatch.setattr(
        window.queue_controller,
        "launch_solver",
        lambda **kwargs: launched.append(kwargs),
    )
    record_path = tmp_path / "run_record.json"
    window._active_run_record_path = record_path
    window._task_succeeded(
        "authorise_run",
        {
            "state": "starting",
            "snapshot_path": str(tmp_path / "run_case.yaml"),
            "run_id": "run-1",
            "case_id": "case-1",
        },
    )
    assert launched[0]["solver_prefix"] == Path("C:/solver-prefix").resolve()
    assert launched[0]["case_path"] == str(tmp_path / "run_case.yaml")


def test_queue_reordering_changes_persisted_plan_order(qtbot, tmp_path: Path) -> None:
    create_application([])
    window = MainWindow(tmp_path, Path("C:/missing-solver-prefix"))
    qtbot.addWidget(window)
    for index in (1, 2):
        run_dir = tmp_path / f"run-{index}"
        run_dir.mkdir()
        snapshot = run_dir / "run_case.yaml"
        snapshot.write_text("case: {}\n", encoding="utf-8")
        record_path = run_dir / "run_record.json"
        record_path.write_text("{}", encoding="utf-8")
        window.queue.add_prepared_run(
            {
                "state": "ready",
                "run_id": f"run-{index}",
                "snapshot_path": str(snapshot),
                "validation_receipt_path": str(run_dir / "receipt.json"),
            }
        )
    window.queue.table.selectRow(1)
    window.queue.move_up_button.click()
    assert window.queue.table.item(0, 3).text() == "run-2"
    assert window.queue.run_record_paths[0].endswith("run-2\\run_record.json")


def test_status_and_plot_equivalent_are_not_colour_only(qtbot) -> None:
    create_application([])
    window = MainWindow(PROJECT_ROOT, Path("C:/missing-solver-prefix"))
    qtbot.addWidget(window)
    assert window.home.status.text() == "Not checked"
    assert window.home.status.pixmap() is not None
    assert window.home.status.accessibleDescription().startswith("Status:")
    headers = [
        window.explore.table.horizontalHeaderItem(column).text()
        for column in range(window.explore.table.columnCount())
    ]
    assert headers == ["time_s", "value"]


def test_explore_reads_saved_provenance_numerical_and_exact_plot_data(qtbot, tmp_path: Path) -> None:
    result = tmp_path / "run-1" / "results"
    result.mkdir(parents=True)
    (tmp_path / "run-1" / "run_record.json").write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "state": "completed",
                "scientific_fingerprint": "0" * 64,
                "snapshot_sha256": "1" * 64,
                "source_case": {"sha256": "2" * 64},
                "output_completeness": {"status": "complete"},
            }
        ),
        encoding="utf-8",
    )
    (result / "manifest.json").write_text(
        json.dumps(
            {
                "output_schema_version": "objective1_audit_v4",
                "run_identity": {"simulation_completed": True},
                "time_semantics": {"duration_s": 86_400.0},
                "traceability": {"database_path": "db.dat", "database_sha256": "3" * 64},
                "solver_configuration": {"workflow": {"mode": "fixed"}},
                "output_files": [
                    "manifest.json",
                    "diagnostics.json",
                    "timeseries.csv",
                    "solver_history.csv",
                    "carbon_inventory.csv",
                ],
            }
        ),
        encoding="utf-8",
    )
    (result / "diagnostics.json").write_text(
        json.dumps(
            {
                "output_schema_version": "objective1_audit_v4",
                "simulation_completed": True,
                "output_completeness": {"status": "complete"},
                "final_time_reached_s": 86_400.0,
                "number_of_accepted_steps": 1,
                "number_of_rejected_steps": 0,
            }
        ),
        encoding="utf-8",
    )
    (result / "timeseries.csv").write_text("time_s,pH\n0,7.0\n86400,6.5\n", encoding="utf-8")
    (result / "solver_history.csv").write_text(
        "attempt_index,time_end_s,accepted\n1,86400,True\n", encoding="utf-8"
    )
    (result / "carbon_inventory.csv").write_text(
        "time_s,total_carbon_mol\n"
        + "\n".join(f"{index},{index / 10}" for index in range(1_001))
        + "\n",
        encoding="utf-8",
    )
    create_application([])
    window = MainWindow(tmp_path, Path("C:/missing-solver-prefix"))
    qtbot.addWidget(window)
    window.explore.load_package(result)
    assert window.explore.overview.rowCount() >= 10
    assert window.explore.artifacts.rowCount() == 5
    assert window.explore.numerical.rowCount() == 1
    assert window.explore.saved_table_select.count() == 3
    window.explore.saved_table_select.setCurrentText("carbon_inventory.csv")
    assert window.explore.saved_table.rowCount() == 1_000
    assert "first 1,000" in window.explore.saved_table_notice.text()
    overview = {
        window.explore.overview.item(row, 0).text(): window.explore.overview.item(row, 1).text()
        for row in range(window.explore.overview.rowCount())
    }
    assert overview["Available saved audit tables"] == "carbon_inventory.csv"
    assert window.explore.table.rowCount() == 2
    window.explore.time_unit.setCurrentIndex(window.explore.time_unit.findData("days"))
    assert window.explore.table.horizontalHeaderItem(0).text() == "time_days"
    assert window.explore.table.item(1, 0).text() == "1.0"
    assert window.explore.plot.getAxis("bottom").labelText == "Time (days)"
    assert window.explore.plot.backgroundBrush().color().name() == "#ffffff"


def test_runs_filters_compare_preview_and_study_spec_editor(qtbot, tmp_path: Path) -> None:
    first = _result_package(tmp_path, "run-a", "0" * 64, model="kinec")
    second = _result_package(tmp_path, "run-b", "1" * 64, model="kinec")
    rebuild_index(tmp_path / ".workbench" / "run_index.sqlite", tmp_path / "runs")
    create_application([])
    window = MainWindow(tmp_path, Path("C:/missing-solver-prefix"))
    qtbot.addWidget(window)

    window.runs.status_filter.setText("completed")
    window.runs.model_filter.setText("kinec")
    assert window.runs.table.rowCount() == 2
    window.runs.model_filter.setText("palandri_kharaka")
    assert window.runs.table.rowCount() == 0

    window.compare.add_package(first)
    window.compare.add_package(second)
    window.compare.quantity.setCurrentText("pH")
    window.compare.check_requested.disconnect(window._compare_check)
    comparison_check = QSignalSpy(window.compare.check_requested)
    window.compare.check_compatibility()
    assert comparison_check.count() == 1
    window.compare.apply_compatibility(
        compatibility_gate([ResultPackage(first), ResultPackage(second)], "pH")
    )
    assert window.compare.save_button.isEnabled()
    saved_spec = tmp_path / "comparison_spec.json"
    saved_spec.write_text(json.dumps({"selected_quantities": ["pH"]}), encoding="utf-8")
    saved_data = tmp_path / "comparison.csv"
    saved_data.write_text(
        f"run_path,time_s,pH\n{first},0,7\n{first},10,6\n{second},0,7\n{second},10,6\n",
        encoding="utf-8",
    )
    assert window.compare.set_saved_artifacts(saved_spec, saved_data)
    assert window.compare.run_paths.count() == 2
    assert window.compare.quantity.currentData() == "pH"
    assert "loaded saved comparison" in window.compare.status.text().casefold()
    assert window.compare.report_button.isEnabled()
    assert window.compare.data.rowCount() == 4
    assert len(window.compare.plot.listDataItems()) == 2
    assert len({item.opts["symbol"] for item in window.compare.plot.listDataItems()}) == 2
    assert window.compare.overlay_stack.currentWidget() is window.compare.plot

    study_path = tmp_path / "study_spec.yaml"
    study_path.write_text(
        json.dumps(
            {
                "study_schema_version": "1.0",
                "study_id": "study-1",
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
    window.studies.load_study_spec(study_path)
    assert window.studies.baseline.text() == "baseline.yaml"
    assert "specification valid" in window.studies.status.text().casefold()
    original = study_path.read_text(encoding="utf-8")
    window.studies.spec_editor.setPlainText(original + "\n# local edit\n")
    study_path.write_text(original + "\n# external edit\n", encoding="utf-8")
    window.studies._save_study_spec()
    assert "not saved" in window.studies.status.text().casefold()
    assert "# external edit" in study_path.read_text(encoding="utf-8")


def test_qt_is_confined_to_presentation_package() -> None:
    for root_name in ("batch_runner", "workbench_core"):
        for path in (PROJECT_ROOT / root_name).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "from PySide6" not in text and "import PySide6" not in text, path


def _result_package(root: Path, run_id: str, fingerprint: str, *, model: str) -> Path:
    result = root / "runs" / run_id / "results"
    result.mkdir(parents=True)
    (result.parent / "run_record.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "case_id": "case",
                "state": "completed",
                "scientific_fingerprint": fingerprint,
                "kinetic_model": model,
                "workflow_mode": "fixed",
                "output_completeness": {"status": "complete"},
            }
        ),
        encoding="utf-8",
    )
    (result / "manifest.json").write_text(
        json.dumps(
            {
                "output_schema_version": "objective1_audit_v4",
                "run_identity": {"simulation_completed": True, "run_id": run_id},
                "time_semantics": {"duration_s": 10.0},
                "output_files": ["manifest.json", "diagnostics.json", "timeseries.csv"],
            }
        ),
        encoding="utf-8",
    )
    (result / "diagnostics.json").write_text(
        json.dumps(
            {
                "output_schema_version": "objective1_audit_v4",
                "simulation_completed": True,
                "output_completeness": {"status": "complete"},
            }
        ),
        encoding="utf-8",
    )
    (result / "timeseries.csv").write_text("time_s,pH\n0,7\n10,6\n", encoding="utf-8")
    return result

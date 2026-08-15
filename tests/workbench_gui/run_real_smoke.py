"""Manual offscreen smoke of the wired MainWindow -> QProcess -> runner path."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PySide6.QtCore import QTimer

from workbench.app import create_application
from workbench.main_window import MainWindow
from workbench_core.operations import prepare_run
from workbench_core.run_records import load_run_record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case", type=Path)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--solver-prefix", type=Path, required=True)
    parser.add_argument("--timeout-ms", type=int, default=240_000)
    args = parser.parse_args()
    root = args.project_root.resolve()
    record = prepare_run(args.case, root, args.solver_prefix)
    if record.state.value != "ready":
        print(record.model_dump_json(indent=2))
        return 1
    record_path = Path(record.snapshot_path).parent / "run_record.json"
    app = create_application([])
    window = MainWindow(root, args.solver_prefix)
    window.queue.add_prepared_run(record.model_dump(mode="json"))
    outcome = {"timed_out": True}

    def poll() -> None:
        current = load_run_record(record_path)
        if current.finished_at_utc is not None:
            outcome.update(
                timed_out=False,
                run_id=current.run_id,
                state=current.state.value,
                output_completeness=current.output_completeness.status,
                event_path=str(record_path.parent / "events.jsonl"),
            )
            app.quit()
        else:
            QTimer.singleShot(250, poll)

    QTimer.singleShot(0, window.queue.start_button.click)
    QTimer.singleShot(250, poll)
    QTimer.singleShot(args.timeout_ms, app.quit)
    app.exec()
    print(json.dumps(outcome, indent=2))
    return 0 if outcome.get("state") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

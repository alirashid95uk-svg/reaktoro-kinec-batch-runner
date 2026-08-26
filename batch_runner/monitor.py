"""Present existing runtime telemetry to humans without affecting chemistry.

The CLI feeds this monitor lifecycle events, solver-progress records, and
accepted observation rows already produced by simulation orchestration.  It
renders an in-place dashboard when the terminal supports it, falls back to
plain lines otherwise, and writes a concise UTC ``simulation.log``.  It never
queries Reaktoro, creates solver targets, or changes acceptance/timestep policy.
"""

from __future__ import annotations

import os
import sys
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any, Callable, TextIO

from batch_runner.config import ResolvedCase


class SimulationMonitor:
    """Render observational progress, accepted results, and lifecycle events.

    Display output and human logging are separate from the JSONL protocol.  ETA
    is a recent throughput estimate derived from accepted simulated time; it is
    reset after rejection and is not a solver forecast or scientific metric.
    Display/log I/O failures disable that presentation path rather than
    terminate simulation.
    """

    def __init__(
        self,
        case: ResolvedCase,
        *,
        display_enabled: bool,
        stream: TextIO = sys.stdout,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.case = case
        self.settings = case.config.outputs.monitor
        self.display_enabled = display_enabled
        self.stream = stream
        self.clock = clock
        self.is_tty = _supports_in_place_updates(stream)
        self.log_path = case.output_dir / "simulation.log"
        self._pending_log: list[str] = []
        self._log_active = False
        self._log_error: str | None = None
        self._dashboard_lines = 0
        self._started_at = clock()
        self._last_render_at: float | None = None
        self._accepted_time_s = 0.0
        self._attempted_dt_s: float | None = None
        self._accepted_attempts = 0
        self._rejected_attempts = 0
        self._latest_success: bool | None = None
        self._solver_iterations: int | None = None
        self._stage = "configuration_validation"
        self._retry_count = 0
        self._eta_samples: deque[tuple[float, float]] = deque(maxlen=8)
        self._eta_s: float | None = None
        self._latest_row: dict[str, Any] | None = None
        self._recent_warnings: deque[str] = deque(maxlen=3)
        self._mapping_count = 0

    @property
    def progress_percent(self) -> float:
        """Return accepted simulated time as a duration percentage, capped at 100."""
        if self.case.duration_s <= 0.0:
            return 100.0 if self._latest_row is not None else 0.0
        return min(100.0, 100.0 * self._accepted_time_s / self.case.duration_s)

    @property
    def eta_s(self) -> float | None:
        """Return the current observational wall-time ETA in seconds, if estimable."""
        return self._eta_s

    @property
    def log_error(self) -> str | None:
        """Return the most recent ``simulation.log`` I/O error, if any."""
        return self._log_error

    def start(self, *, python_version: str, reaktoro_version: str) -> None:
        """Record run identity/environment and render the initial dashboard.

        Logging is buffered until :meth:`activate_log` because the fresh output
        directory may not exist during early validation stages.
        """
        config = self.case.config
        database = self.case.database_path or config.database.name
        if self.display_enabled:
            self._display("Reaktoro Batch Runner | RUNNING\n")
            self._display(f"Case: {config.case.name}\n")
            self._display(
                f"Database: {Path(str(database)).name} | "
                f"Conditions: {config.physical.temperature_c:g} deg C, "
                f"{config.physical.pressure_bar:g} bar | "
                f"Duration: {_format_time(self.case.duration_s)}\n"
            )
        self._event("INFO", f"Simulation started: {config.case.name}")
        self._event("INFO", f"Python {python_version} | Reaktoro {reaktoro_version}")
        self._event("INFO", f"Database configured: {Path(str(database)).name}")
        self._event("INFO", "Configuration validated")
        self._render(force=True)

    def activate_log(self) -> None:
        """Create ``simulation.log`` and flush buffered lifecycle messages.

        Directory or file errors are retained in :attr:`log_error` and surfaced
        as warnings; they do not propagate into the scientific execution path.
        """
        if self._log_active or self._log_error is not None:
            return
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self.log_path.write_text("\n".join(self._pending_log) + "\n", encoding="utf-8")
            self._pending_log.clear()
            self._log_active = True
        except OSError as error:
            self._log_error = str(error)
            message = f"simulation.log unavailable: {error}"
            self._recent_warnings.append(message)
            if self.display_enabled:
                self._clear_dashboard()
                self._display(f"WARNING {message}\n")

    def handle_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """Consume one existing lifecycle event for human presentation.

        Recognized events update display/log state only.  The payload is read,
        never modified, and unknown event types are ignored.
        """
        if event_type == "stage_started":
            self._stage = str(payload["stage"])
            if self._stage == "solver_execution":
                self._event(
                    "INFO",
                    f"Solver started; requested duration {_format_time(self.case.duration_s)}",
                )
            elif self._stage == "output_writing":
                self._event("INFO", "Writing output package")
            else:
                self._event("INFO", f"Starting {_stage_label(self._stage)}")
            self._render(force=True)
        elif event_type == "stage_completed":
            stage = str(payload["stage"])
            if stage == "database_loading":
                database = self.case.database_path or self.case.config.database.name
                self._event("INFO", f"Database loaded: {Path(str(database)).name}")
            elif stage == "mapping":
                self._event("INFO", f"Kinetic mapping validated ({self._mapping_count} entries)")
        elif event_type == "mapping_result":
            self._mapping_count = len(payload.get("mapping", []))
        elif event_type == "checkpoint_written":
            self._event(
                "INFO",
                f"Checkpoint {payload['checkpoint_index']} written at "
                f"{_format_time(float(payload['time_s']))}",
            )
        elif event_type == "validation_issue":
            self._event(
                "ERROR",
                f"{_stage_label(str(payload.get('stage', 'validation')))} failed: "
                f"{payload.get('error_message', 'unknown error')}",
            )
        elif event_type == "output_written":
            completeness = payload["output_completeness"]["status"]
            category = "INFO" if completeness == "complete" else "ERROR"
            self._event(
                category,
                f"Output package {completeness}: {payload['output_dir']}",
            )

    def handle_progress(self, payload: dict[str, Any]) -> None:
        """Consume one solver-attempt progress snapshot.

        Accepted time drives progress and ETA.  Rejections clear ETA samples and
        are presented as restored retries or terminal failures according to the
        supplied fields; no retry or acceptance decision is made here.
        """
        now = self.clock()
        accepted_time_s = float(payload["accepted_time_s"])
        advanced = accepted_time_s > self._accepted_time_s
        self._accepted_time_s = accepted_time_s
        self._attempted_dt_s = float(payload["current_dt_s"])
        self._accepted_attempts = int(payload["accepted_attempts"])
        self._rejected_attempts = int(payload["rejected_attempts"])
        solver_succeeded = payload.get("solver_succeeded")
        self._latest_success = solver_succeeded if isinstance(solver_succeeded, bool) else None
        self._solver_iterations = payload.get("solver_iterations")
        self._stage = str(payload["stage"])

        if not payload["latest_accepted"]:
            self._eta_samples.clear()
            self._eta_s = None
            reason = payload.get("latest_reason") or None

            if self._latest_success is True:
                message = (
                    f"Step not committed at {_format_time(accepted_time_s)}; state restored"
                )
                if reason:
                    message += f": {reason}"
                self._event("WARNING", message)
                self._render(force=True, now=now)
                return

            next_dt_s = payload.get("next_dt_s")
            message = (
                f"Reaktoro solve failed at {_format_time(accepted_time_s)}, "
                f"attempted dt {_format_time(self._attempted_dt_s)}"
            )
            if reason:
                message += f": {reason}"

            if next_dt_s is not None:
                self._retry_count += 1
                self._event("WARNING", message)
                self._event(
                    "INFO",
                    f"State restored; retrying from {_format_time(accepted_time_s)} "
                    f"with dt {_format_time(float(next_dt_s))}",
                )
            else:
                self._event(
                    "ERROR",
                    f"Reaktoro solve failed during {_stage_label(self._stage)} at "
                    f"{_format_time(accepted_time_s)}"
                    + (
                        f", attempted dt {_format_time(self._attempted_dt_s)}"
                        if self._attempted_dt_s > 0.0
                        else ""
                    )
                    + (f": {reason}" if reason else ""),
                )
            self._render(force=True, now=now)
            return

        if self._retry_count:
            self._event(
                "INFO",
                f"Solver recovered at attempted dt {_format_time(self._attempted_dt_s)} "
                f"after {self._retry_count} retr{'y' if self._retry_count == 1 else 'ies'}",
            )
            self._retry_count = 0
            self._eta_samples.clear()

        if advanced:
            self._eta_samples.append((now - self._started_at, accepted_time_s))
            self._update_eta()
        self._render(now=now)

    def handle_accepted_row(self, row: dict[str, Any]) -> None:
        """Present configured values at already-scheduled accepted result times.

        Monitor result times are resolved upstream as a subset of the scientific
        output schedule, so this method cannot add solver targets or observations.
        """
        self._latest_row = row
        time_s = float(row["time_s"])
        if time_s in self.case.monitor_result_times_s:
            values = self._result_values(row)
            suffix = f" | {values}" if values else ""
            self._event(
                "RESULT",
                f"Accepted scientific result at {_format_time(time_s)}{suffix}",
            )
            self._render(force=True)

    def finish(self, result: Any, output_dir: Path) -> None:
        """Write the terminal/log conclusion from diagnostics and completeness.

        A run is presented as complete only when simulation and output package
        both completed.  Failure details are read from diagnostics; the result
        and package are not mutated.
        """
        self.activate_log()
        diagnostics = result.diagnostics
        simulation_completed = bool(diagnostics["simulation_completed"])
        completeness = diagnostics["output_completeness"]["status"]
        completed = simulation_completed and completeness == "complete"
        self._clear_dashboard()

        if completed:
            self._event("RESULT", "Simulation completed")
            self._event(
                "INFO",
                f"Final simulated time: {_format_time(float(diagnostics['final_time_reached_s']))}",
            )
            self._event("INFO", f"Accepted attempts: {diagnostics['number_of_accepted_steps']:,}")
            self._event(
                "INFO",
                f"Failed/retried attempts: {diagnostics['number_of_rejected_steps']:,}",
            )
        elif diagnostics.get("cancellation_requested"):
            self._event(
                "WARNING",
                f"CANCELLED at {_format_time(float(diagnostics['final_time_reached_s']))}",
            )
        else:
            recent_warnings = list(self._recent_warnings)
            output_failure = diagnostics.get("output_failure") or {}
            failed_stage = output_failure.get("failed_stage") or diagnostics.get("failed_stage") or "unknown"
            error_message = output_failure.get("error_message") or diagnostics.get("error_message") or "unknown error"
            self._event(
                "ERROR",
                f"FAILED | stage={failed_stage} | last accepted="
                f"{_format_time(float(diagnostics['final_time_reached_s']))} | {error_message}",
            )
            for warning in recent_warnings:
                self._event("WARNING", f"Recent: {warning}")
            for warning in diagnostics.get("warnings", [])[-3:]:
                if warning not in self._recent_warnings:
                    self._event("WARNING", str(warning))

        self._event("INFO", f"Output package: {completeness} | {output_dir}")
        if not completed:
            self._event("INFO", f"Technical details: {output_dir / 'diagnostics.json'}")

    def _update_eta(self) -> None:
        self._eta_s = None
        if len(self._eta_samples) < 3:
            return
        first_wall, first_time = self._eta_samples[0]
        last_wall, last_time = self._eta_samples[-1]
        if last_wall <= first_wall or last_time <= first_time:
            return
        simulated_per_wall_second = (last_time - first_time) / (last_wall - first_wall)
        remaining = max(0.0, self.case.duration_s - self._accepted_time_s)
        self._eta_s = remaining / simulated_per_wall_second

    def _render(self, *, force: bool = False, now: float | None = None) -> None:
        if not self.display_enabled:
            return
        now = self.clock() if now is None else now
        if not force and self._last_render_at is not None:
            if now - self._last_render_at < self.settings.refresh_interval_s:
                return
        self._last_render_at = now
        elapsed = now - self._started_at
        percent = self.progress_percent
        filled = round(percent / 5.0)
        eta = _format_clock(self._eta_s) if self._eta_s is not None else "estimating..."
        result_text = self._result_values(self._latest_row) if self._latest_row else "waiting for accepted output"
        status = (
            "SUCCESS"
            if self._latest_success is True
            else "FAILED"
            if self._latest_success is False
            else "waiting"
        )
        iterations = f", {self._solver_iterations} iterations" if self._solver_iterations is not None else ""
        lines = [
            f"Progress [{'#' * filled}{'-' * (20 - filled)}] "
            f"{_format_time(self._accepted_time_s)} / {_format_time(self.case.duration_s)} "
            f"({percent:6.2f}%) | wall {_format_clock(elapsed)} | ETA {eta}",
            f"Stage {_stage_label(self._stage)} | attempted dt {_format_time(self._attempted_dt_s)} | "
            f"accepted {self._accepted_attempts:,} | failed/retried {self._rejected_attempts:,} | "
            f"last {status}{iterations}",
            f"Accepted results @ {_format_time(float(self._latest_row['time_s'])) if self._latest_row else '--'} | "
            f"{result_text}",
        ]
        if self.is_tty:
            if self._dashboard_lines:
                self._display(f"\x1b[{self._dashboard_lines}A\x1b[J")
            self._display("\n".join(lines) + "\n")
            self._dashboard_lines = len(lines)
        else:
            self._display(" | ".join(lines) + "\n")

    def _result_values(self, row: dict[str, Any] | None) -> str:
        if row is None:
            return ""
        values = [f"{name}={_number(row[name])}" for name in self.settings.scalars]
        values.extend(
            f"{name}={_number(row[f'species_molality_mol_kgw::{name}'])} mol/kgw"
            for name in self.settings.species
        )
        values.extend(
            f"{name}={_number(row[f'mineral_amount_mol::{name}'])} mol "
            f"(delta {_number(row[f'mineral_delta_mol::{name}'])} mol)"
            for name in self.settings.minerals
        )
        return "; ".join(values)

    def _event(self, category: str, message: str) -> None:
        if category in {"WARNING", "ERROR"}:
            self._recent_warnings.append(message)
        self._log(category, message)
        if self.display_enabled:
            self._clear_dashboard()
            self._display(f"{category:<7} {message}\n")

    def _log(self, category: str, message: str) -> None:
        line = f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%SZ} [{category}] {message}"
        if not self._log_active:
            self._pending_log.append(line)
            return
        try:
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
        except OSError as error:
            self._log_error = str(error)
            self._log_active = False

    def _clear_dashboard(self) -> None:
        if self.display_enabled and self.is_tty and self._dashboard_lines:
            self._display(f"\x1b[{self._dashboard_lines}A\x1b[J")
        self._dashboard_lines = 0

    def _display(self, text: str) -> None:
        try:
            self.stream.write(text)
            self.stream.flush()
        except OSError:
            self.display_enabled = False


def _stage_label(stage: str) -> str:
    return stage.replace("_", " ")


def _supports_in_place_updates(stream: TextIO) -> bool:
    if not bool(getattr(stream, "isatty", lambda: False)()):
        return False
    if os.name != "nt" or stream is not sys.stdout:
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        return bool(
            kernel32.GetConsoleMode(handle, ctypes.byref(mode))
            and kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        )
    except (AttributeError, OSError):
        return False


def _format_time(seconds: float | None) -> str:
    if seconds is None:
        return "--"
    seconds = float(seconds)
    if seconds >= 86400.0:
        return f"{seconds / 86400.0:.4g} d"
    if seconds >= 3600.0:
        return f"{seconds / 3600.0:.4g} h"
    if seconds >= 60.0:
        return f"{seconds / 60.0:.4g} min"
    return f"{seconds:.4g} s"


def _format_clock(seconds: float | None) -> str:
    if seconds is None:
        return "--:--:--"
    total = max(0, round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _number(value: Any) -> str:
    return f"{float(value):.6g}"

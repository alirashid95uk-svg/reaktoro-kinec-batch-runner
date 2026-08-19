"""Human presentation adapter for accepted-state numerical-integrity diagnostics."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from batch_runner.monitor import SimulationMonitor, _format_time, _number


class IntegritySimulationMonitor(SimulationMonitor):
    """Extend the human monitor without changing solver or chemistry behaviour."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._numerical_integrity: dict[str, Any] | None = None
        self._integrity_warning_reported = False

    def handle_numerical_integrity(self, snapshot: dict[str, Any]) -> None:
        """Store diagnostics computed from an accepted state for presentation only."""
        self._numerical_integrity = snapshot
        if snapshot.get("status") == "unavailable" and not self._integrity_warning_reported:
            self._integrity_warning_reported = True
            self._event(
                "WARNING",
                "Numerical-integrity diagnostics unavailable: "
                f"{snapshot.get('reason', 'unknown error')}",
            )

    def handle_accepted_row(self, row: dict[str, Any]) -> None:
        time_s = float(row["time_s"])
        if time_s in self.case.monitor_result_times_s:
            integrity = self._integrity_log_values(time_s)
            if integrity:
                self._log(
                    "RESULT",
                    f"Numerical integrity at {_format_time(time_s)} | {integrity}",
                )
        super().handle_accepted_row(row)

    def finish(self, result: Any, output_dir: Path) -> None:
        """Finish the base monitor, then report final accepted-state integrity."""
        super().finish(result, output_dir)
        snapshot = self._numerical_integrity
        if not snapshot:
            return

        time_s = snapshot.get("time_s")
        if time_s is None:
            return
        final_values = self._integrity_log_values(float(time_s))
        if not final_values:
            return

        category = "WARNING" if snapshot.get("status") == "unavailable" else "RESULT"
        self._event(
            category,
            "Final accepted-state numerical integrity at "
            f"{_format_time(float(time_s))} | {final_values}",
        )

    def _display(self, text: str) -> None:
        """Insert compact accepted-state integrity metrics into progress rendering."""
        if "Progress [" in text and " | wall " in text:
            compact = self._progress_integrity_values()
            if compact:
                text = text.replace(
                    " | wall ",
                    f" | {compact} | wall ",
                    1,
                )
        super()._display(text)

    def _render(self, *, force: bool = False, now: float | None = None) -> None:
        previous_render_at = self._last_render_at
        super()._render(force=force, now=now)
        if not self.display_enabled or self._last_render_at == previous_render_at:
            return
        text = self._integrity_display_values()
        if not text:
            return
        time_s = self._numerical_integrity.get("time_s") if self._numerical_integrity else None
        line = (
            f"Numerical integrity @ "
            f"{_format_time(float(time_s)) if time_s is not None else '--'} | {text}\n"
        )
        self._display(line)
        if self.is_tty:
            self._dashboard_lines += 1

    def _progress_integrity_values(self) -> str:
        """Return compact current material/charge checks for the progress line."""
        snapshot = self._numerical_integrity
        if not snapshot:
            return ""
        if snapshot.get("status") == "unavailable":
            return "balance n/a | charge n/a"

        material = snapshot.get("material_balance", {})
        if material.get("status") == "evaluated":
            worst = material.get("worst_component")
            balance = (
                f"balance {_number(material['max_relative_residual'])} rel"
                + (f" ({worst})" if worst else "")
            )
        else:
            balance = "balance n/a"

        charge = snapshot.get("charge", {})
        charge_status = charge.get("status")
        if charge_status == "evaluated":
            charge_text = f"charge {_number(charge['residual_mol'])} mol"
        elif charge_status == "open_boundary":
            charge_text = "charge open boundary"
        else:
            charge_text = "charge n/a"

        return f"{balance} | {charge_text}"

    def _integrity_display_values(self) -> str:
        snapshot = self._numerical_integrity
        if not snapshot:
            return ""
        if snapshot.get("status") == "unavailable":
            return "not evaluated"

        material = snapshot.get("material_balance", {})
        if material.get("status") == "evaluated":
            worst = material.get("worst_component")
            component = (
                f"component max {_number(material['max_relative_residual'])} rel"
                + (f" ({worst})" if worst else "")
            )
            rms = f"RMS {_number(material['rms_relative_residual'])} rel"
            cumulative = (
                "cumulative max "
                f"{_number(material['cumulative_max_relative_residual'])} rel"
            )
        else:
            component = "component balance not evaluated"
            rms = ""
            cumulative = ""

        carbon = snapshot.get("carbon", {})
        carbon_status = carbon.get("status")
        if carbon_status == "evaluated":
            if carbon.get("relative_residual") is not None:
                carbon_text = f"carbon {_number(carbon['relative_residual'])} rel"
            else:
                carbon_text = (
                    f"carbon {_number(carbon['residual_mol'])} mol residual (relative n/a)"
                )
        elif carbon_status == "open_boundary":
            carbon_text = "carbon open boundary"
        elif carbon_status == "not_present":
            carbon_text = "carbon not present"
        else:
            carbon_text = "carbon not evaluated"

        charge = snapshot.get("charge", {})
        charge_status = charge.get("status")
        if charge_status == "evaluated":
            charge_text = f"charge {_number(charge['residual_mol'])} mol"
        elif charge_status == "open_boundary":
            charge_text = "charge open boundary"
        else:
            charge_text = "charge not evaluated"

        return " | ".join(
            value
            for value in (component, rms, cumulative, carbon_text, charge_text)
            if value
        )

    def _integrity_log_values(self, time_s: float) -> str:
        snapshot = self._numerical_integrity
        if not snapshot or float(snapshot.get("time_s", -1.0)) != time_s:
            return ""
        if snapshot.get("status") == "unavailable":
            return f"not evaluated: {snapshot.get('reason', 'unknown error')}"

        material = snapshot.get("material_balance", {})
        values: list[str] = []
        if material.get("status") == "evaluated":
            values.extend(
                [
                    f"component max={_number(material['max_relative_residual'])}",
                    f"worst component={material.get('worst_component') or 'n/a'}",
                    f"RMS={_number(material['rms_relative_residual'])}",
                    "cumulative max="
                    f"{_number(material['cumulative_max_relative_residual'])}",
                ]
            )

        carbon = snapshot.get("carbon", {})
        if carbon.get("status") == "evaluated":
            if carbon.get("relative_residual") is not None:
                values.append(f"carbon residual={_number(carbon['relative_residual'])}")
            else:
                values.append(
                    f"carbon residual={_number(carbon['residual_mol'])} mol (relative n/a)"
                )
        elif carbon.get("status") == "open_boundary":
            values.append("carbon=open boundary / not evaluated")

        charge = snapshot.get("charge", {})
        if charge.get("status") == "evaluated":
            values.append(f"charge residual={_number(charge['residual_mol'])} mol")
        elif charge.get("status") == "open_boundary":
            values.append("charge=open boundary / not evaluated")
        return " | ".join(values)

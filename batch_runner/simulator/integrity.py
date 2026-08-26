"""Observe accepted-state component, carbon, and charge residuals.

The runner may attach :class:`NumericalIntegrityObserver` to accepted-state
callbacks.  It reads Reaktoro component inventories after acceptance and never
participates in timestep or solver decisions.  Components opened by fixed gas
fugacity and charge opened by pE are explicitly excluded from closed-system
residual claims.

These metrics are numerical diagnostics, not proof of chemical calibration,
temporal convergence, transport conservation, or scientific validity.
"""

from __future__ import annotations

from math import sqrt
from typing import Any

from batch_runner.config import ResolvedCase
from batch_runner.simulator.chemistry.conditions import (
    fixed_fugacity_applies,
    redox_applies,
)


class NumericalIntegrityObserver:
    """Track residuals relative to the first accepted state.

    Diagnostic exceptions are contained and permanently mark the observer
    unavailable so optional monitoring cannot terminate or change chemistry.
    Nonzero closed-component references contribute to relative aggregates;
    zero-reference components remain visible only through absolute residuals.
    """

    def __init__(self, case: ResolvedCase) -> None:
        self.case = case
        self._initialized = False
        self._unavailable_reason: str | None = None
        self._element_names: tuple[str, ...] = ()
        self._open_elements: tuple[str, ...] = ()
        self._open_reasons: dict[str, str] = {}
        self._charge_open_reason: str | None = None
        self._reference_components: tuple[float, ...] = ()
        self._reference_charge_mol: float | None = None
        self._cumulative_max_relative_residual = 0.0
        self._latest: dict[str, Any] | None = None

    @property
    def unavailable_reason(self) -> str | None:
        """Return the first contained diagnostic failure, if any."""
        return self._unavailable_reason

    def observe(self, state: Any, *, time_s: float, initialize: bool = False) -> dict[str, Any]:
        """Evaluate one accepted state without raising diagnostic failures.

        Args:
            state: Accepted Reaktoro ``ChemicalState`` to read.
            time_s: Accepted simulation time in seconds.
            initialize: Reset the reference inventory to this state.

        Returns:
            dict[str, Any]: A JSON-serializable snapshot with material, carbon,
                and charge status.  On any diagnostic error, returns
                ``status=unavailable``.
        """
        if self._unavailable_reason is not None:
            self._latest = self._unavailable_snapshot(time_s)
            return self._latest
        try:
            if initialize or not self._initialized:
                return self._initialize(state, time_s)
            return self._evaluate(state, time_s)
        except Exception as error:  # diagnostics must never terminate chemistry
            self._unavailable_reason = f"{type(error).__name__}: {error}"
            self._latest = self._unavailable_snapshot(time_s)
            return self._latest

    def summary(self) -> dict[str, Any]:
        """Return definitions, reference inventories, and latest metrics.

        The summary records open-component reasons and the precise relative
        normalization so a consumer can distinguish evaluated, open-boundary,
        zero-reference, and unavailable quantities.
        """
        status = (
            "unavailable"
            if self._unavailable_reason is not None
            else "evaluated"
            if self._initialized
            else "not_initialized"
        )
        return {
            "status": status,
            "definition": "accepted-state conservative element drift; diagnostic only",
            "relative_normalization": (
                "abs(current-reference)/abs(reference) for nonzero reference inventory; "
                "zero-reference components excluded from relative aggregates and retained "
                "in absolute-residual diagnostics"
            ),
            "element_names": list(self._element_names),
            "open_elements": list(self._open_elements),
            "open_component_reasons": dict(self._open_reasons),
            "charge_open_reason": self._charge_open_reason,
            "reference_component_amounts_mol": (
                dict(zip(self._element_names, self._reference_components[:-1]))
                if self._reference_components
                else {}
            ),
            "reference_charge_mol": self._reference_charge_mol,
            "cumulative_max_relative_residual": (
                self._cumulative_max_relative_residual if self._initialized else None
            ),
            "latest": self._latest,
            "unavailable_reason": self._unavailable_reason,
        }

    def _initialize(self, state: Any, time_s: float) -> dict[str, Any]:
        system = state.system()
        elements = tuple(str(element.symbol()) for element in system.elements())
        components = _component_amounts(state)
        if len(components) != len(elements) + 1:
            raise ValueError(
                "Reaktoro componentAmounts size does not equal elements + charge: "
                f"{len(components)} != {len(elements) + 1}"
            )

        open_reasons: dict[str, str] = {}
        charge_open_reason = None
        constraint_stage = (
            "initial_equilibrium"
            if self.case.config.solver.workflow.mode == "equilibrium_only"
            else "kinetic_steps"
        )
        if fixed_fugacity_applies(self.case, constraint_stage):
            gas_name = self.case.config.co2.gas_species
            gas = system.database().species(gas_name)
            reason = f"fixed fugacity {gas_name}"
            for symbol in gas.elements().symbols():
                symbol = str(symbol)
                if symbol in elements:
                    open_reasons[symbol] = reason
            if float(gas.charge()) != 0.0:
                charge_open_reason = reason

        if redox_applies(self.case, constraint_stage):
            charge_open_reason = "pE constraint"

        self._element_names = elements
        self._open_reasons = open_reasons
        self._open_elements = tuple(symbol for symbol in elements if symbol in open_reasons)
        self._charge_open_reason = charge_open_reason
        self._reference_components = components
        self._reference_charge_mol = float(state.charge())
        self._cumulative_max_relative_residual = 0.0
        self._initialized = True
        self._latest = self._evaluate(state, time_s)
        return self._latest

    def _evaluate(self, state: Any, time_s: float) -> dict[str, Any]:
        components = _component_amounts(state)
        if len(components) != len(self._reference_components):
            raise ValueError("Reaktoro componentAmounts size changed during simulation")

        relative_residuals: list[tuple[str, float, float]] = []
        absolute_residuals: list[tuple[str, float]] = []
        zero_reference_residuals: list[tuple[str, float]] = []
        for index, symbol in enumerate(self._element_names):
            if symbol in self._open_reasons:
                continue
            reference = self._reference_components[index]
            residual_mol = components[index] - reference
            absolute_residuals.append((symbol, residual_mol))
            if reference == 0.0:
                zero_reference_residuals.append((symbol, residual_mol))
                continue
            relative_residuals.append(
                (symbol, residual_mol, abs(residual_mol) / abs(reference))
            )

        max_absolute = max(
            (abs(item[1]) for item in absolute_residuals),
            default=0.0,
        )
        zero_reference_max_absolute = max(
            (abs(item[1]) for item in zero_reference_residuals),
            default=0.0,
        )
        if relative_residuals:
            worst_symbol, worst_residual_mol, max_relative = max(
                relative_residuals, key=lambda item: item[2]
            )
            rms_relative = sqrt(
                sum(item[2] ** 2 for item in relative_residuals)
                / len(relative_residuals)
            )
            self._cumulative_max_relative_residual = max(
                self._cumulative_max_relative_residual, max_relative
            )
            material = {
                "status": "evaluated",
                "max_relative_residual": max_relative,
                "rms_relative_residual": rms_relative,
                "cumulative_max_relative_residual": self._cumulative_max_relative_residual,
                "max_absolute_residual_mol": max_absolute,
                "worst_component": worst_symbol,
                "worst_component_residual_mol": worst_residual_mol,
                "relative_component_count": len(relative_residuals),
                "zero_reference_component_count": len(zero_reference_residuals),
                "zero_reference_max_absolute_residual_mol": zero_reference_max_absolute,
            }
        else:
            material = {
                "status": "not_evaluated",
                "max_relative_residual": None,
                "rms_relative_residual": None,
                "cumulative_max_relative_residual": None,
                "max_absolute_residual_mol": max_absolute,
                "worst_component": None,
                "worst_component_residual_mol": None,
                "relative_component_count": 0,
                "zero_reference_component_count": len(zero_reference_residuals),
                "zero_reference_max_absolute_residual_mol": zero_reference_max_absolute,
            }

        carbon = self._carbon_metrics(components)
        charge = self._charge_metrics(state)
        snapshot = {
            "status": "evaluated",
            "time_s": float(time_s),
            "material_balance": material,
            "carbon": carbon,
            "charge": charge,
            "open_elements": list(self._open_elements),
        }
        self._latest = snapshot
        return snapshot

    def _carbon_metrics(self, components: tuple[float, ...]) -> dict[str, Any]:
        if "C" not in self._element_names:
            return {
                "status": "not_present",
                "total_mol": None,
                "reference_mol": None,
                "residual_mol": None,
                "relative_residual": None,
            }

        index = self._element_names.index("C")
        current = components[index]
        reference = self._reference_components[index]
        if "C" in self._open_reasons:
            return {
                "status": "open_boundary",
                "total_mol": current,
                "reference_mol": reference,
                "residual_mol": None,
                "relative_residual": None,
                "reason": self._open_reasons["C"],
            }

        residual = current - reference
        return {
            "status": "evaluated",
            "total_mol": current,
            "reference_mol": reference,
            "residual_mol": residual,
            "relative_residual": (
                abs(residual) / abs(reference) if reference != 0.0 else None
            ),
        }

    def _charge_metrics(self, state: Any) -> dict[str, Any]:
        current = float(state.charge())
        reference = self._reference_charge_mol
        if self._charge_open_reason is not None:
            return {
                "status": "open_boundary",
                "current_mol": current,
                "reference_mol": reference,
                "residual_mol": None,
                "drift_from_reference_mol": None,
                "relative_residual": None,
                "reason": self._charge_open_reason,
            }

        assert reference is not None
        charge_inventory = 0.0
        species_amounts = state.speciesAmounts()
        for index, species in enumerate(state.system().species()):
            charge_inventory += abs(float(species.charge()) * float(species_amounts[index]))
        return {
            "status": "evaluated",
            "current_mol": current,
            "reference_mol": reference,
            "residual_mol": current,
            "drift_from_reference_mol": current - reference,
            "relative_residual": (
                abs(current) / charge_inventory if charge_inventory != 0.0 else None
            ),
        }

    def _unavailable_snapshot(self, time_s: float) -> dict[str, Any]:
        return {
            "status": "unavailable",
            "time_s": float(time_s),
            "reason": self._unavailable_reason,
            "material_balance": {"status": "unavailable"},
            "carbon": {"status": "unavailable"},
            "charge": {"status": "unavailable"},
            "open_elements": list(self._open_elements),
        }


def _component_amounts(state: Any) -> tuple[float, ...]:
    return tuple(float(value) for value in state.componentAmounts())

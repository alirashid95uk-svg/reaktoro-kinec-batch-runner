"""Decide when configured open-system constraints apply and build them.

Solver orchestration asks this module whether initial equilibrium is required
and requests Reaktoro equilibrium specifications for either the initial or
kinetic stage.  The decisions preserve the configured CO2 workflow and pE
scope; they must not silently carry an initial-only constraint into closed
kinetics.
"""

from typing import Any, Literal

import reaktoro as rkt

from batch_runner.config import ResolvedCase


ConstraintStage = Literal["initial_equilibrium", "kinetic_steps"]


def requires_initial_equilibrium(case: ResolvedCase) -> bool:
    """Return whether the configured workflow requires a time-zero solve."""
    workflow = case.config.solver.workflow.mode
    return (
        workflow == "equilibrium_only"
        or workflow == "fixed_fugacity_initial_equilibrium_then_closed_kinetics"
        or (
            case.config.redox.enabled
            and case.config.redox.apply_during == "initial_equilibrium_only"
        )
    )


def fixed_fugacity_applies(case: ResolvedCase, stage: ConstraintStage) -> bool:
    """Return whether fixed CO2 fugacity is active during *stage*.

    This is a workflow decision only.  It does not create a gas inventory or
    change a finite-amount CO2 case into an open system.
    """
    workflow = case.config.solver.workflow.mode
    if case.config.co2.mode != "fixed_fugacity":
        return False
    if workflow == "equilibrium_only":
        return stage == "initial_equilibrium"
    if workflow == "fixed_fugacity_initial_equilibrium_then_closed_kinetics":
        return stage == "initial_equilibrium"
    if workflow == "fixed_fugacity_during_kinetic_steps":
        return True
    return False


def redox_applies(case: ResolvedCase, stage: ConstraintStage) -> bool:
    """Return whether the configured pE constraint is active during *stage*."""
    if not case.config.redox.enabled:
        return False
    if case.config.redox.apply_during == "initial_equilibrium_only":
        return stage == "initial_equilibrium"
    return stage == "kinetic_steps"


def constraints_apply(case: ResolvedCase, stage: ConstraintStage) -> bool:
    """Return whether either supported equilibrium constraint applies."""
    return fixed_fugacity_applies(case, stage) or redox_applies(case, stage)


def build_conditions(
    case: ResolvedCase,
    system,
    state,
    stage: ConstraintStage,
) -> tuple[Any | None, Any | None]:
    """Build Reaktoro specifications and conditions for one solver stage.

    Temperature and pressure are passed with explicit ``celsius`` and ``bar``
    units.  Fixed fugacity is also expressed in bar; pE is dimensionless.
    Initial component amounts are taken from *state*, so the returned
    conditions are tied to that accepted chemical inventory.

    Returns:
        tuple[Any | None, Any | None]: ``(specs, conditions)`` when a configured
            constraint applies, otherwise ``(None, None)`` so the caller can
            construct an unconstrained solver.

    Raises:
        Exception: Reaktoro cannot represent a configured species or constraint
            in the supplied system.
    """
    if not constraints_apply(case, stage):
        return None, None

    specs = rkt.EquilibriumSpecs.TP(system)
    if fixed_fugacity_applies(case, stage):
        specs.fugacity(case.config.co2.gas_species)
    if redox_applies(case, stage):
        specs.pE()

    conditions = rkt.EquilibriumConditions(specs)
    conditions.temperature(case.config.physical.temperature_c, "celsius")
    conditions.pressure(case.config.physical.pressure_bar, "bar")
    if fixed_fugacity_applies(case, stage):
        conditions.fugacity(
            case.config.co2.gas_species,
            case.config.co2.fugacity_bar,
            "bar",
        )
    if redox_applies(case, stage):
        conditions.pE(case.config.redox.pe)
    conditions.setInitialComponentAmountsFromState(state)
    return specs, conditions

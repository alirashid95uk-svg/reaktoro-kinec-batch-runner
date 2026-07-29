"""Explicit workflow and constraint-staging decisions."""

from typing import Literal

from batch_runner.config import ResolvedCase


ConstraintStage = Literal["initial_equilibrium", "kinetic_steps"]


def requires_initial_equilibrium(case: ResolvedCase) -> bool:
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
    if not case.config.redox.enabled:
        return False
    if case.config.redox.apply_during == "initial_equilibrium_only":
        return stage == "initial_equilibrium"
    return stage == "kinetic_steps"


def constraints_apply(case: ResolvedCase, stage: ConstraintStage) -> bool:
    return fixed_fugacity_applies(case, stage) or redox_applies(case, stage)

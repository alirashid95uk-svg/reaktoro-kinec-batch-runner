"""Direct Reaktoro solver construction and timed calls."""

from time import perf_counter
from typing import Any

import reaktoro as rkt


def equilibrium_solver(system: Any, specs: Any | None) -> Any:
    return rkt.EquilibriumSolver(specs) if specs is not None else rkt.EquilibriumSolver(system)


def kinetics_solver(system: Any, specs: Any | None) -> Any:
    return rkt.KineticsSolver(specs) if specs is not None else rkt.KineticsSolver(system)


def timed_precondition(
    solver: Any,
    state: Any,
    conditions: Any | None,
) -> tuple[Any | None, float, Exception | None]:
    start = perf_counter()
    try:
        result = solver.precondition(state, conditions) if conditions is not None else solver.precondition(state)
        return result, perf_counter() - start, None
    except Exception as error:
        return None, perf_counter() - start, error


def timed_solve(
    solver: Any,
    state: Any,
    *,
    dt_s: float | None = None,
    conditions: Any | None = None,
) -> tuple[Any | None, float, Exception | None]:
    start = perf_counter()
    try:
        if dt_s is None:
            result = solver.solve(state, conditions) if conditions is not None else solver.solve(state)
        else:
            result = solver.solve(state, dt_s, conditions) if conditions is not None else solver.solve(state, dt_s)
        return result, perf_counter() - start, None
    except Exception as error:
        return None, perf_counter() - start, error


def failure_reason(result: Any | None, error: Exception | None, stage: str) -> str | None:
    if error is not None:
        return f"{type(error).__name__} during {stage}: {error}"
    if result is None or not result.succeeded():
        return f"Reaktoro solver failed during {stage}"
    return None

"""Construct Reaktoro solvers and normalize one attempted solve.

Timestep implementations share these wrappers so wall time, thrown exceptions,
and unsuccessful Reaktoro results enter the same solver-history path.  The
module does not decide acceptance, retry size, or rollback.
"""

from time import perf_counter
from typing import Any

import reaktoro as rkt


def equilibrium_solver(system: Any, specs: Any | None) -> Any:
    """Return an equilibrium solver for a system or explicit specifications."""
    return rkt.EquilibriumSolver(specs) if specs is not None else rkt.EquilibriumSolver(system)


def kinetics_solver(system: Any, specs: Any | None) -> Any:
    """Return a kinetics solver for a system or explicit specifications."""
    return rkt.KineticsSolver(specs) if specs is not None else rkt.KineticsSolver(system)


def timed_solve(
    solver: Any,
    state: Any,
    *,
    dt_s: float | None = None,
    conditions: Any | None = None,
) -> tuple[Any | None, float, Exception | None]:
    """Attempt one mutating Reaktoro solve and measure its wall time.

    ``dt_s=None`` selects an equilibrium solve; otherwise *dt_s* is passed to
    the kinetic solver in seconds.  Reaktoro mutates *state* during the call.
    Exceptions are returned, not raised, so the controller can restore its
    pre-attempt snapshot and emit a complete failure record.
    """
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
    """Return a stable failure message for an exception or unsuccessful result."""
    if error is not None:
        return f"{type(error).__name__} during {stage}: {error}"
    if result is None or not result.succeeded():
        return f"Reaktoro solver failed during {stage}"
    return None

"""Construct the common, unit-labelled solver-history record schema.

All timestep controllers emit this base vocabulary before optional controller-
specific fields are added.  Times and timesteps are seconds; wall time is
seconds; a rejected record ends at its restored accepted time.
"""

from typing import Any


def solver_record(
    *,
    step_index: int,
    stage: str,
    time_start_s: float,
    time_end_s: float,
    dt_s: float,
    result: Any,
    wall_time_s: float,
    accepted: bool = True,
    failure_reason: str = "",
    attempt_index: int | None = None,
    next_dt_s: float | None = None,
) -> dict[str, Any]:
    """Return one JSON-serializable solver-attempt record.

    ``result`` may be ``None`` for thrown or unattempted solves.  The function
    reads Reaktoro success and iteration metadata but does not mutate state or
    decide whether the caller should accept the attempt.
    """
    return {
        "step_index": step_index,
        "attempt_index": attempt_index,
        "time_start_s": float(time_start_s),
        "time_end_s": float(time_end_s),
        "dt_s": float(dt_s),
        "stage": stage,
        "accepted": accepted,
        "solver_succeeded": bool(result.succeeded()) if result is not None else False,
        "iterations": int(result.iterations()) if result is not None else None,
        "wall_time_s": float(wall_time_s),
        "failure_reason": failure_reason,
        "next_dt_s": next_dt_s,
    }


def unsolved_record(step_index: int, stage: str, time_s: float) -> dict[str, Any]:
    """Return the zero-duration record used to label an unsolved initial state."""
    return {
        "step_index": step_index,
        "attempt_index": None,
        "time_start_s": float(time_s),
        "time_end_s": float(time_s),
        "dt_s": 0.0,
        "stage": stage,
        "accepted": True,
        "solver_succeeded": None,
        "iterations": None,
        "wall_time_s": 0.0,
        "failure_reason": "",
        "next_dt_s": None,
    }

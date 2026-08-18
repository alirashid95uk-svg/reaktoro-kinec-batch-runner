"""Stable solver-history record construction."""

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

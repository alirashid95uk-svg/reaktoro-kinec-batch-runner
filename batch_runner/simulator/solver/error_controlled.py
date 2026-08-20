"""Richardson error-controlled kinetic timesteps with separate event caps."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from math import isfinite
from typing import Any, Mapping

import reaktoro as rkt

from batch_runner.config import RichardsonErrorControlConfig

from .adaptive import adaptive_target, next_forced_target
from .calls import failure_reason, kinetics_solver, timed_solve
from .records import solver_record
from .runtime import SolverRun


@dataclass(frozen=True)
class ErrorEstimate:
    value: float
    worst_mineral: str
    raw_error_mol: float
    tolerance_mol: float
    scaled_error: float


def richardson_error(
    full_amounts: Mapping[str, float],
    half_amounts: Mapping[str, float],
    config: RichardsonErrorControlConfig,
) -> ErrorEstimate:
    denominator = 2.0**config.temporal_order - 1.0
    if not isfinite(denominator) or denominator <= 0.0:
        raise ValueError("Richardson denominator must be finite and positive")

    estimates: list[tuple[str, float, float, float]] = []
    for item in config.controlled_minerals:
        full = float(full_amounts[item.name])
        half = float(half_amounts[item.name])
        raw = abs(half - full) / denominator
        tolerance = item.absolute_tolerance.value + config.relative_tolerance * max(
            abs(half), item.reference_floor.value
        )
        scaled = raw / tolerance if tolerance > 0.0 else float("nan")
        if not all(isfinite(value) for value in (full, half, raw, tolerance, scaled)):
            raise ValueError(f"non-finite Richardson error for {item.name}")
        estimates.append((item.name, raw, tolerance, scaled))

    name, raw, tolerance, scaled = max(estimates, key=lambda item: item[3])
    return ErrorEstimate(scaled, name, raw, tolerance, scaled)


def controller_step(
    attempted_dt_s: float,
    error: float,
    *,
    temporal_order: float,
    safety_factor: float,
    shrink_factor: float,
    growth_factor: float,
    dt_min_s: float,
    dt_max_s: float,
    accepted: bool,
) -> float:
    if error == 0.0:
        factor = growth_factor
    elif not isfinite(error) or error < 0.0:
        factor = shrink_factor
    else:
        factor = safety_factor * error ** (-1.0 / (temporal_order + 1.0))
        factor = min(growth_factor, max(shrink_factor, factor))
    if not accepted:
        factor = min(1.0, factor)
        return min(attempted_dt_s, max(dt_min_s, attempted_dt_s * factor))
    return min(dt_max_s, max(dt_min_s, attempted_dt_s * factor))


def run_error_controlled_timesteps(run: SolverRun) -> tuple[Any, dict[str, Any]]:
    initial_state = run.initial_state
    timestep = run.timestep
    error_config = timestep.error_control
    controller_dt_s = run.case.dt_initial_s
    previous_observation: dict[str, Any] | None = None
    current_observation = observe_state(run, run.state, run.time_s)
    pending_soft_cap_s: float | None = None
    pending_soft_types: tuple[str, ...] = ()
    localizations_at_current_time = 0

    while run.time_s < run.case.duration_s:
        if run.is_cancelled():
            return initial_state, run.cancelled("before_error_controlled_trial")
        if run.kinetic_attempts >= timestep.max_internal_steps:
            return initial_state, run.progress(
                completed=False,
                termination_reason="max_internal_steps_exceeded",
                failed_stage="timestep_controller",
                error_message=(
                    "error-controlled controller reached "
                    f"max_internal_steps={timestep.max_internal_steps}"
                ),
                accepted_state_restored=True,
            )

        forced_target_s = next_forced_target(
            run.time_s,
            run.case.duration_s,
            run.next_output_time,
            run.next_checkpoint_time,
        )
        event_dt_s, event_type, event_target_s = _predicted_event_cap(
            run,
            previous_observation,
            current_observation,
            controller_dt_s,
        )
        if pending_soft_cap_s is not None and (
            event_dt_s is None or pending_soft_cap_s < event_dt_s
        ):
            event_dt_s = pending_soft_cap_s
            event_type = ";".join(pending_soft_types)
            event_target_s = run.time_s + pending_soft_cap_s
        capped_proposal_s = min(
            controller_dt_s,
            event_dt_s if event_dt_s is not None else controller_dt_s,
        )
        target_time_s = adaptive_target(
            run.time_s, capped_proposal_s, forced_target_s
        )
        if event_target_s is not None and target_time_s < event_target_s:
            event_type = ""
            event_target_s = None
        dt_s = float(Decimal(str(target_time_s)) - Decimal(str(run.time_s)))
        start_s = run.time_s
        accepted_state = run.snapshot_state(run.state)
        full_state = run.snapshot_state(accepted_state)
        half_state = run.snapshot_state(accepted_state)
        full_solver = kinetics_solver(run.system, run.kinetic_specs)
        half_solver = kinetics_solver(run.system, run.kinetic_specs)

        full_result, full_wall, full_error = timed_solve(
            full_solver, full_state, dt_s=dt_s, conditions=run.conditions
        )
        results: list[Any | None] = [full_result, None, None]
        walls: list[float | None] = [full_wall, None, None]
        errors: list[Exception | None] = [full_error, None, None]
        full_reason = failure_reason(full_result, full_error, "Richardson full step")
        if full_reason is None:
            half_result_1, half_wall_1, half_error_1 = timed_solve(
                half_solver, half_state, dt_s=dt_s / 2.0, conditions=run.conditions
            )
            results[1], walls[1], errors[1] = half_result_1, half_wall_1, half_error_1
            half_reason_1 = failure_reason(
                half_result_1, half_error_1, "Richardson first half step"
            )
        else:
            half_reason_1 = None
        if full_reason is None and half_reason_1 is None:
            half_result_2, half_wall_2, half_error_2 = timed_solve(
                half_solver, half_state, dt_s=dt_s / 2.0, conditions=run.conditions
            )
            results[2], walls[2], errors[2] = half_result_2, half_wall_2, half_error_2
            half_reason_2 = failure_reason(
                half_result_2, half_error_2, "Richardson second half step"
            )
        else:
            half_reason_2 = None

        solve_calls = sum(result is not None or error is not None for result, error in zip(results, errors))
        run.reaktoro_solve_calls += solve_calls
        run.kinetic_attempts += 1
        solver_reason = full_reason or half_reason_1 or half_reason_2
        cancelled = run.is_cancelled()
        if cancelled:
            solver_reason = "cooperative cancellation requested before trial commit" + (
                f"; {solver_reason}" if solver_reason else ""
            )

        if solver_reason is not None:
            run.state.assign(accepted_state)
            run.failed_steps += 1
            run.retries_at_current_time += 1
            is_cancellation = cancelled
            if full_reason or half_reason_1 or half_reason_2:
                run.solver_failed_attempts += 1
                run.rejection_reason_counts["solver_failure"] = (
                    run.rejection_reason_counts.get("solver_failure", 0) + 1
                )
            next_dt_s = _solver_failure_retry(run, dt_s)
            run.emit_record(
                _composite_record(
                    run,
                    start_s=start_s,
                    target_s=start_s,
                    proposed_dt_s=controller_dt_s,
                    dt_s=dt_s,
                    results=tuple(results),
                    wall_times=tuple(walls),
                    accepted=False,
                    failure=solver_reason,
                    rejection_reason="cancellation" if is_cancellation else "solver_failure",
                    next_dt_s=next_dt_s,
                    solve_calls=solve_calls,
                    solver_failure=bool(full_reason or half_reason_1 or half_reason_2),
                    event_type=event_type,
                    event_target_s=event_target_s,
                )
            )
            if is_cancellation:
                return initial_state, run.cancelled("after_error_controlled_trial")
            if _retry_exhausted(run, dt_s, next_dt_s):
                return initial_state, run.progress(
                    completed=False,
                    termination_reason=(
                        "retry_limit_exceeded"
                        if run.retries_at_current_time
                        > timestep.step_size.max_retries_per_step
                        else "minimum_timestep_rejected"
                    ),
                    failed_stage="error_controlled_trial",
                    error_message=solver_reason,
                    exception_type=next(
                        (type(error).__name__ for error in errors if error is not None),
                        None,
                    ),
                    failed_attempt_target_time_s=target_time_s,
                    failed_attempt_dt_s=dt_s,
                    accepted_state_restored=True,
                )
            controller_dt_s = next_dt_s
            continue

        full_amounts = _controlled_amounts(full_state, error_config)
        half_amounts = _controlled_amounts(half_state, error_config)
        try:
            estimate = richardson_error(full_amounts, half_amounts, error_config)
        except ValueError as error:
            estimate = ErrorEstimate(float("nan"), "", float("nan"), float("nan"), float("nan"))
            estimate_error = str(error)
        else:
            estimate_error = ""

        temporal_accept = isfinite(estimate.value) and estimate.value <= 1.0
        next_dt_s = controller_step(
            dt_s,
            estimate.value,
            temporal_order=error_config.temporal_order,
            safety_factor=timestep.step_size.safety_factor,
            shrink_factor=timestep.step_size.shrink_factor,
            growth_factor=timestep.step_size.growth_factor,
            dt_min_s=run.case.dt_min_s,
            dt_max_s=run.case.dt_max_s,
            accepted=temporal_accept,
        )
        if not temporal_accept:
            run.state.assign(accepted_state)
            run.failed_steps += 1
            run.retries_at_current_time += 1
            run.temporal_error_rejections += 1
            reason = (
                "non_finite_temporal_error"
                if not isfinite(estimate.value)
                else "temporal_error_rejection"
            )
            run.rejection_reason_counts[reason] = (
                run.rejection_reason_counts.get(reason, 0) + 1
            )
            run.emit_record(
                _composite_record(
                    run,
                    start_s=start_s,
                    target_s=start_s,
                    proposed_dt_s=controller_dt_s,
                    dt_s=dt_s,
                    results=tuple(results),
                    wall_times=tuple(walls),
                    accepted=False,
                    failure=estimate_error or reason,
                    rejection_reason=reason,
                    next_dt_s=next_dt_s,
                    solve_calls=solve_calls,
                    temporal_error_rejection=True,
                    estimate=estimate,
                    event_type=event_type,
                    event_target_s=event_target_s,
                )
            )
            if _retry_exhausted(run, dt_s, next_dt_s):
                return initial_state, run.progress(
                    completed=False,
                    termination_reason=(
                        "retry_limit_exceeded"
                        if run.retries_at_current_time
                        > timestep.step_size.max_retries_per_step
                        else "minimum_timestep_rejected"
                    ),
                    failed_stage="temporal_error_control",
                    error_message=estimate_error or reason,
                    failed_attempt_target_time_s=target_time_s,
                    failed_attempt_dt_s=dt_s,
                    accepted_state_restored=True,
                )
            controller_dt_s = next_dt_s
            continue

        end_observation = observe_state(run, half_state, target_time_s)
        localized_dt_s, localized_mineral = _hard_localization(
            run, current_observation, end_observation, dt_s
        )
        hard = timestep.events.hard_mineral_exhaustion
        if localized_dt_s is not None and hard is not None:
            run.state.assign(accepted_state)
            run.failed_steps += 1
            run.retries_at_current_time += 1
            if localizations_at_current_time >= hard.max_localizations:
                reason = f"hard event localization limit reached for {localized_mineral}"
                run.rejection_reason_counts["hard_event_localization_limit"] = (
                    run.rejection_reason_counts.get("hard_event_localization_limit", 0)
                    + 1
                )
                run.emit_record(
                    _composite_record(
                        run,
                        start_s=start_s,
                        target_s=start_s,
                        proposed_dt_s=controller_dt_s,
                        dt_s=dt_s,
                        results=tuple(results),
                        wall_times=tuple(walls),
                        accepted=False,
                        failure=reason,
                        rejection_reason="hard_event_localization_limit",
                        next_dt_s=localized_dt_s,
                        solve_calls=solve_calls,
                        estimate=estimate,
                        event_type=f"hard_mineral_exhaustion:{localized_mineral}",
                        event_target_s=start_s + localized_dt_s,
                    )
                )
                return initial_state, run.progress(
                    completed=False,
                    termination_reason="hard_event_localization_limit",
                    failed_stage="hard_event_localization",
                    error_message=reason,
                    failed_attempt_target_time_s=target_time_s,
                    failed_attempt_dt_s=dt_s,
                    accepted_state_restored=True,
                )
            run.event_localizations += 1
            localizations_at_current_time += 1
            reason = f"hard_event_localization:{localized_mineral}"
            run.rejection_reason_counts["hard_event_localization"] = (
                run.rejection_reason_counts.get("hard_event_localization", 0) + 1
            )
            run.emit_record(
                _composite_record(
                    run,
                    start_s=start_s,
                    target_s=start_s,
                    proposed_dt_s=controller_dt_s,
                    dt_s=dt_s,
                    results=tuple(results),
                    wall_times=tuple(walls),
                    accepted=False,
                    failure=reason,
                    rejection_reason="hard_event_localization",
                    next_dt_s=localized_dt_s,
                    solve_calls=solve_calls,
                    estimate=estimate,
                    event_type=reason,
                    event_target_s=start_s + localized_dt_s,
                    controller_history_reset=False,
                )
            )
            controller_dt_s = localized_dt_s
            continue

        admissibility_error = _admissibility_error(
            end_observation,
            error_config.negative_amount_tolerance.value,
        )
        if admissibility_error is not None:
            run.state.assign(accepted_state)
            run.failed_steps += 1
            run.retries_at_current_time += 1
            reason = "state_admissibility_rejection"
            run.rejection_reason_counts[reason] = (
                run.rejection_reason_counts.get(reason, 0) + 1
            )
            next_dt_s = min(
                dt_s,
                max(run.case.dt_min_s, dt_s * timestep.step_size.shrink_factor),
            )
            run.emit_record(
                _composite_record(
                    run,
                    start_s=start_s,
                    target_s=start_s,
                    proposed_dt_s=controller_dt_s,
                    dt_s=dt_s,
                    results=tuple(results),
                    wall_times=tuple(walls),
                    accepted=False,
                    failure=admissibility_error,
                    rejection_reason=reason,
                    next_dt_s=next_dt_s,
                    solve_calls=solve_calls,
                    estimate=estimate,
                    event_type=event_type,
                    event_target_s=event_target_s,
                )
            )
            if _retry_exhausted(run, dt_s, next_dt_s):
                return initial_state, run.progress(
                    completed=False,
                    termination_reason=(
                        "retry_limit_exceeded"
                        if run.retries_at_current_time
                        > timestep.step_size.max_retries_per_step
                        else "minimum_timestep_rejected"
                    ),
                    failed_stage="state_admissibility",
                    error_message=admissibility_error,
                    failed_attempt_target_time_s=target_time_s,
                    failed_attempt_dt_s=dt_s,
                    accepted_state_restored=True,
                )
            controller_dt_s = next_dt_s
            continue

        hard_events = _landed_hard_events(run, current_observation, end_observation)
        soft_types = _soft_event_types(run, current_observation, end_observation)
        detected_event_target_s = target_time_s if hard_events or soft_types else event_target_s
        controller_history_reset = bool(hard_events)
        if hard_events:
            next_dt_s = run.case.hard_event_restart_dt_s
        pending_soft_types = tuple(soft_types)
        pending_soft_cap_s = (
            max(run.case.dt_min_s, dt_s * timestep.events.soft.timestep_cap_factor)
            if soft_types and timestep.events.soft is not None
            else None
        )

        run.state.assign(half_state)
        record = _composite_record(
            run,
            start_s=start_s,
            target_s=target_time_s,
            proposed_dt_s=controller_dt_s,
            dt_s=dt_s,
            results=tuple(results),
            wall_times=tuple(walls),
            accepted=True,
            failure="",
            rejection_reason="",
            next_dt_s=next_dt_s,
            solve_calls=solve_calls,
            estimate=estimate,
            event_type=";".join(hard_events or soft_types) or event_type,
            event_target_s=detected_event_target_s,
            controller_history_reset=controller_history_reset,
        )
        run.accept_step(dt_s, target_time_s)
        run.retries_at_current_time = 0
        localizations_at_current_time = 0
        run.emit_record(record)
        row = None
        if run.output_due(run.time_s):
            row = run.collect_row(run.case, run.state, record, initial_state)
            run.emit_row(row)
        if run.checkpoint_due(run.time_s):
            run.checkpoint_count += 1
            run.emit_checkpoint(record, run.state)
        if run.time_s == run.case.duration_s:
            run.emit_boundary(
                "final",
                row or run.collect_row(run.case, run.state, record, initial_state),
            )
        previous_observation, current_observation = current_observation, end_observation
        controller_dt_s = next_dt_s

    return initial_state, run.progress(completed=True)


def observe_state(run: SolverRun, state: Any, time_s: float) -> dict[str, Any]:
    aqueous = rkt.AqueousProps(state)
    amounts = {
        mineral.name: float(state.speciesAmount(mineral.name))
        for mineral in run.case.config.minerals
    }
    saturation_indices = {
        mineral.name: float(aqueous.saturationIndex(mineral.name))
        for mineral in run.case.config.minerals
    }
    rates: dict[str, float] = {}
    soft = run.timestep.events.soft
    if soft is not None and soft.max_reaction_rate_relative_change is not None:
        props = rkt.ChemicalProps(state)
        rates = {
            mineral.name: float(props.reactionRate(mineral.name))
            for mineral in run.case.config.minerals
            if mineral.role == "kinetic"
        }
    return {
        "time_s": float(time_s),
        "pH": float(aqueous.pH()),
        "amounts": amounts,
        "saturation_indices": saturation_indices,
        "rates": rates,
    }


def _controlled_amounts(state: Any, config: RichardsonErrorControlConfig) -> dict[str, float]:
    return {
        item.name: float(state.speciesAmount(item.name))
        for item in config.controlled_minerals
    }


def _solver_failure_retry(run: SolverRun, dt_s: float) -> float:
    return min(
        dt_s,
        max(
            run.case.dt_min_s,
            dt_s * run.timestep.step_size.solver_failure_shrink_factor,
        ),
    )


def _retry_exhausted(run: SolverRun, dt_s: float, next_dt_s: float) -> bool:
    return (
        run.retries_at_current_time > run.timestep.step_size.max_retries_per_step
        or dt_s <= run.case.dt_min_s
        or next_dt_s >= dt_s
    )


def _hard_localization(
    run: SolverRun,
    start: Mapping[str, Any],
    end: Mapping[str, Any],
    dt_s: float,
) -> tuple[float | None, str | None]:
    hard = run.timestep.events.hard_mineral_exhaustion
    if hard is None:
        return None, None
    tolerance = hard.amount_tolerance.value
    candidates: list[tuple[float, str]] = []
    for mineral in run.case.config.minerals:
        if mineral.role != "kinetic":
            continue
        before = float(start["amounts"][mineral.name])
        after = float(end["amounts"][mineral.name])
        if before > tolerance and after <= tolerance and before != after:
            localized = dt_s * (before - tolerance) / (before - after)
            if (
                localized > run.case.hard_event_time_tolerance_s
                and dt_s - localized > run.case.hard_event_time_tolerance_s
            ):
                candidates.append((localized, mineral.name))
    return min(candidates) if candidates else (None, None)


def _landed_hard_events(
    run: SolverRun,
    start: Mapping[str, Any],
    end: Mapping[str, Any],
) -> list[str]:
    hard = run.timestep.events.hard_mineral_exhaustion
    if hard is None:
        return []
    tolerance = hard.amount_tolerance.value
    return [
        f"hard_mineral_exhaustion:{mineral.name}"
        for mineral in run.case.config.minerals
        if mineral.role == "kinetic"
        and float(start["amounts"][mineral.name]) > tolerance
        and float(end["amounts"][mineral.name]) <= tolerance
    ]


def _soft_event_types(
    run: SolverRun,
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
) -> list[str]:
    soft = run.timestep.events.soft
    if soft is None:
        return []
    events: list[str] = []
    if soft.saturation_index_crossing:
        for name, before in previous["saturation_indices"].items():
            after = current["saturation_indices"][name]
            if before != after and before * after <= 0.0:
                events.append(f"soft_saturation_index_crossing:{name}")
    if soft.max_pH_change is not None and abs(current["pH"] - previous["pH"]) >= soft.max_pH_change:
        events.append("soft_rapid_pH_change")
    if soft.secondary_mineral_appearance is not None:
        threshold = soft.secondary_mineral_appearance.value
        for mineral in run.case.config.minerals:
            if (
                mineral.role == "equilibrium"
                and previous["amounts"][mineral.name] <= threshold
                and current["amounts"][mineral.name] > threshold
            ):
                events.append(f"soft_secondary_mineral_appearance:{mineral.name}")
    if soft.max_reaction_rate_relative_change is not None:
        floor = soft.reaction_rate_floor.value
        for name, before in previous["rates"].items():
            after = current["rates"][name]
            relative = abs(after - before) / max(abs(after), abs(before), floor)
            if relative >= soft.max_reaction_rate_relative_change:
                events.append(f"soft_rapid_reaction_rate_change:{name}")
    return events


def _admissibility_error(
    observation: Mapping[str, Any], negative_amount_tolerance_mol: float
) -> str | None:
    pH = float(observation["pH"])
    if not isfinite(pH):
        return "accepted candidate has non-finite pH"
    for name, amount in observation["amounts"].items():
        value = float(amount)
        if not isfinite(value) or value < -negative_amount_tolerance_mol:
            return f"accepted candidate has inadmissible mineral amount for {name}"
    for label in ("saturation_indices", "rates"):
        for name, value in observation[label].items():
            if not isfinite(float(value)):
                return f"accepted candidate has non-finite {label} value for {name}"
    return None


def _predicted_event_cap(
    run: SolverRun,
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any],
    proposed_dt_s: float,
) -> tuple[float | None, str, float | None]:
    if previous is None:
        return None, "", None
    candidates: list[tuple[float, str, float]] = []
    hard = run.timestep.events.hard_mineral_exhaustion
    if hard is not None:
        tolerance = hard.amount_tolerance.value
        for mineral in run.case.config.minerals:
            if mineral.role != "kinetic":
                continue
            previous_gap = previous["amounts"][mineral.name] - tolerance
            current_gap = current["amounts"][mineral.name] - tolerance
            if previous_gap <= current_gap or current_gap <= 0.0:
                continue
            target = _future_zero(
                previous["time_s"],
                previous_gap,
                current["time_s"],
                current_gap,
            )
            if (
                target is not None
                and target - current["time_s"] > run.case.hard_event_time_tolerance_s
                and target <= current["time_s"] + proposed_dt_s
            ):
                candidates.append(
                    (target - current["time_s"], f"hard_mineral_exhaustion:{mineral.name}", target)
                )
    soft = run.timestep.events.soft
    if soft is not None and soft.saturation_index_crossing:
        for name, value in current["saturation_indices"].items():
            target = _future_zero(
                previous["time_s"],
                previous["saturation_indices"][name],
                current["time_s"],
                value,
            )
            if (
                target is not None
                and target - current["time_s"] >= run.case.dt_min_s
                and target <= current["time_s"] + proposed_dt_s
            ):
                candidates.append(
                    (target - current["time_s"], f"soft_saturation_index_prediction:{name}", target)
                )
    return min(candidates) if candidates else (None, "", None)


def _future_zero(t0: float, g0: float, t1: float, g1: float) -> float | None:
    if not all(isfinite(value) for value in (t0, g0, t1, g1)) or t1 <= t0 or g1 == g0:
        return None
    target = t1 - g1 * (t1 - t0) / (g1 - g0)
    return target if isfinite(target) and target > t1 else None


def _composite_record(
    run: SolverRun,
    *,
    start_s: float,
    target_s: float,
    proposed_dt_s: float,
    dt_s: float,
    results: tuple[Any | None, Any | None, Any | None],
    wall_times: tuple[float | None, float | None, float | None],
    accepted: bool,
    failure: str,
    rejection_reason: str,
    next_dt_s: float,
    solve_calls: int,
    solver_failure: bool = False,
    temporal_error_rejection: bool = False,
    estimate: ErrorEstimate | None = None,
    event_type: str = "",
    event_target_s: float | None = None,
    controller_history_reset: bool = False,
    solver_reconstruction: bool | None = None,
) -> dict[str, Any]:
    present_results = [result for result in results if result is not None]
    last_result = present_results[-1] if present_results else None
    record = solver_record(
        step_index=run.step_index,
        attempt_index=run.kinetic_attempts or None,
        stage="adaptive_error_controlled_trial",
        time_start_s=start_s,
        time_end_s=target_s,
        dt_s=dt_s,
        result=last_result,
        wall_time_s=sum(value or 0.0 for value in wall_times),
        accepted=accepted,
        failure_reason=failure,
        next_dt_s=next_dt_s,
    )
    record.update(
        {
            "timestep_mode": "adaptive_error_controlled",
            "accepted_time_before_s": start_s,
            "accepted_time_after_s": target_s,
            "proposed_dt_s": proposed_dt_s,
            "effective_dt_s": dt_s,
            "full_step_succeeded": _succeeded(results[0]),
            "first_half_step_succeeded": _succeeded(results[1]),
            "second_half_step_succeeded": _succeeded(results[2]),
            "full_step_iterations": _iterations(results[0]),
            "first_half_step_iterations": _iterations(results[1]),
            "second_half_step_iterations": _iterations(results[2]),
            "full_step_wall_time_s": wall_times[0],
            "first_half_step_wall_time_s": wall_times[1],
            "second_half_step_wall_time_s": wall_times[2],
            "reaktoro_solve_calls": solve_calls,
            "richardson_error": estimate.value if estimate else None,
            "worst_controlled_mineral": estimate.worst_mineral if estimate else None,
            "raw_error_mol": estimate.raw_error_mol if estimate else None,
            "error_tolerance_mol": estimate.tolerance_mol if estimate else None,
            "scaled_error": estimate.scaled_error if estimate else None,
            "rejection_reason": rejection_reason,
            "solver_failure": solver_failure,
            "temporal_error_rejection": temporal_error_rejection,
            "event_cap_type": event_type,
            "event_target_time_s": event_target_s,
            "retry_count": run.retries_at_current_time,
            "solver_reconstruction": (
                dt_s > 0.0
                if solver_reconstruction is None
                else solver_reconstruction
            ),
            "controller_history_reset": controller_history_reset,
        }
    )
    record["iterations"] = sum(_iterations(result) or 0 for result in results)
    record["solver_succeeded"] = not solver_failure and bool(present_results) and all(
        _succeeded(result) is True for result in present_results
    )
    return record


def _succeeded(result: Any | None) -> bool | None:
    return bool(result.succeeded()) if result is not None else None


def _iterations(result: Any | None) -> int | None:
    return int(result.iterations()) if result is not None else None

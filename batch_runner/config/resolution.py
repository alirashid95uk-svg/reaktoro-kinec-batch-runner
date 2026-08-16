"""Resolve validated cases into absolute paths and solver schedules."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR, localcontext
from heapq import merge
from math import isfinite
from pathlib import Path
from typing import Any

from ._base import PROJECT_ROOT, TimeUnit
from .case import CaseConfig
from .timestep import (
    CheckpointScheduleConfig,
    FixedTimestepConfig,
    OutputScheduleConfig,
    TimeValue,
)


@dataclass(frozen=True)
class ResolvedCase:
    config: CaseConfig
    config_path: Path
    source_config_sha256: str | None
    output_dir: Path
    database_path: Path | None
    kinetics_path: Path | None
    duration_s: float
    dt_s: float
    dt_initial_s: float
    dt_min_s: float
    dt_max_s: float
    full_steps: int
    final_step_s: float
    resolved_output_times_s: tuple[float, ...] | None
    checkpoint_times_s: tuple[float, ...]
    extra_solver_targets_s: tuple[float, ...]
    minimum_accepted_steps: int

    @property
    def base_internal_step_count(self) -> int:
        if self.config.solver.timestep.mode != "fixed":
            return 0
        return self.full_steps + int(self.final_step_s > 0)

    @property
    def internal_step_count(self) -> int:
        if self.config.solver.timestep.mode != "fixed":
            return 0
        return self.base_internal_step_count + len(self.extra_solver_targets_s)

    @property
    def requested_output_row_count(self) -> int | None:
        if not self.config.kinetics.enabled:
            schedule = self.config.solver.timestep.output_schedule
            return int(schedule.include_initial or schedule.include_final)
        if self.resolved_output_times_s is not None:
            return len(self.resolved_output_times_s)
        schedule = self.config.solver.timestep.output_schedule
        if self.config.solver.timestep.mode != "fixed":
            return None
        return self.internal_step_count + int(schedule.include_initial) - int(
            not schedule.include_final
        )

    def output_times_s(self) -> Iterator[float]:
        schedule = self.config.solver.timestep.output_schedule
        if not self.config.kinetics.enabled:
            if schedule.include_initial or schedule.include_final:
                yield 0.0
            return
        if self.resolved_output_times_s is not None:
            yield from self.resolved_output_times_s
            return
        if schedule.include_initial:
            yield 0.0
        if self.config.solver.timestep.mode != "fixed":
            return
        for target_time_s in self._base_target_times_s():
            if target_time_s != self.duration_s or schedule.include_final:
                yield target_time_s

    def fixed_steps_s(self) -> Iterator[tuple[float, float]]:
        if not self.config.kinetics.enabled or self.config.solver.timestep.mode != "fixed":
            return
        current_time = Decimal("0")
        for target_time_s in merge(self._base_target_times_s(), self.extra_solver_targets_s):
            target_time = Decimal(str(target_time_s))
            yield float(target_time - current_time), target_time_s
            current_time = target_time

    def _base_target_times_s(self) -> Iterator[float]:
        dt_s = Decimal(str(self.dt_s))
        for step_index in range(1, self.full_steps + 1):
            target_time_s = (
                self.duration_s
                if self.final_step_s == 0 and step_index == self.full_steps
                else float(dt_s * step_index)
            )
            yield target_time_s
        if self.final_step_s > 0:
            yield self.duration_s

    def output_schedule_summary(self) -> dict[str, Any]:
        schedule = self.config.solver.timestep.output_schedule
        return {
            "mode": schedule.mode,
            "include_initial": schedule.include_initial,
            "include_final": schedule.include_final,
            "resolved_count": self.requested_output_row_count,
            "resolved_times_s": (
                list(self.resolved_output_times_s)
                if self.resolved_output_times_s is not None
                else None
            ),
            "representation": (
                (
                    "every actual accepted solver step, including schedule-split steps"
                    if schedule.mode == "every_internal_step"
                    else "generated from accepted adaptive steps"
                )
                if self.resolved_output_times_s is None
                else "sorted unique absolute timestamps"
            ),
        }

    def checkpoint_schedule_summary(self) -> dict[str, Any]:
        return {
            "enabled": self.config.solver.timestep.checkpoint_schedule.enabled,
            "resolved_count": len(self.checkpoint_times_s),
            "resolved_times_s": list(self.checkpoint_times_s),
        }

    @property
    def kinetic_parameter_sha256(self) -> str | None:
        return _sha256(self.kinetics_path) if self.kinetics_path is not None else None

    def as_dict(self) -> dict[str, Any]:
        data = self.config.model_dump(mode="json")
        data["paths"]["output_dir"] = str(self.output_dir)
        if self.database_path is not None:
            data["database"]["path"] = str(self.database_path)
        if self.kinetics_path is not None:
            data["kinetics"]["path"] = str(self.kinetics_path)
            data["kinetics"]["sha256"] = self.kinetic_parameter_sha256
        data["solver"]["timestep"]["derived_duration_s"] = self.duration_s
        if self.config.solver.timestep.mode == "fixed":
            data["solver"]["timestep"]["derived_dt_s"] = self.dt_s
            data["solver"]["timestep"]["derived_full_steps"] = self.full_steps
            data["solver"]["timestep"]["derived_final_step_s"] = self.final_step_s
            data["solver"]["timestep"]["derived_base_internal_steps"] = (
                self.base_internal_step_count
            )
            data["solver"]["timestep"]["derived_internal_steps"] = self.internal_step_count
        else:
            data["solver"]["timestep"]["derived_dt_initial_s"] = self.dt_initial_s
            data["solver"]["timestep"]["derived_dt_min_s"] = self.dt_min_s
            data["solver"]["timestep"]["derived_dt_max_s"] = self.dt_max_s
        data["solver"]["timestep"]["estimated_result_rows"] = self.requested_output_row_count
        data["solver"]["timestep"]["minimum_possible_accepted_steps"] = (
            self.minimum_accepted_steps
        )
        data["solver"]["timestep"]["resolved_output_schedule"] = self.output_schedule_summary()
        data["solver"]["timestep"]["resolved_checkpoint_schedule"] = (
            self.checkpoint_schedule_summary()
        )
        data["source_config"] = str(self.config_path)
        return data


def resolve_case(
    config: CaseConfig,
    config_path: Path,
    *,
    source_config_sha256: str | None = None,
) -> ResolvedCase:
    output_dir = _resolve_project_path(config.paths.output_dir)
    if output_dir.exists():
        raise FileExistsError(f"output directory already exists: {output_dir}")

    database_path = None
    if config.database.source == "local":
        database_path = _resolve_project_path(config.database.path)
        if not database_path.is_file():
            raise FileNotFoundError(f"local PHREEQC database does not exist: {database_path}")
        if database_path.suffix.lower() != ".dat":
            raise ValueError(f"local PHREEQC database must use a .dat path: {database_path}")

    kinetics_path = None
    if config.kinetics.enabled:
        kinetics_path = _resolve_project_path(config.kinetics.path)
        if not kinetics_path.is_file():
            raise FileNotFoundError(f"kinetic parameter file does not exist: {kinetics_path}")

    duration_s = 0.0
    dt_s = 0.0
    dt_initial_s = 0.0
    dt_min_s = 0.0
    dt_max_s = 0.0
    full_steps = 0
    final_step_s = 0.0
    resolved_output_times_s = None
    checkpoint_times_s: tuple[float, ...] = ()
    extra_solver_targets_s: tuple[float, ...] = ()
    minimum_accepted_steps = 0
    if config.kinetics.enabled:
        timestep = config.solver.timestep
        year_days = timestep.time.year_definition_days
        time = timestep.time
        duration = _time_to_seconds_decimal(time.duration_value, time.duration_unit, year_days)
        duration_s = float(duration)
        if not isfinite(duration_s):
            if isinstance(timestep, FixedTimestepConfig):
                raise ValueError("resolved duration_s and dt_s must be finite")
            raise ValueError("resolved adaptive duration_s must be finite")
        resolved_output_times_s = _resolve_output_schedule(
            timestep.output_schedule,
            duration,
            year_days,
            timestep.max_internal_steps,
        )
        checkpoint_times_s = _resolve_checkpoint_schedule(
            timestep.checkpoint_schedule,
            duration,
            year_days,
        )
        if isinstance(timestep, FixedTimestepConfig):
            step = timestep.step_size.dt
            dt = _time_to_seconds_decimal(step.value, step.unit, year_days)
            dt_s = float(dt)
            dt_initial_s = dt_min_s = dt_max_s = dt_s
            if not isfinite(dt_s):
                raise ValueError("resolved dt_s must be finite")
            full_steps_decimal, final_step = divmod(duration, dt)
            full_steps = int(full_steps_decimal)
            final_step_s = float(final_step)
            scheduled_targets = set(checkpoint_times_s)
            if resolved_output_times_s is not None:
                scheduled_targets.update(resolved_output_times_s)
            extra_solver_targets_s = tuple(
                target
                for target in sorted(scheduled_targets)
                if target > 0.0
                and not _is_base_target(
                    Decimal(str(target)),
                    duration,
                    dt,
                )
            )
            base_internal_steps = full_steps + int(final_step_s > 0)
            internal_steps = base_internal_steps + len(extra_solver_targets_s)
            minimum_accepted_steps = internal_steps
            if internal_steps > timestep.max_internal_steps:
                raise ValueError(
                    "fixed timestep preflight rejected case: "
                    f"requested_internal_steps={internal_steps}, "
                    f"max_internal_steps={timestep.max_internal_steps}, "
                    f"base_internal_steps={base_internal_steps}, "
                    f"duration_s={duration_s}, dt_s={dt_s}"
                )
        else:
            step = timestep.step_size
            dt_initial = _time_to_seconds_decimal(
                step.dt_initial.value, step.dt_initial.unit, year_days
            )
            dt_min = _time_to_seconds_decimal(step.dt_min.value, step.dt_min.unit, year_days)
            dt_max = _time_to_seconds_decimal(step.dt_max.value, step.dt_max.unit, year_days)
            dt_initial_s = float(dt_initial)
            dt_min_s = float(dt_min)
            dt_max_s = float(dt_max)
            if not all(isfinite(value) for value in (dt_initial_s, dt_min_s, dt_max_s)):
                raise ValueError("resolved adaptive timestep values must be finite")
            if not dt_min <= dt_initial <= dt_max:
                raise ValueError("adaptive timestep requires dt_min <= dt_initial <= dt_max")
            forced_targets = set(checkpoint_times_s)
            if resolved_output_times_s is not None:
                forced_targets.update(resolved_output_times_s)
            forced_targets.add(duration_s)
            forced_targets.discard(0.0)
            previous = Decimal("0")
            minimum_accepted_steps = 0
            for target_s in sorted(forced_targets):
                interval = Decimal(str(target_s)) - previous
                full, remainder = divmod(interval, dt_max)
                minimum_accepted_steps += int(full) + int(remainder > 0)
                previous = Decimal(str(target_s))
            if minimum_accepted_steps > timestep.max_internal_steps:
                raise ValueError(
                    "adaptive timestep preflight rejected case: "
                    f"minimum_possible_accepted_steps={minimum_accepted_steps}, "
                    f"max_internal_steps={timestep.max_internal_steps}, "
                    f"forced_interval_count={len(forced_targets)}, "
                    f"duration_s={duration_s}, dt_max_s={dt_max_s}"
                )

    return ResolvedCase(
        config=config,
        config_path=config_path,
        source_config_sha256=source_config_sha256,
        output_dir=output_dir,
        database_path=database_path,
        kinetics_path=kinetics_path,
        duration_s=duration_s,
        dt_s=dt_s,
        dt_initial_s=dt_initial_s,
        dt_min_s=dt_min_s,
        dt_max_s=dt_max_s,
        full_steps=full_steps,
        final_step_s=final_step_s,
        resolved_output_times_s=resolved_output_times_s,
        checkpoint_times_s=checkpoint_times_s,
        extra_solver_targets_s=extra_solver_targets_s,
        minimum_accepted_steps=minimum_accepted_steps,
    )


def _resolve_output_schedule(
    schedule: OutputScheduleConfig,
    duration_s: Decimal,
    year_definition_days: float | None,
    max_internal_steps: int,
) -> tuple[float, ...] | None:
    if schedule.mode == "every_internal_step":
        return None

    times: set[Decimal] = set()
    for index, item in enumerate(schedule.explicit_times):
        times.add(
            _bounded_time_seconds(
                item,
                duration_s,
                year_definition_days,
                f"output_schedule.explicit_times[{index}]",
            )
        )

    logarithmic = schedule.logarithmic
    if logarithmic is not None:
        start = _bounded_time_seconds(
            logarithmic.start,
            duration_s,
            year_definition_days,
            "output_schedule.logarithmic.start",
        )
        end = _bounded_time_seconds(
            logarithmic.end,
            duration_s,
            year_definition_days,
            "output_schedule.logarithmic.end",
        )
        if start > end:
            raise ValueError("output_schedule logarithmic start must not exceed end")
        with localcontext() as context:
            context.prec = 40
            last_index = int(
                (((end / start).log10()) * logarithmic.points_per_decade).to_integral_value(
                    rounding=ROUND_FLOOR
                )
            )
            if last_index + 1 > max_internal_steps:
                raise ValueError(
                    "logarithmic output schedule exceeds max_internal_steps before generation: "
                    f"minimum_log_points={last_index + 1}, "
                    f"max_internal_steps={max_internal_steps}"
                )
            for index in range(last_index + 1):
                value = start * (
                    Decimal("10")
                    ** (Decimal(index) / Decimal(logarithmic.points_per_decade))
                )
                if value <= end:
                    times.add(value)
        times.add(end)

    if schedule.include_initial:
        times.add(Decimal("0"))
    if schedule.include_final:
        times.add(duration_s)
    return _unique_float_times(times)


def _resolve_checkpoint_schedule(
    schedule: CheckpointScheduleConfig,
    duration_s: Decimal,
    year_definition_days: float | None,
) -> tuple[float, ...]:
    if not schedule.enabled:
        return ()
    return _unique_float_times(
        {
            _bounded_time_seconds(
                item,
                duration_s,
                year_definition_days,
                f"checkpoint_schedule.times[{index}]",
            )
            for index, item in enumerate(schedule.times)
        }
    )


def _bounded_time_seconds(
    value: TimeValue,
    duration_s: Decimal,
    year_definition_days: float | None,
    field_name: str,
) -> Decimal:
    seconds = _time_to_seconds_decimal(value.value, value.unit, year_definition_days)
    if seconds > duration_s:
        raise ValueError(f"{field_name} exceeds configured duration")
    if not isfinite(float(seconds)):
        raise ValueError(f"{field_name} resolves to a non-finite number of seconds")
    return seconds


def _unique_float_times(times: set[Decimal]) -> tuple[float, ...]:
    values = tuple(sorted({float(value) for value in times}))
    if not all(isfinite(value) for value in values):
        raise ValueError("resolved schedule timestamps must be finite")
    return values


def _is_base_target(target_s: Decimal, duration_s: Decimal, dt_s: Decimal) -> bool:
    return target_s == duration_s or (Decimal("0") < target_s < duration_s and target_s % dt_s == 0)


def _time_to_seconds_decimal(
    value: float,
    unit: TimeUnit,
    year_definition_days: float | None,
) -> Decimal:
    factors = {
        "second": Decimal("1"),
        "seconds": Decimal("1"),
        "minute": Decimal("60"),
        "minutes": Decimal("60"),
        "hour": Decimal("3600"),
        "hours": Decimal("3600"),
        "day": Decimal("86400"),
        "days": Decimal("86400"),
    }
    if unit in {"year", "years"}:
        if year_definition_days is None:
            raise ValueError("year_definition_days is required for year conversion")
        factor = Decimal(str(year_definition_days)) * Decimal("86400")
    else:
        factor = factors[unit]
    return Decimal(str(value)) * factor


def _resolve_project_path(value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

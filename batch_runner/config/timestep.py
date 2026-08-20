"""Solver workflow and timestep configuration models."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from ._base import StrictModel, TimeUnit, WorkflowMode


class SolverWorkflowConfig(StrictModel):
    mode: WorkflowMode


class TimeValue(StrictModel):
    value: float = Field(gt=0, allow_inf_nan=False)
    unit: TimeUnit


class LogarithmicScheduleConfig(StrictModel):
    start: TimeValue
    end: TimeValue
    points_per_decade: int = Field(gt=0)


class OutputScheduleConfig(StrictModel):
    mode: Literal["every_internal_step", "explicit", "logarithmic", "hybrid"] = (
        "every_internal_step"
    )
    include_initial: bool = True
    include_final: bool = True
    explicit_times: list[TimeValue] = Field(default_factory=list)
    logarithmic: LogarithmicScheduleConfig | None = None

    @model_validator(mode="after")
    def validate_mode_fields(self) -> "OutputScheduleConfig":
        if self.mode == "every_internal_step":
            if self.explicit_times or self.logarithmic is not None:
                raise ValueError("every_internal_step output schedule forbids explicit and logarithmic fields")
        elif self.mode == "explicit":
            if self.logarithmic is not None:
                raise ValueError("explicit output schedule forbids logarithmic")
        elif self.mode == "logarithmic":
            if self.explicit_times or self.logarithmic is None:
                raise ValueError("logarithmic output schedule requires logarithmic and forbids explicit_times")
        elif not self.explicit_times or self.logarithmic is None:
            raise ValueError("hybrid output schedule requires explicit_times and logarithmic")
        return self


class CheckpointScheduleConfig(StrictModel):
    enabled: bool = False
    times: list[TimeValue] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_enabled(self) -> "CheckpointScheduleConfig":
        if self.enabled and not self.times:
            raise ValueError("enabled checkpoint_schedule requires times")
        if not self.enabled and self.times:
            raise ValueError("disabled checkpoint_schedule forbids times")
        return self


class DurationConfig(StrictModel):
    duration_value: float = Field(gt=0, allow_inf_nan=False)
    duration_unit: TimeUnit
    year_definition_days: float | None = Field(default=None, gt=0, allow_inf_nan=False)


class FixedStepSizeConfig(StrictModel):
    dt: TimeValue


class AdaptiveStepSizeConfig(StrictModel):
    dt_initial: TimeValue
    dt_min: TimeValue
    dt_max: TimeValue
    growth_factor: float = Field(gt=1, allow_inf_nan=False)
    shrink_factor: float = Field(gt=0, lt=1, allow_inf_nan=False)
    max_retries_per_step: int = Field(ge=0)


class ErrorControlledStepSizeConfig(StrictModel):
    dt_initial: TimeValue
    dt_min: TimeValue
    dt_max: TimeValue
    safety_factor: float = Field(gt=0, lt=1, allow_inf_nan=False)
    growth_factor: float = Field(gt=1, allow_inf_nan=False)
    shrink_factor: float = Field(gt=0, lt=1, allow_inf_nan=False)
    solver_failure_shrink_factor: float = Field(gt=0, lt=1, allow_inf_nan=False)
    max_retries_per_step: int = Field(ge=0)


class MolarValue(StrictModel):
    value: float = Field(ge=0, allow_inf_nan=False)
    unit: Literal["mol"]


class MolarRateValue(StrictModel):
    value: float = Field(gt=0, allow_inf_nan=False)
    unit: Literal["mol/s"]


class ControlledMineralTolerance(StrictModel):
    name: str = Field(min_length=1)
    absolute_tolerance: MolarValue
    reference_floor: MolarValue


class RichardsonErrorControlConfig(StrictModel):
    temporal_order: float = Field(gt=0, allow_inf_nan=False)
    relative_tolerance: float = Field(ge=0, allow_inf_nan=False)
    negative_amount_tolerance: MolarValue
    controlled_minerals: list[ControlledMineralTolerance] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_scales(self) -> "RichardsonErrorControlConfig":
        names = [item.name for item in self.controlled_minerals]
        if len(names) != len(set(names)):
            raise ValueError("error-control mineral names must be unique")
        for item in self.controlled_minerals:
            if item.absolute_tolerance.value == 0 and (
                self.relative_tolerance == 0 or item.reference_floor.value == 0
            ):
                raise ValueError(
                    f"error-control tolerance scale for {item.name} can become zero"
                )
        return self


class HardMineralExhaustionConfig(StrictModel):
    amount_tolerance: MolarValue
    time_tolerance: TimeValue
    restart_dt: TimeValue
    max_localizations: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_amount_tolerance(self) -> "HardMineralExhaustionConfig":
        if self.amount_tolerance.value <= 0.0:
            raise ValueError("hard exhaustion amount_tolerance must be positive")
        return self


class SoftEventConfig(StrictModel):
    timestep_cap_factor: float = Field(gt=0, lt=1, allow_inf_nan=False)
    saturation_index_crossing: bool
    max_pH_change: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    secondary_mineral_appearance: MolarValue | None = None
    max_reaction_rate_relative_change: float | None = Field(
        default=None, gt=0, allow_inf_nan=False
    )
    reaction_rate_floor: MolarRateValue | None = None

    @model_validator(mode="after")
    def validate_rate_fields(self) -> "SoftEventConfig":
        if (self.max_reaction_rate_relative_change is None) != (
            self.reaction_rate_floor is None
        ):
            raise ValueError(
                "soft reaction-rate events require both threshold and mol/s floor"
            )
        return self


class GeochemicalEventsConfig(StrictModel):
    hard_mineral_exhaustion: HardMineralExhaustionConfig | None
    soft: SoftEventConfig | None


class FixedTimestepConfig(StrictModel):
    mode: Literal["fixed"]
    time: DurationConfig
    step_size: FixedStepSizeConfig
    max_internal_steps: int = Field(default=100_000, gt=0)
    output_schedule: OutputScheduleConfig = Field(default_factory=OutputScheduleConfig)
    checkpoint_schedule: CheckpointScheduleConfig = Field(default_factory=CheckpointScheduleConfig)

    @model_validator(mode="after")
    def validate_year_definition(self) -> "FixedTimestepConfig":
        units = [self.time.duration_unit, self.step_size.dt.unit]
        units.extend(item.unit for item in self.output_schedule.explicit_times)
        if self.output_schedule.logarithmic is not None:
            units.extend(
                [
                    self.output_schedule.logarithmic.start.unit,
                    self.output_schedule.logarithmic.end.unit,
                ]
            )
        units.extend(item.unit for item in self.checkpoint_schedule.times)
        uses_years = any(unit in {"year", "years"} for unit in units)
        if uses_years and self.time.year_definition_days is None:
            raise ValueError("year_definition_days is required when duration or dt uses years")
        if not uses_years and self.time.year_definition_days is not None:
            raise ValueError("year_definition_days is only valid when duration or dt uses years")
        return self


class AdaptiveTimestepConfig(StrictModel):
    mode: Literal["adaptive"]
    time: DurationConfig
    step_size: AdaptiveStepSizeConfig
    max_internal_steps: int = Field(default=100_000, gt=0)
    output_schedule: OutputScheduleConfig
    checkpoint_schedule: CheckpointScheduleConfig = Field(default_factory=CheckpointScheduleConfig)

    @model_validator(mode="after")
    def validate_mode(self) -> "AdaptiveTimestepConfig":
        units = [
            self.time.duration_unit,
            self.step_size.dt_initial.unit,
            self.step_size.dt_min.unit,
            self.step_size.dt_max.unit,
        ]
        units.extend(item.unit for item in self.output_schedule.explicit_times)
        if self.output_schedule.logarithmic is not None:
            units.extend(
                [
                    self.output_schedule.logarithmic.start.unit,
                    self.output_schedule.logarithmic.end.unit,
                ]
            )
        units.extend(item.unit for item in self.checkpoint_schedule.times)
        uses_years = any(unit in {"year", "years"} for unit in units)
        if uses_years and self.time.year_definition_days is None:
            raise ValueError("year_definition_days is required when adaptive times use years")
        if not uses_years and self.time.year_definition_days is not None:
            raise ValueError("year_definition_days is only valid when an adaptive time uses years")
        return self


class AdaptiveErrorControlledTimestepConfig(StrictModel):
    mode: Literal["adaptive_error_controlled"]
    time: DurationConfig
    step_size: ErrorControlledStepSizeConfig
    error_control: RichardsonErrorControlConfig
    events: GeochemicalEventsConfig
    max_internal_steps: int = Field(default=100_000, gt=0)
    output_schedule: OutputScheduleConfig
    checkpoint_schedule: CheckpointScheduleConfig = Field(
        default_factory=CheckpointScheduleConfig
    )

    @model_validator(mode="after")
    def validate_mode(self) -> "AdaptiveErrorControlledTimestepConfig":
        units = [
            self.time.duration_unit,
            self.step_size.dt_initial.unit,
            self.step_size.dt_min.unit,
            self.step_size.dt_max.unit,
        ]
        units.extend(item.unit for item in self.output_schedule.explicit_times)
        if self.output_schedule.logarithmic is not None:
            units.extend(
                [
                    self.output_schedule.logarithmic.start.unit,
                    self.output_schedule.logarithmic.end.unit,
                ]
            )
        units.extend(item.unit for item in self.checkpoint_schedule.times)
        hard = self.events.hard_mineral_exhaustion
        if hard is not None:
            units.extend([hard.time_tolerance.unit, hard.restart_dt.unit])
        uses_years = any(unit in {"year", "years"} for unit in units)
        if uses_years and self.time.year_definition_days is None:
            raise ValueError(
                "year_definition_days is required when error-controlled times use years"
            )
        if not uses_years and self.time.year_definition_days is not None:
            raise ValueError(
                "year_definition_days is only valid when an error-controlled time uses years"
            )
        return self


TimestepConfig = Annotated[
    FixedTimestepConfig | AdaptiveTimestepConfig | AdaptiveErrorControlledTimestepConfig,
    Field(discriminator="mode"),
]


class SolverConfig(StrictModel):
    workflow: SolverWorkflowConfig
    timestep: TimestepConfig

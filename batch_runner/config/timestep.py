"""Solver workflow and timestep configuration models."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from ._base import StrictModel, TimeUnit, WorkflowMode


class SolverWorkflowConfig(StrictModel):
    mode: WorkflowMode
    precondition_kinetics: bool


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


class ElementConservationConfig(StrictModel):
    enabled: bool
    relative_tolerance: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    absolute_tolerance_mol: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_tolerances(self) -> "ElementConservationConfig":
        configured = self.relative_tolerance is not None or self.absolute_tolerance_mol is not None
        if self.enabled and not configured:
            raise ValueError("enabled element_conservation requires a relative or absolute tolerance")
        if not self.enabled and configured:
            raise ValueError("disabled element_conservation forbids tolerances")
        return self


class AmountChangeToleranceConfig(StrictModel):
    absolute_tolerance_mol: float = Field(ge=0, allow_inf_nan=False)
    relative_tolerance: float = Field(ge=0, allow_inf_nan=False)
    reference_floor_mol: float = Field(gt=0, allow_inf_nan=False)


class AdaptiveAcceptanceConfig(StrictModel):
    enabled: bool
    fail_on_non_finite: bool
    negative_amount_tolerance_mol: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    max_delta_pH: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    max_delta_saturation_index: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    selected_species_change: AmountChangeToleranceConfig | None = None
    mineral_change: AmountChangeToleranceConfig | None = None
    element_conservation: ElementConservationConfig
    max_relative_rate_change: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_checks(self) -> "AdaptiveAcceptanceConfig":
        if not self.enabled:
            raise ValueError("adaptive timestep acceptance must be enabled")
        if self.max_relative_rate_change is not None:
            raise ValueError("rate-based adaptive acceptance is not verified and must remain null")
        checks = (
            self.fail_on_non_finite,
            self.negative_amount_tolerance_mol is not None,
            self.max_delta_pH is not None,
            self.max_delta_saturation_index is not None,
            self.selected_species_change is not None,
            self.mineral_change is not None,
            self.element_conservation.enabled,
        )
        if not any(checks):
            raise ValueError("adaptive acceptance requires at least one configured state check")
        return self


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
    mode: Literal["adaptive", "adaptive_long_horizon"]
    time: DurationConfig
    step_size: AdaptiveStepSizeConfig
    acceptance: AdaptiveAcceptanceConfig
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
        if self.mode == "adaptive_long_horizon":
            if self.output_schedule.mode == "every_internal_step":
                raise ValueError("adaptive_long_horizon requires an explicit, logarithmic, or hybrid output schedule")
            if not self.output_schedule.include_final:
                raise ValueError("adaptive_long_horizon requires include_final: true")
            if not self.checkpoint_schedule.enabled:
                raise ValueError("adaptive_long_horizon requires checkpoint_schedule.enabled: true")
        return self


class RestartConfig(StrictModel):
    enabled: bool = False
    from_checkpoint: str | None = None

    @model_validator(mode="after")
    def validate_restart(self) -> "RestartConfig":
        if self.enabled:
            raise ValueError("automatic restart is not implemented or validated")
        if self.from_checkpoint is not None:
            raise ValueError("disabled restart forbids from_checkpoint")
        return self


TimestepConfig = Annotated[
    FixedTimestepConfig | AdaptiveTimestepConfig,
    Field(discriminator="mode"),
]


class SolverConfig(StrictModel):
    workflow: SolverWorkflowConfig
    timestep: TimestepConfig
    restart: RestartConfig = Field(default_factory=RestartConfig)

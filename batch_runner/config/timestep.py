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
                raise ValueError(
                    "every_internal_step output schedule forbids explicit and logarithmic fields"
                )
        elif self.mode == "explicit":
            if self.logarithmic is not None:
                raise ValueError("explicit output schedule forbids logarithmic")
        elif self.mode == "logarithmic":
            if self.explicit_times or self.logarithmic is None:
                raise ValueError(
                    "logarithmic output schedule requires logarithmic and forbids explicit_times"
                )
        elif not self.explicit_times or self.logarithmic is None:
            raise ValueError(
                "hybrid output schedule requires explicit_times and logarithmic"
            )
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
    year_definition_days: float | None = Field(
        default=None, gt=0, allow_inf_nan=False
    )


class FixedStepSizeConfig(StrictModel):
    dt: TimeValue


class AdaptiveErrorControlConfig(StrictModel):
    """Optional Richardson error estimator plus I/PI timestep controller.

    ``temporal_order`` is intentionally not defaulted when the feature is enabled:
    the effective order must be demonstrated for the Reaktoro workflow rather than
    assumed from a generic integrator description.
    """

    enabled: bool = False
    temporal_order: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    relative_tolerance: float = Field(default=1.0e-3, ge=0, allow_inf_nan=False)
    species_absolute_tolerance_mol: float = Field(
        default=1.0e-12, gt=0, allow_inf_nan=False
    )
    mineral_absolute_tolerance_mol: float = Field(
        default=1.0e-12, gt=0, allow_inf_nan=False
    )
    controlled_species: list[str] = Field(default_factory=list)
    controlled_minerals: list[str] = Field(default_factory=list)
    safety_factor: float = Field(default=0.8, gt=0, lt=1, allow_inf_nan=False)
    startup_normalized_gain: float = Field(
        default=0.7, gt=0, allow_inf_nan=False
    )
    pi_normalized_integral_gain: float = Field(
        default=0.3, gt=0, allow_inf_nan=False
    )
    pi_normalized_proportional_gain: float = Field(
        default=0.4, ge=0, allow_inf_nan=False
    )
    max_growth_factor: float = Field(default=2.0, gt=1, allow_inf_nan=False)
    min_reduction_factor: float = Field(
        default=0.1, gt=0, lt=1, allow_inf_nan=False
    )
    restart_factor: float = Field(default=0.33, gt=0, lt=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_enabled(self) -> "AdaptiveErrorControlConfig":
        if self.enabled and self.temporal_order is None:
            raise ValueError(
                "enabled adaptive error_control requires temporal_order established "
                "by timestep-refinement validation"
            )
        if not self.enabled and self.temporal_order is not None:
            raise ValueError(
                "temporal_order is only valid when adaptive error_control is enabled"
            )
        return self


class AdaptiveEventControlConfig(StrictModel):
    """Predictor/corrector zero-crossing limits for abrupt geochemical events."""

    enabled: bool = False
    minerals: list[str] = Field(default_factory=list)
    mineral_exhaustion: bool = True
    saturation_crossing: bool = True
    mineral_amount_event_tolerance_mol: float = Field(
        default=1.0e-14, ge=0, allow_inf_nan=False
    )
    saturation_index_event_tolerance: float = Field(
        default=1.0e-8, ge=0, allow_inf_nan=False
    )

    @model_validator(mode="after")
    def validate_enabled(self) -> "AdaptiveEventControlConfig":
        if self.enabled and not (self.mineral_exhaustion or self.saturation_crossing):
            raise ValueError(
                "enabled adaptive event_control requires mineral_exhaustion and/or "
                "saturation_crossing"
            )
        return self


class AdaptiveStepSizeConfig(StrictModel):
    dt_initial: TimeValue
    dt_min: TimeValue
    dt_max: TimeValue
    growth_factor: float = Field(gt=1, allow_inf_nan=False)
    shrink_factor: float = Field(gt=0, lt=1, allow_inf_nan=False)
    max_retries_per_step: int = Field(ge=0)


class FixedTimestepConfig(StrictModel):
    mode: Literal["fixed"]
    time: DurationConfig
    step_size: FixedStepSizeConfig
    max_internal_steps: int = Field(default=100_000, gt=0)
    output_schedule: OutputScheduleConfig = Field(default_factory=OutputScheduleConfig)
    checkpoint_schedule: CheckpointScheduleConfig = Field(
        default_factory=CheckpointScheduleConfig
    )

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
            raise ValueError(
                "year_definition_days is required when duration or dt uses years"
            )
        if not uses_years and self.time.year_definition_days is not None:
            raise ValueError(
                "year_definition_days is only valid when duration or dt uses years"
            )
        return self


class AdaptiveTimestepConfig(StrictModel):
    mode: Literal["adaptive"]
    time: DurationConfig
    step_size: AdaptiveStepSizeConfig
    max_internal_steps: int = Field(default=100_000, gt=0)
    output_schedule: OutputScheduleConfig
    checkpoint_schedule: CheckpointScheduleConfig = Field(
        default_factory=CheckpointScheduleConfig
    )
    error_control: AdaptiveErrorControlConfig = Field(
        default_factory=AdaptiveErrorControlConfig
    )
    event_control: AdaptiveEventControlConfig = Field(
        default_factory=AdaptiveEventControlConfig
    )

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
            raise ValueError(
                "year_definition_days is required when adaptive times use years"
            )
        if not uses_years and self.time.year_definition_days is not None:
            raise ValueError(
                "year_definition_days is only valid when an adaptive time uses years"
            )
        if self.event_control.enabled and not self.error_control.enabled:
            raise ValueError(
                "adaptive event_control requires error_control so event restrictions "
                "augment, rather than replace, temporal-error control"
            )
        return self


TimestepConfig = Annotated[
    FixedTimestepConfig | AdaptiveTimestepConfig,
    Field(discriminator="mode"),
]


class SolverConfig(StrictModel):
    workflow: SolverWorkflowConfig
    timestep: TimestepConfig

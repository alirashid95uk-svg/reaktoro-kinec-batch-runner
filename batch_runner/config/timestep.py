"""Define solver-workflow and timestep source configuration.

The simulator dispatches from these discriminated timestep modes.  This module
owns user-visible time units, schedules, controller parameters, Richardson
error tolerances, and event thresholds; resolution converts them to canonical
seconds and checks schedule feasibility before any Reaktoro solve.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from ._base import StrictModel, TimeUnit, WorkflowMode


class SolverWorkflowConfig(StrictModel):
    """Select the implemented equilibrium/kinetic constraint sequence."""

    mode: WorkflowMode = Field(
        description="Workflow that determines equilibrium, kinetics, CO2, and redox staging."
    )


class TimeValue(StrictModel):
    """A positive finite duration converted to canonical seconds during resolution."""

    value: float = Field(gt=0, allow_inf_nan=False, description="Positive finite time value.")
    unit: TimeUnit = Field(description="Supported unit for the associated time value.")


class LogarithmicScheduleConfig(StrictModel):
    """Define logarithmically spaced scientific output targets."""

    start: TimeValue = Field(description="Positive first logarithmic output time.")
    end: TimeValue = Field(description="Positive final logarithmic output time.")
    points_per_decade: int = Field(
        gt=0,
        description="Positive number of logarithmic intervals generated per time decade.",
    )


class OutputScheduleConfig(StrictModel):
    """Select accepted-state times written to the scientific timeseries.

    Boundary flags affect output only, never the final solver target.  Explicit
    and logarithmic times are validated and canonicalized during resolution.
    """

    mode: Literal["every_internal_step", "explicit", "logarithmic", "hybrid"] = (
        Field(
            default="every_internal_step",
            description="Schedule source: internal steps, explicit times, logarithmic times, or both.",
        )
    )
    include_initial: bool = Field(
        default=True,
        description="Include the accepted initial state at time zero in the timeseries.",
    )
    include_final: bool = Field(
        default=True,
        description="Include the accepted state at the configured final time in the timeseries.",
    )
    explicit_times: list[TimeValue] = Field(
        default_factory=list,
        description="Explicit positive output targets used by explicit and hybrid schedules.",
    )
    logarithmic: LogarithmicScheduleConfig | None = Field(
        default=None,
        description="Logarithmic target definition required by logarithmic and hybrid schedules.",
    )

    @model_validator(mode="after")
    def validate_mode_fields(self) -> "OutputScheduleConfig":
        """Internal forbids targets; explicit, logarithmic, and hybrid admit only their inputs."""

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
    """Select accepted states to serialize as checkpoints.

    Checkpoints are independent of timeseries output and do not provide restart
    capability.
    """

    enabled: bool = Field(default=False, description="Write checkpoints at configured times.")
    times: list[TimeValue] = Field(
        default_factory=list,
        description="Positive checkpoint targets; required when enabled and forbidden when disabled.",
    )

    @model_validator(mode="after")
    def validate_enabled(self) -> "CheckpointScheduleConfig":
        """Enabled checkpoints require times; disabled checkpoints forbid them."""

        if self.enabled and not self.times:
            raise ValueError("enabled checkpoint_schedule requires times")
        if not self.enabled and self.times:
            raise ValueError("disabled checkpoint_schedule forbids times")
        return self


class DurationConfig(StrictModel):
    """Define simulation duration and any explicit conversion for year units."""

    duration_value: float = Field(
        gt=0,
        allow_inf_nan=False,
        description="Positive finite simulation duration.",
    )
    duration_unit: TimeUnit = Field(description="Unit of the simulation duration.")
    year_definition_days: float | None = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
        description=(
            "Positive days per year, required when any timestep field uses year/years "
            "and forbidden otherwise."
        ),
    )


class FixedStepSizeConfig(StrictModel):
    """Define the nominal step size for fixed-grid integration."""

    dt: TimeValue = Field(description="Positive fixed internal timestep.")


class AdaptiveStepSizeConfig(StrictModel):
    """Configure the legacy solver-feasibility adaptive controller."""

    dt_initial: TimeValue = Field(description="Initial proposed timestep.")
    dt_min: TimeValue = Field(description="Minimum ordinary adaptive timestep.")
    dt_max: TimeValue = Field(description="Maximum proposed adaptive timestep.")
    growth_factor: float = Field(
        gt=1,
        allow_inf_nan=False,
        description="Factor applied after a successful Reaktoro solve.",
    )
    shrink_factor: float = Field(
        gt=0,
        lt=1,
        allow_inf_nan=False,
        description="Factor applied after a failed Reaktoro solve.",
    )
    max_retries_per_step: int = Field(
        ge=0,
        description="Maximum failed-solve retries allowed at one accepted time.",
    )


class ErrorControlledStepSizeConfig(StrictModel):
    """Configure Richardson error control and distinct solver-failure recovery."""

    dt_initial: TimeValue = Field(description="Initial proposed timestep.")
    dt_min: TimeValue = Field(description="Minimum ordinary error-controlled timestep.")
    dt_max: TimeValue = Field(description="Maximum proposed error-controlled timestep.")
    safety_factor: float = Field(
        gt=0,
        lt=1,
        allow_inf_nan=False,
        description="Safety multiplier in accepted-step Richardson timestep proposals.",
    )
    growth_factor: float = Field(
        gt=1,
        allow_inf_nan=False,
        description="Maximum factor by which an accepted proposal may grow.",
    )
    shrink_factor: float = Field(
        gt=0,
        lt=1,
        allow_inf_nan=False,
        description="Fallback shrink factor for temporal-error rejection.",
    )
    solver_failure_shrink_factor: float = Field(
        gt=0,
        lt=1,
        allow_inf_nan=False,
        description="Separate shrink factor applied after a failed Reaktoro solve.",
    )
    max_retries_per_step: int = Field(
        ge=0,
        description="Maximum rejected trials allowed at one accepted time.",
    )


class MolarValue(StrictModel):
    """A finite non-negative amount used only by error-control diagnostics."""

    value: float = Field(
        ge=0,
        allow_inf_nan=False,
        description="Finite non-negative amount in mol.",
    )
    unit: Literal["mol"] = Field(description="Required molar amount unit.")


class MolarRateValue(StrictModel):
    """A finite positive molar-rate floor for relative event comparisons."""

    value: float = Field(
        gt=0,
        allow_inf_nan=False,
        description="Finite positive reaction-rate floor in mol/s.",
    )
    unit: Literal["mol/s"] = Field(description="Required molar-rate unit.")


class ControlledMineralTolerance(StrictModel):
    """Define the Richardson error scale for one kinetic mineral amount."""

    name: str = Field(min_length=1, description="Controlled kinetic mineral name.")
    absolute_tolerance: MolarValue = Field(
        description="Absolute mineral-amount error tolerance in mol."
    )
    reference_floor: MolarValue = Field(
        description="Non-negative amount floor used in the relative error scale."
    )


class RichardsonErrorControlConfig(StrictModel):
    """Configure step-doubling LTE estimates for kinetic mineral amounts.

    ``temporal_order`` is an explicit estimator assumption, not a claim of
    measured convergence.  The configured mineral set must exactly match the
    case's kinetic minerals; :class:`case.CaseConfig` enforces that relation.
    """

    temporal_order: float = Field(
        gt=0,
        allow_inf_nan=False,
        description="Assumed positive temporal order used in the Richardson denominator.",
    )
    relative_tolerance: float = Field(
        ge=0,
        allow_inf_nan=False,
        description="Non-negative dimensionless relative mineral-amount tolerance.",
    )
    negative_amount_tolerance: MolarValue = Field(
        description="Admissibility tolerance for small negative trial mineral amounts."
    )
    controlled_minerals: list[ControlledMineralTolerance] = Field(
        min_length=1,
        description="Unique tolerance definitions, one for every kinetic mineral.",
    )

    @model_validator(mode="after")
    def validate_scales(self) -> "RichardsonErrorControlConfig":
        """Controlled names must be unique and every tolerance scale positive at zero amount."""

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
    """Configure hard localisation of a kinetic mineral exhaustion event."""

    amount_tolerance: MolarValue = Field(
        description="Strictly positive mineral amount tolerance in mol."
    )
    time_tolerance: TimeValue = Field(
        description="Positive time-width tolerance for event localisation."
    )
    restart_dt: TimeValue = Field(
        description="Positive controller timestep used immediately after the event."
    )
    max_localizations: int = Field(
        ge=1,
        description="Maximum localisation trials for one exhaustion event."
    )

    @model_validator(mode="after")
    def validate_amount_tolerance(self) -> "HardMineralExhaustionConfig":
        """Hard exhaustion requires ``amount_tolerance`` strictly greater than zero mol."""

        if self.amount_tolerance.value <= 0.0:
            raise ValueError("hard exhaustion amount_tolerance must be positive")
        return self


class SoftEventConfig(StrictModel):
    """Configure geochemical indicators that cap only the next timestep proposal."""

    timestep_cap_factor: float = Field(
        gt=0,
        lt=1,
        allow_inf_nan=False,
        description="Factor that caps the proposal following a detected soft event."
    )
    saturation_index_crossing: bool = Field(
        description="Detect sign crossings of requested mineral saturation indices."
    )
    max_pH_change: float | None = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
        description="Optional positive maximum absolute pH change across an accepted step."
    )
    secondary_mineral_appearance: MolarValue | None = Field(
        default=None,
        description="Optional mineral-amount threshold in mol for secondary appearance."
    )
    max_reaction_rate_relative_change: float | None = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
        description=(
            "Optional positive relative reaction-rate change threshold; requires "
            "reaction_rate_floor."
        ),
    )
    reaction_rate_floor: MolarRateValue | None = Field(
        default=None,
        description=(
            "Positive mol/s floor paired with max_reaction_rate_relative_change "
            "to stabilize relative comparisons."
        ),
    )

    @model_validator(mode="after")
    def validate_rate_fields(self) -> "SoftEventConfig":
        """Reaction-rate events require both relative threshold and mol/s floor, or neither."""

        if (self.max_reaction_rate_relative_change is None) != (
            self.reaction_rate_floor is None
        ):
            raise ValueError(
                "soft reaction-rate events require both threshold and mol/s floor"
            )
        return self


class GeochemicalEventsConfig(StrictModel):
    """Select explicit hard and soft event policies with no hidden thresholds."""

    hard_mineral_exhaustion: HardMineralExhaustionConfig | None = Field(
        description="Hard exhaustion localisation policy, or null to disable it."
    )
    soft: SoftEventConfig | None = Field(
        description="Soft next-step proposal caps, or null to disable them."
    )


class FixedTimestepConfig(StrictModel):
    """Configure deterministic fixed-step integration and scheduled state landing."""

    mode: Literal["fixed"] = Field(description="Select fixed-timestep integration.")
    time: DurationConfig = Field(description="Total simulation duration and year definition.")
    step_size: FixedStepSizeConfig = Field(description="Fixed internal timestep.")
    max_internal_steps: int = Field(
        default=100_000,
        gt=0,
        description="Positive safety limit on internal solver steps."
    )
    output_schedule: OutputScheduleConfig = Field(
        default_factory=OutputScheduleConfig,
        description="Accepted-state scientific output schedule."
    )
    checkpoint_schedule: CheckpointScheduleConfig = Field(
        default_factory=CheckpointScheduleConfig,
        description="Accepted-state checkpoint schedule; restart is not supported."
    )

    @model_validator(mode="after")
    def validate_year_definition(self) -> "FixedTimestepConfig":
        """Any year-valued time requires ``year_definition_days``; other units forbid it."""

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
    """Configure the legacy solver-feasibility adaptive controller.

    Successful Reaktoro solves are accepted and grow the next proposal; failed
    solves roll back to the accepted state, shrink, and retry.  This mode does
    not estimate temporal error.
    """

    mode: Literal["adaptive"] = Field(
        description="Select the legacy solver-feasibility adaptive controller."
    )
    time: DurationConfig = Field(description="Total simulation duration and year definition.")
    step_size: AdaptiveStepSizeConfig = Field(
        description="Legacy adaptive proposal and retry controls."
    )
    max_internal_steps: int = Field(
        default=100_000,
        gt=0,
        description="Positive safety limit on accepted internal steps."
    )
    output_schedule: OutputScheduleConfig = Field(
        description="Required accepted-state scientific output schedule."
    )
    checkpoint_schedule: CheckpointScheduleConfig = Field(
        default_factory=CheckpointScheduleConfig,
        description="Accepted-state checkpoint schedule; restart is not supported."
    )

    @model_validator(mode="after")
    def validate_year_definition(self) -> "AdaptiveTimestepConfig":
        """Any year-valued time requires ``year_definition_days``; other units forbid it."""

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
    """Configure Richardson step-doubling with explicit geochemical event policies."""

    mode: Literal["adaptive_error_controlled"] = Field(
        description="Select Richardson error-controlled adaptive integration."
    )
    time: DurationConfig = Field(description="Total simulation duration and year definition.")
    step_size: ErrorControlledStepSizeConfig = Field(
        description="Error-controlled proposal and retry parameters."
    )
    error_control: RichardsonErrorControlConfig = Field(
        description="Mineral-amount Richardson estimator and tolerance scales."
    )
    events: GeochemicalEventsConfig = Field(
        description="Explicit hard and soft geochemical event policies."
    )
    max_internal_steps: int = Field(
        default=100_000,
        gt=0,
        description="Positive safety limit on accepted internal steps."
    )
    output_schedule: OutputScheduleConfig = Field(
        description="Required accepted-state scientific output schedule."
    )
    checkpoint_schedule: CheckpointScheduleConfig = Field(
        default_factory=CheckpointScheduleConfig,
        description="Accepted-state checkpoint schedule; restart is not supported."
    )

    @model_validator(mode="after")
    def validate_year_definition(self) -> "AdaptiveErrorControlledTimestepConfig":
        """Any year-valued time requires ``year_definition_days``; other units forbid it."""

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
    """Bind the scientific workflow to one strict discriminated timestep mode."""

    workflow: SolverWorkflowConfig = Field(description="Equilibrium and kinetic stage workflow.")
    timestep: TimestepConfig = Field(
        description="Fixed, solver-feasibility adaptive, or Richardson error-controlled settings."
    )

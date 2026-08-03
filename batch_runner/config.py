"""Strict YAML loading, validation, path resolution, and preprocessing."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR, localcontext
from heapq import merge
from math import isfinite
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TimeUnit = Literal[
    "second",
    "seconds",
    "minute",
    "minutes",
    "hour",
    "hours",
    "day",
    "days",
    "year",
    "years",
]
WorkflowMode = Literal[
    "equilibrium_only",
    "closed_kinetics",
    "fixed_fugacity_initial_equilibrium_then_closed_kinetics",
    "fixed_fugacity_during_kinetic_steps",
]
KineticModel = Literal["palandri_kharaka", "kinec"]
DEFAULT_KINETIC_PATHS = {
    "palandri_kharaka": "data/kinetics/PalandriKharaka_local.yaml",
    "kinec": "data/kinetics/kinec_rates_minimal.yaml",
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Amount(StrictModel):
    value: float = Field(ge=0)
    unit: str = Field(min_length=1)


class SurfaceArea(StrictModel):
    value: float = Field(gt=0)
    unit: str = Field(min_length=1)


class CaseInfo(StrictModel):
    name: str = Field(min_length=1)


class PathsConfig(StrictModel):
    output_dir: str = Field(min_length=1)


class DatabaseConfig(StrictModel):
    source: Literal["embedded", "local"]
    name: str | None = None
    path: str | None = None

    @model_validator(mode="after")
    def validate_source_fields(self) -> "DatabaseConfig":
        if self.source == "embedded":
            if not self.name or self.path is not None:
                raise ValueError("embedded database requires name and forbids path")
        elif not self.path or self.name is not None:
            raise ValueError("local database requires path and forbids name")
        return self


class ActivityModelsConfig(StrictModel):
    aqueous: Literal["phreeqc"]
    gas: Literal["peng_robinson_phreeqc"] | None = None


class PhysicalConfig(StrictModel):
    temperature_c: float
    pressure_bar: float = Field(gt=0)


class BrineConfig(StrictModel):
    aqueous_elements: list[str] = Field(min_length=1)
    species_amounts: dict[str, Amount] = Field(min_length=1)


class Co2Config(StrictModel):
    mode: Literal["disabled", "finite", "fixed_fugacity"]
    gas_species: str | None = None
    initial_amount: Amount | None = None
    fugacity_bar: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_mode(self) -> "Co2Config":
        if self.mode == "disabled":
            if self.gas_species is not None or self.initial_amount is not None or self.fugacity_bar is not None:
                raise ValueError("disabled CO2 forbids gas_species, initial_amount, and fugacity_bar")
        elif self.mode == "finite":
            if not self.gas_species or self.initial_amount is None or self.fugacity_bar is not None:
                raise ValueError("finite CO2 requires gas_species and initial_amount, and forbids fugacity_bar")
        elif not self.gas_species or self.fugacity_bar is None or self.initial_amount is not None:
            raise ValueError(
                "fixed_fugacity CO2 requires gas_species and fugacity_bar, and forbids initial_amount"
            )
        return self


class RedoxConfig(StrictModel):
    enabled: bool
    pe: float | None = None
    apply_during: Literal["initial_equilibrium_only", "kinetic_steps"] | None = None

    @model_validator(mode="after")
    def validate_redox(self) -> "RedoxConfig":
        if self.enabled and (self.pe is None or self.apply_during is None):
            raise ValueError("enabled redox requires pe and apply_during")
        if not self.enabled and (self.pe is not None or self.apply_during is not None):
            raise ValueError("disabled redox forbids pe and apply_during")
        return self


class KineticsConfig(StrictModel):
    enabled: bool
    model: KineticModel | None = None
    path: str | None = None

    @model_validator(mode="after")
    def validate_kinetics(self) -> "KineticsConfig":
        if self.enabled:
            self.model = self.model or "palandri_kharaka"
            self.path = self.path or DEFAULT_KINETIC_PATHS[self.model]
        elif self.model is not None or self.path is not None:
            raise ValueError("disabled kinetics forbids model and path")
        return self


class MineralConfig(StrictModel):
    name: str = Field(min_length=1)
    role: Literal["equilibrium", "kinetic"]
    initial_amount: Amount | None = None
    surface_area: SurfaceArea | None = None
    surface_area_basis: str | None = Field(default=None, min_length=1)
    surface_area_provenance: str | None = Field(default=None, min_length=1)
    selection_reason: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_role(self) -> "MineralConfig":
        if self.role == "kinetic" and (self.initial_amount is None or self.surface_area is None):
            raise ValueError("kinetic mineral requires initial_amount and surface_area")
        if self.role == "equilibrium" and self.surface_area is not None:
            raise ValueError("equilibrium minerals must not define surface_area")
        return self


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


class PostprocessingConfig(StrictModel):
    requested_species: list[str]
    requested_minerals: list[str]
    aqueous_molalities: bool
    saturation_indices: bool
    reaction_rates: bool
    element_budget: "ElementBudgetConfig"
    carbon_inventory: "CarbonInventoryConfig"
    mineral_volume_change: "MineralVolumeChangeConfig"
    regime_classification: "EnabledConfig"
    surface_area_audit: "EnabledConfig"
    workflow_comparison: "EnabledConfig"
    secondary_mineral_assemblage: "EnabledConfig"
    surrogate_dataset: "SurrogateDatasetConfig"
    porosity_permeability: "PorosityPermeabilityConfig"


class EnabledConfig(StrictModel):
    enabled: bool


class ElementBudgetConfig(StrictModel):
    enabled: bool
    elements: list[str]
    species: dict[str, dict[str, float]]
    minerals: dict[str, dict[str, float]]
    gas_species: dict[str, dict[str, float]]

    @model_validator(mode="after")
    def validate_budget(self) -> "ElementBudgetConfig":
        if not self.enabled:
            if self.elements or self.species or self.minerals or self.gas_species:
                raise ValueError("disabled element_budget forbids elements and stoichiometry mappings")
            return self
        if not self.elements:
            raise ValueError("enabled element_budget requires elements")
        if not (self.species or self.minerals or self.gas_species):
            raise ValueError("enabled element_budget requires at least one stoichiometry mapping")
        allowed = set(self.elements)
        for group_name, group in (
            ("species", self.species),
            ("minerals", self.minerals),
            ("gas_species", self.gas_species),
        ):
            for item, stoichiometry in group.items():
                missing = set(stoichiometry).difference(allowed)
                if missing:
                    raise ValueError(
                        f"element_budget {group_name} mapping for {item} uses unconfigured elements: "
                        + ", ".join(sorted(missing))
                    )
                if any(value < 0 for value in stoichiometry.values()):
                    raise ValueError(f"element_budget {group_name} mapping for {item} has negative coefficient")
        return self


class CarbonInventoryConfig(StrictModel):
    enabled: bool
    carbon_species: dict[str, float]
    carbon_minerals: dict[str, float]
    carbon_gas_species: dict[str, float]

    @model_validator(mode="after")
    def validate_inventory(self) -> "CarbonInventoryConfig":
        if not self.enabled:
            if self.carbon_species or self.carbon_minerals or self.carbon_gas_species:
                raise ValueError("disabled carbon_inventory forbids carbon mappings")
            return self
        if not (self.carbon_species or self.carbon_minerals or self.carbon_gas_species):
            raise ValueError("enabled carbon_inventory requires at least one carbon mapping")
        for group in (self.carbon_species, self.carbon_minerals, self.carbon_gas_species):
            if any(value < 0 for value in group.values()):
                raise ValueError("carbon_inventory coefficients must be non-negative")
        return self


class MineralVolumeChangeConfig(StrictModel):
    enabled: bool
    molar_volumes_cm3_per_mol: dict[str, float]
    sources: dict[str, str]

    @model_validator(mode="after")
    def validate_volume_sources(self) -> "MineralVolumeChangeConfig":
        if not self.enabled:
            if self.molar_volumes_cm3_per_mol or self.sources:
                raise ValueError("disabled mineral_volume_change forbids molar volumes and sources")
            return self
        if any(value <= 0 for value in self.molar_volumes_cm3_per_mol.values()):
            raise ValueError("mineral molar volumes must be positive")
        return self


class SurrogateDatasetConfig(StrictModel):
    enabled: bool
    validity_domain: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_domain(self) -> "SurrogateDatasetConfig":
        if self.enabled and not self.validity_domain:
            raise ValueError("enabled surrogate_dataset requires validity_domain")
        if not self.enabled and self.validity_domain is not None:
            raise ValueError("disabled surrogate_dataset forbids validity_domain")
        return self


class PorosityPermeabilityConfig(StrictModel):
    enabled: bool
    bulk_volume_cm3: float | None = Field(default=None, gt=0)
    permeability_update_law: str | None = Field(default=None, min_length=1)
    capillary_entry_pressure_law: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_laws(self) -> "PorosityPermeabilityConfig":
        if not self.enabled and (
            self.bulk_volume_cm3 is not None
            or self.permeability_update_law is not None
            or self.capillary_entry_pressure_law is not None
        ):
            raise ValueError("disabled porosity_permeability forbids volume and update laws")
        if self.permeability_update_law is not None or self.capillary_entry_pressure_law is not None:
            raise ValueError("permeability and capillary-entry-pressure update laws are not implemented")
        return self


class ValidationTarget(StrictModel):
    quantity: str = Field(min_length=1)
    target_value: float
    unit: str = Field(min_length=1)
    uncertainty: float | None = Field(default=None, ge=0)
    source: str = Field(min_length=1)


class ValidationConfig(StrictModel):
    enabled: bool
    targets: list[ValidationTarget]

    @model_validator(mode="after")
    def validate_targets(self) -> "ValidationConfig":
        if self.enabled and not self.targets:
            raise ValueError("enabled validation requires targets")
        if not self.enabled and self.targets:
            raise ValueError("disabled validation forbids targets")
        return self


class ManifestOutputConfig(StrictModel):
    enabled: bool
    include_input_snapshot: bool


class DiagnosticsOutputConfig(StrictModel):
    enabled: bool


class TimeseriesOutputConfig(StrictModel):
    enabled: bool
    include_species_amounts: bool
    include_species_molalities: bool
    include_mineral_amounts: bool
    include_mineral_deltas: bool
    include_saturation_indices: bool
    include_solver_columns: bool


class SummaryOutputsConfig(StrictModel):
    mineral_summary: bool
    aqueous_summary: bool
    reaction_rates: bool
    reaction_rate_validation: bool
    carbon_inventory: bool
    element_budget: bool
    mineral_volume_change: bool
    regime_classification: bool
    surface_area_audit: bool
    workflow_comparison: bool
    secondary_mineral_assemblage: bool
    surrogate_dataset: bool
    validation_ledger: bool
    porosity_permeability: bool


class SolverHistoryOutputConfig(StrictModel):
    enabled: bool


class PlotOutputsConfig(StrictModel):
    enabled: bool
    pH: bool
    mineral_change: bool
    saturation_index: bool
    solver_dt: bool
    solver_iterations: bool

    @model_validator(mode="after")
    def validate_plots(self) -> "PlotOutputsConfig":
        flags = (
            self.pH,
            self.mineral_change,
            self.saturation_index,
            self.solver_dt,
            self.solver_iterations,
        )
        if self.enabled and not any(flags):
            raise ValueError("enabled plots require at least one plot flag")
        return self


class DebugOutputsConfig(StrictModel):
    enabled: bool
    mineral_connection: bool
    resolved_config: bool
    final_state: bool


class OutputsConfig(StrictModel):
    manifest: ManifestOutputConfig
    diagnostics: DiagnosticsOutputConfig
    timeseries: TimeseriesOutputConfig
    summaries: SummaryOutputsConfig
    solver_history: SolverHistoryOutputConfig
    plots: PlotOutputsConfig
    debug: DebugOutputsConfig


class CaseConfig(StrictModel):
    case: CaseInfo
    paths: PathsConfig
    database: DatabaseConfig
    activity_models: ActivityModelsConfig
    physical: PhysicalConfig
    brine: BrineConfig
    co2: Co2Config
    redox: RedoxConfig
    kinetics: KineticsConfig
    minerals: list[MineralConfig] = Field(min_length=1)
    solver: SolverConfig
    postprocessing: PostprocessingConfig
    validation: ValidationConfig
    outputs: OutputsConfig

    @model_validator(mode="after")
    def validate_case_consistency(self) -> "CaseConfig":
        names = [mineral.name for mineral in self.minerals]
        if len(names) != len(set(names)):
            raise ValueError("mineral names must be unique")

        kinetic_count = sum(mineral.role == "kinetic" for mineral in self.minerals)
        workflow = self.solver.workflow
        if self.kinetics.enabled and kinetic_count == 0:
            raise ValueError("enabled kinetics requires at least one kinetic mineral")
        if not self.kinetics.enabled and kinetic_count:
            raise ValueError("kinetic minerals require kinetics.enabled: true")
        if workflow.mode == "equilibrium_only" and self.kinetics.enabled:
            raise ValueError("equilibrium_only requires kinetics.enabled: false")
        if workflow.mode != "equilibrium_only" and not self.kinetics.enabled:
            raise ValueError(f"{workflow.mode} requires kinetics.enabled: true")
        if not self.kinetics.enabled and workflow.precondition_kinetics:
            raise ValueError("precondition_kinetics must be false when kinetics are disabled")
        timestep = self.solver.timestep
        if not self.kinetics.enabled and timestep.mode != "fixed":
            raise ValueError("adaptive timestep modes require kinetics.enabled: true")
        if isinstance(timestep, AdaptiveTimestepConfig):
            acceptance = timestep.acceptance
            if (
                acceptance.selected_species_change is not None
                and not self.postprocessing.requested_species
            ):
                raise ValueError("selected-species acceptance requires requested_species")
            if (
                acceptance.element_conservation.enabled
                and workflow.mode == "fixed_fugacity_during_kinetic_steps"
            ):
                raise ValueError(
                    "element-conservation acceptance is not valid with fixed-fugacity kinetic steps"
                )

        fixed_fugacity_workflows = {
            "fixed_fugacity_initial_equilibrium_then_closed_kinetics",
            "fixed_fugacity_during_kinetic_steps",
        }
        if workflow.mode in fixed_fugacity_workflows and self.co2.mode != "fixed_fugacity":
            raise ValueError(f"{workflow.mode} requires co2.mode: fixed_fugacity")
        if self.co2.mode == "fixed_fugacity" and workflow.mode == "closed_kinetics":
            raise ValueError(
                "fixed_fugacity CO2 with kinetics requires an explicit fixed-fugacity workflow"
            )
        if self.co2.mode == "finite" and self.activity_models.gas is None:
            raise ValueError("finite CO2 requires an explicit gas activity model")

        if self.redox.enabled and self.redox.apply_during == "kinetic_steps":
            if workflow.mode != "fixed_fugacity_during_kinetic_steps":
                raise ValueError(
                    "redox.apply_during: kinetic_steps requires a constrained kinetic workflow"
                )

        requested_minerals = set(self.postprocessing.requested_minerals)
        missing_requested = requested_minerals.difference(names)
        if missing_requested:
            raise ValueError(
                "postprocessing requested_minerals are not configured minerals: "
                + ", ".join(sorted(missing_requested))
            )
        if _species_outputs_enabled(self.outputs) and not self.postprocessing.requested_species:
            raise ValueError("enabled species outputs require postprocessing.requested_species")
        if _mineral_outputs_enabled(self.outputs) and not self.postprocessing.requested_minerals:
            raise ValueError("enabled mineral outputs require postprocessing.requested_minerals")

        if self.outputs.plots.solver_dt and not self.outputs.solver_history.enabled:
            raise ValueError("solver_dt plot requires solver_history output")
        if self.outputs.plots.solver_iterations and not self.outputs.solver_history.enabled:
            raise ValueError("solver_iterations plot requires solver_history output")
        summaries = self.outputs.summaries
        post = self.postprocessing
        if summaries.reaction_rates and not post.reaction_rates:
            raise ValueError("reaction_rates output requires postprocessing.reaction_rates: true")
        if summaries.reaction_rate_validation and not post.reaction_rates:
            raise ValueError(
                "reaction_rate_validation output requires postprocessing.reaction_rates: true"
            )
        if summaries.carbon_inventory and not post.carbon_inventory.enabled:
            raise ValueError("carbon_inventory output requires postprocessing.carbon_inventory.enabled: true")
        if summaries.element_budget and not post.element_budget.enabled:
            raise ValueError("element_budget output requires postprocessing.element_budget.enabled: true")
        if summaries.mineral_volume_change and not post.mineral_volume_change.enabled:
            raise ValueError(
                "mineral_volume_change output requires postprocessing.mineral_volume_change.enabled: true"
            )
        if summaries.regime_classification and not post.regime_classification.enabled:
            raise ValueError(
                "regime_classification output requires postprocessing.regime_classification.enabled: true"
            )
        if summaries.surface_area_audit and not post.surface_area_audit.enabled:
            raise ValueError("surface_area_audit output requires postprocessing.surface_area_audit.enabled: true")
        if summaries.workflow_comparison and not post.workflow_comparison.enabled:
            raise ValueError("workflow_comparison output requires postprocessing.workflow_comparison.enabled: true")
        if summaries.secondary_mineral_assemblage and not post.secondary_mineral_assemblage.enabled:
            raise ValueError(
                "secondary_mineral_assemblage output requires "
                "postprocessing.secondary_mineral_assemblage.enabled: true"
            )
        if summaries.surrogate_dataset and not post.surrogate_dataset.enabled:
            raise ValueError("surrogate_dataset output requires postprocessing.surrogate_dataset.enabled: true")
        if summaries.validation_ledger and not self.validation.enabled:
            raise ValueError("validation_ledger output requires validation.enabled: true")
        if summaries.porosity_permeability and not post.porosity_permeability.enabled:
            raise ValueError(
                "porosity_permeability output requires postprocessing.porosity_permeability.enabled: true"
            )
        if post.surrogate_dataset.enabled:
            if not (post.reaction_rates and post.element_budget.enabled and post.carbon_inventory.enabled):
                raise ValueError(
                    "surrogate_dataset requires reaction_rates, element_budget, and carbon_inventory diagnostics"
                )
        _validate_postprocessing_mappings(self)
        return self


@dataclass(frozen=True)
class ResolvedCase:
    config: CaseConfig
    config_path: Path
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


def load_case(
    config_path: str | Path,
    *,
    output_dir_override: str | Path | None = None,
) -> ResolvedCase:
    path = Path(config_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"case config does not exist: {path}")

    with path.open("r", encoding="utf-8") as stream:
        raw = yaml.safe_load(stream)
    if not isinstance(raw, dict):
        raise ValueError(f"case config must contain a YAML mapping: {path}")
    if output_dir_override is not None:
        raw["paths"]["output_dir"] = str(output_dir_override)

    config = CaseConfig.model_validate(raw)
    return resolve_case(config, path)


def resolve_case(config: CaseConfig, config_path: Path) -> ResolvedCase:
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


def _species_outputs_enabled(outputs: OutputsConfig) -> bool:
    return (
        outputs.summaries.aqueous_summary
        or (outputs.timeseries.enabled and outputs.timeseries.include_species_amounts)
        or (outputs.timeseries.enabled and outputs.timeseries.include_species_molalities)
    )


def _mineral_outputs_enabled(outputs: OutputsConfig) -> bool:
    return (
        outputs.summaries.mineral_summary
        or (outputs.timeseries.enabled and outputs.timeseries.include_mineral_amounts)
        or (outputs.timeseries.enabled and outputs.timeseries.include_mineral_deltas)
        or (outputs.timeseries.enabled and outputs.timeseries.include_saturation_indices)
        or (outputs.plots.enabled and outputs.plots.mineral_change)
        or (outputs.plots.enabled and outputs.plots.saturation_index)
    )


def _validate_postprocessing_mappings(config: CaseConfig) -> None:
    mineral_names = {mineral.name for mineral in config.minerals}
    post = config.postprocessing

    if post.reaction_rates and not config.kinetics.enabled:
        raise ValueError("reaction_rates requires kinetics.enabled: true")

    for label, names in (
        ("element_budget.minerals", set(post.element_budget.minerals)),
        ("carbon_inventory.carbon_minerals", set(post.carbon_inventory.carbon_minerals)),
        (
            "mineral_volume_change.molar_volumes_cm3_per_mol",
            set(post.mineral_volume_change.molar_volumes_cm3_per_mol),
        ),
    ):
        missing = names.difference(mineral_names)
        if missing:
            raise ValueError(f"{label} includes unconfigured minerals: " + ", ".join(sorted(missing)))

    for label, sources, volumes in (
        (
            "mineral_volume_change.sources",
            set(post.mineral_volume_change.sources),
            set(post.mineral_volume_change.molar_volumes_cm3_per_mol),
        ),
    ):
        missing = sources.difference(volumes)
        if missing:
            raise ValueError(f"{label} has sources for minerals without molar volumes: " + ", ".join(sorted(missing)))

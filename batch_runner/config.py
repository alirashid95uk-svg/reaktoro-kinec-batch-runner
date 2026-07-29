"""Strict YAML loading, validation, path resolution, and preprocessing."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

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
    path: str | None = None

    @model_validator(mode="after")
    def validate_kinetics(self) -> "KineticsConfig":
        if self.enabled and not self.path:
            raise ValueError("enabled kinetics requires path")
        if not self.enabled and self.path is not None:
            raise ValueError("disabled kinetics forbids path")
        return self


class MineralConfig(StrictModel):
    name: str = Field(min_length=1)
    thermo_name: str | None = Field(default=None, min_length=1)
    kinetic_name: str | None = Field(default=None, min_length=1)
    role: Literal["equilibrium", "kinetic"]
    initial_amount: Amount | None = None
    surface_area: SurfaceArea | None = None
    surface_area_basis: str | None = Field(default=None, min_length=1)
    surface_area_provenance: str | None = Field(default=None, min_length=1)
    selection_reason: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_role(self) -> "MineralConfig":
        if (self.thermo_name is None) != (self.kinetic_name is None):
            raise ValueError("explicit mineral alias requires both thermo_name and kinetic_name")
        if self.role == "kinetic" and (self.initial_amount is None or self.surface_area is None):
            raise ValueError("kinetic mineral requires initial_amount and surface_area")
        if self.role == "equilibrium" and self.surface_area is not None:
            raise ValueError("equilibrium minerals must not define surface_area")
        return self


class SolverWorkflowConfig(StrictModel):
    mode: WorkflowMode
    precondition_kinetics: bool


class TimeValue(StrictModel):
    value: float = Field(gt=0)
    unit: TimeUnit


class DurationConfig(StrictModel):
    duration_value: float = Field(gt=0)
    duration_unit: TimeUnit
    year_definition_days: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_year_definition(self) -> "DurationConfig":
        if self.duration_unit in {"year", "years"} and self.year_definition_days is None:
            raise ValueError("year duration requires year_definition_days")
        if self.duration_unit not in {"year", "years"} and self.year_definition_days is not None:
            raise ValueError("year_definition_days is only valid for year duration")
        return self


class FixedStepSizeConfig(StrictModel):
    dt: TimeValue


class FixedTimestepConfig(StrictModel):
    mode: Literal["fixed"]
    time: DurationConfig
    step_size: FixedStepSizeConfig


class SolverConfig(StrictModel):
    workflow: SolverWorkflowConfig
    timestep: FixedTimestepConfig


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
    kinec_rate_validation: bool
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
        thermo_names = [mineral.thermo_name or mineral.name for mineral in self.minerals]
        if len(thermo_names) != len(set(thermo_names)):
            raise ValueError("resolved thermodynamic mineral names must be unique")

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
        if summaries.kinec_rate_validation and not post.reaction_rates:
            raise ValueError("kinec_rate_validation output requires postprocessing.reaction_rates: true")
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
    full_steps: int
    final_step_s: float

    def step_sizes_s(self) -> tuple[float, ...]:
        if not self.config.kinetics.enabled:
            return ()
        dt_s = time_value_to_seconds(self.config.solver.timestep.step_size.dt)
        steps = [dt_s] * self.full_steps
        if self.final_step_s > 0:
            steps.append(self.final_step_s)
        return tuple(steps)

    def as_dict(self) -> dict[str, Any]:
        data = self.config.model_dump(mode="json")
        data["paths"]["output_dir"] = str(self.output_dir)
        if self.database_path is not None:
            data["database"]["path"] = str(self.database_path)
        if self.kinetics_path is not None:
            data["kinetics"]["path"] = str(self.kinetics_path)
        data["solver"]["timestep"]["derived_duration_s"] = self.duration_s
        data["solver"]["timestep"]["derived_full_steps"] = self.full_steps
        data["solver"]["timestep"]["derived_final_step_s"] = self.final_step_s
        data["source_config"] = str(self.config_path)
        return data


def load_case(config_path: str | Path) -> ResolvedCase:
    path = Path(config_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"case config does not exist: {path}")

    with path.open("r", encoding="utf-8") as stream:
        raw = yaml.safe_load(stream)
    if not isinstance(raw, dict):
        raise ValueError(f"case config must contain a YAML mapping: {path}")

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
            raise FileNotFoundError(f"Kinec kinetic YAML does not exist: {kinetics_path}")

    duration_s = 0.0
    full_steps = 0
    final_step_s = 0.0
    if config.kinetics.enabled:
        year_days = config.solver.timestep.time.year_definition_days
        duration_s = duration_to_seconds(config.solver.timestep.time)
        dt_s = time_value_to_seconds(config.solver.timestep.step_size.dt, year_days)
        full_steps, final_step_s = _derive_steps(duration_s, dt_s)

    return ResolvedCase(
        config=config,
        config_path=config_path,
        output_dir=output_dir,
        database_path=database_path,
        kinetics_path=kinetics_path,
        duration_s=duration_s,
        full_steps=full_steps,
        final_step_s=final_step_s,
    )


def duration_to_seconds(value: DurationConfig) -> float:
    year_days = value.year_definition_days
    return time_value_to_seconds(
        TimeValue(value=value.duration_value, unit=value.duration_unit),
        year_days,
    )


def time_value_to_seconds(value: TimeValue, year_definition_days: float | None = None) -> float:
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
    if value.unit in {"year", "years"}:
        if year_definition_days is None:
            year_definition_days = 365.25
        factor = Decimal(str(year_definition_days)) * Decimal("86400")
    else:
        factor = factors[value.unit]
    return float(Decimal(str(value.value)) * factor)


def _resolve_project_path(value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _derive_steps(duration_s: float, dt_s: float) -> tuple[int, float]:
    duration = Decimal(str(duration_s))
    dt = Decimal(str(dt_s))
    full_steps = int(duration // dt)
    remainder = duration - (dt * full_steps)
    return full_steps, float(remainder)


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

"""Top-level case schema and cross-section validation."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from ._base import Amount, DEFAULT_KINETIC_PATHS, KineticModel, StrictModel, SurfaceArea
from .reporting import OutputsConfig, PostprocessingConfig, ValidationConfig
from .timestep import AdaptiveTimestepConfig, SolverConfig


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

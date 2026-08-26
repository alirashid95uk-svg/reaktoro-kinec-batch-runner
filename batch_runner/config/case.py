"""Define the authoritative source-YAML case schema.

``loading`` parses YAML into :class:`CaseConfig`, then ``resolution`` derives
canonical paths, time values, and schedules for the simulator.  This module
owns cross-section scientific consistency: database selection, phase and
kinetics compatibility, mineral roles, workflow constraints, and requested
reporting products.  It does not construct Reaktoro objects or run a solver.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from ._base import Amount, DEFAULT_KINETIC_PATHS, KineticModel, StrictModel, SurfaceArea
from .reporting import OutputsConfig, PostprocessingConfig, ValidationConfig
from .timestep import SolverConfig


class CaseInfo(StrictModel):
    """Human-readable identity used in output provenance and run naming."""

    name: str = Field(min_length=1, description="Non-empty case name.")


class PathsConfig(StrictModel):
    """User-selected filesystem destinations resolved before execution."""

    output_dir: str = Field(
        min_length=1,
        description="Directory in which the simulation output package is written.",
    )


class DatabaseConfig(StrictModel):
    """Select exactly one explicit PHREEQC-style thermodynamic database source."""

    source: Literal["embedded", "local"] = Field(
        description="Database source: a Reaktoro embedded name or a project-local file."
    )
    name: str | None = Field(
        default=None,
        description="Embedded database name; required for embedded and forbidden for local.",
    )
    path: str | None = Field(
        default=None,
        description="Local database path; required for local and forbidden for embedded.",
    )

    @model_validator(mode="after")
    def validate_source_fields(self) -> "DatabaseConfig":
        """Embedded requires ``name`` and forbids ``path``; local does the reverse."""

        if self.source == "embedded":
            if not self.name or self.path is not None:
                raise ValueError("embedded database requires name and forbids path")
        elif not self.path or self.name is not None:
            raise ValueError("local database requires path and forbids name")
        return self


class ActivityModelsConfig(StrictModel):
    """Select the implemented aqueous and optional gas activity models."""

    aqueous: Literal["phreeqc"] = Field(
        description="Aqueous activity model used to construct the Reaktoro aqueous phase."
    )
    gas: Literal["peng_robinson_phreeqc"] | None = Field(
        default=None,
        description=(
            "Optional gas activity model; required by a finite CO2 gas phase and "
            "otherwise omitted when no gas phase is constructed."
        ),
    )


class PhysicalConfig(StrictModel):
    """Thermodynamic conditions applied while constructing and solving the case."""

    temperature_c: float = Field(description="Simulation temperature in degrees Celsius.")
    pressure_bar: float = Field(gt=0, description="Positive simulation pressure in bar.")


class BrineConfig(StrictModel):
    """Define the aqueous phase and exactly one initial brine inventory."""

    aqueous_elements: list[str] = Field(
        min_length=1,
        description="Elements admitted to the aqueous phase chemical system.",
    )
    species_amounts: dict[str, Amount] | None = Field(
        default=None,
        min_length=1,
        description=(
            "Explicit initial aqueous species amounts; mutually exclusive with "
            "element_amounts and may represent a disequilibrium inventory."
        ),
    )
    element_amounts: dict[str, Amount] | None = Field(
        default=None,
        min_length=1,
        description=(
            "Conserved initial aqueous element totals; mutually exclusive with "
            "species_amounts, with every key listed in aqueous_elements."
        ),
    )

    @model_validator(mode="after")
    def validate_initialization(self) -> "BrineConfig":
        """Require exactly one inventory form; element keys must be aqueous elements."""

        if (self.species_amounts is None) == (self.element_amounts is None):
            raise ValueError("brine requires exactly one of species_amounts or element_amounts")
        if self.element_amounts is not None:
            unknown = sorted(set(self.element_amounts) - set(self.aqueous_elements))
            if unknown:
                raise ValueError(
                    "brine.element_amounts keys must be listed in brine.aqueous_elements: "
                    + ", ".join(unknown)
                )
        return self


class Co2Config(StrictModel):
    """Configure the implemented closed-amount or fixed-fugacity CO2 boundary."""

    mode: Literal["disabled", "finite", "fixed_fugacity"] = Field(
        description="CO2 boundary mode controlling whether and how CO2 enters the system."
    )
    gas_species: str | None = Field(
        default=None,
        description="CO2 gas species name; required for finite and fixed_fugacity modes.",
    )
    initial_amount: Amount | None = Field(
        default=None,
        description="Finite initial CO2 amount; required only in finite mode.",
    )
    fugacity_bar: float | None = Field(
        default=None,
        gt=0,
        description="Positive imposed CO2 fugacity in bar; required only in fixed_fugacity mode.",
    )

    @model_validator(mode="after")
    def validate_mode(self) -> "Co2Config":
        """Disabled forbids all inputs; finite requires species/amount; fugacity requires species/bar."""

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
    """Control whether a pE constraint is applied and at which solver stage."""

    enabled: bool = Field(description="Enable the explicit pE constraint.")
    pe: float | None = Field(
        default=None,
        description="Dimensionless pE value; required when redox is enabled.",
    )
    apply_during: Literal["initial_equilibrium_only", "kinetic_steps"] | None = Field(
        default=None,
        description=(
            "Solver stage receiving the pE constraint; required when enabled and "
            "forbidden when disabled."
        ),
    )

    @model_validator(mode="after")
    def validate_redox(self) -> "RedoxConfig":
        """Enabled redox requires ``pe`` and ``apply_during``; disabled forbids both."""

        if self.enabled and (self.pe is None or self.apply_during is None):
            raise ValueError("enabled redox requires pe and apply_during")
        if not self.enabled and (self.pe is not None or self.apply_during is not None):
            raise ValueError("disabled redox forbids pe and apply_during")
        return self


class KineticsConfig(StrictModel):
    """Enable mineral kinetics and select its YAML rate-parameter source.

    When enabled, omitting ``model`` selects ``palandri_kharaka`` and omitting
    ``path`` selects that model's project-local default.  Disabled kinetics
    forbids both fields.  Resolution never infers a model from a filename.
    """

    enabled: bool = Field(description="Enable kinetic reactions for kinetic minerals.")
    model: KineticModel | None = Field(
        default=None,
        description=(
            "Rate model; when kinetics is enabled, omission resolves to "
            "palandri_kharaka, and the field is forbidden when disabled."
        ),
        json_schema_extra={
            "x-effective-default": "palandri_kharaka",
            "x-default-when": "kinetics.enabled is true and model is omitted",
        },
    )
    path: str | None = Field(
        default=None,
        description=(
            "Kinetics YAML path; when enabled, omission resolves from the selected "
            "model, and the field is forbidden when disabled."
        ),
        json_schema_extra={
            "x-effective-default": DEFAULT_KINETIC_PATHS,
            "x-default-when": "kinetics.enabled is true and path is omitted",
        },
    )

    @model_validator(mode="after")
    def validate_kinetics(self) -> "KineticsConfig":
        """Enabled omissions resolve to Palandri-Kharaka/model path; disabled forbids both."""

        if self.enabled:
            self.model = self.model or "palandri_kharaka"
            self.path = self.path or DEFAULT_KINETIC_PATHS[self.model]
        elif self.model is not None or self.path is not None:
            raise ValueError("disabled kinetics forbids model and path")
        return self


class MineralConfig(StrictModel):
    """Declare one thermodynamic mineral and its equilibrium or kinetic role."""

    name: str = Field(min_length=1, description="Mineral species name in the database.")
    role: Literal["equilibrium", "kinetic"] = Field(
        description="Whether the mineral is equilibrated or integrated kinetically."
    )
    initial_amount: Amount | None = Field(
        default=None,
        description="Initial mineral amount; required for kinetic minerals.",
    )
    surface_area: SurfaceArea | None = Field(
        default=None,
        description="Reactive surface area; required for kinetic and forbidden for equilibrium minerals.",
    )
    surface_area_basis: str | None = Field(
        default=None,
        min_length=1,
        description="Scientific basis used to define the reactive surface area.",
    )
    surface_area_provenance: str | None = Field(
        default=None,
        min_length=1,
        description="Source or derivation record for the reactive surface area.",
    )
    selection_reason: str | None = Field(
        default=None,
        min_length=1,
        description="Scientific reason for including this mineral in the case.",
    )

    @model_validator(mode="after")
    def validate_role(self) -> "MineralConfig":
        """Kinetic minerals require amount/area; equilibrium minerals forbid area."""

        if self.role == "kinetic" and (self.initial_amount is None or self.surface_area is None):
            raise ValueError("kinetic mineral requires initial_amount and surface_area")
        if self.role == "equilibrium" and self.surface_area is not None:
            raise ValueError("equilibrium minerals must not define surface_area")
        return self


class CaseConfig(StrictModel):
    """Validated user-facing case consumed by resolution and simulation setup."""

    case: CaseInfo = Field(description="Case identity and provenance block.")
    paths: PathsConfig = Field(description="Filesystem destination block.")
    database: DatabaseConfig = Field(description="Thermodynamic database selection.")
    activity_models: ActivityModelsConfig = Field(description="Phase activity-model selections.")
    physical: PhysicalConfig = Field(description="Simulation temperature and pressure.")
    brine: BrineConfig = Field(description="Aqueous phase definition and initial inventory.")
    co2: Co2Config = Field(description="CO2 phase or fugacity boundary configuration.")
    redox: RedoxConfig = Field(description="Optional pE constraint and application stage.")
    kinetics: KineticsConfig = Field(description="Kinetic rate-model configuration.")
    minerals: list[MineralConfig] = Field(
        min_length=1,
        description="Configured equilibrium and kinetic minerals; names must be unique.",
    )
    solver: SolverConfig = Field(description="Workflow and timestep configuration.")
    postprocessing: PostprocessingConfig = Field(description="Scientific diagnostic selections.")
    validation: ValidationConfig = Field(description="Optional downstream validation hook.")
    outputs: OutputsConfig = Field(description="Output-package and monitor selections.")

    @model_validator(mode="after")
    def validate_mineral_kinetics_contract(self) -> "CaseConfig":
        """Require unique minerals, workflow-compatible kinetics, and exact Richardson coverage."""

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
        timestep = self.solver.timestep
        if not self.kinetics.enabled and timestep.mode != "fixed":
            raise ValueError("adaptive timestep modes require kinetics.enabled: true")
        if timestep.mode == "adaptive_error_controlled":
            if (
                workflow.mode
                == "fixed_fugacity_initial_equilibrium_then_closed_kinetics"
                or (
                    self.redox.enabled
                    and self.redox.apply_during == "initial_equilibrium_only"
                )
            ):
                raise ValueError(
                    "adaptive_error_controlled does not support an initial-equilibrium stage"
                )
            kinetic_names = {
                mineral.name for mineral in self.minerals if mineral.role == "kinetic"
            }
            controlled_names = {
                item.name for item in timestep.error_control.controlled_minerals
            }
            if controlled_names != kinetic_names:
                missing = sorted(kinetic_names - controlled_names)
                extra = sorted(controlled_names - kinetic_names)
                raise ValueError(
                    "error_control.controlled_minerals must exactly match kinetic minerals; "
                    f"missing={missing}, extra={extra}"
                )

        return self

    @model_validator(mode="after")
    def validate_boundary_conditions(self) -> "CaseConfig":
        """Require compatible fixed-fugacity/finite-gas boundaries and redox workflow staging."""

        workflow = self.solver.workflow
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

        return self

    @model_validator(mode="after")
    def validate_output_selections(self) -> "CaseConfig":
        """Require configured requested quantities, monitor subsets, and solver-history plot inputs."""

        names = [mineral.name for mineral in self.minerals]
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

        monitor = self.outputs.monitor
        missing_monitor_species = set(monitor.species).difference(
            self.postprocessing.requested_species
        )
        if missing_monitor_species:
            raise ValueError(
                "outputs.monitor.species are not postprocessing.requested_species: "
                + ", ".join(sorted(missing_monitor_species))
            )
        missing_monitor_minerals = set(monitor.minerals).difference(requested_minerals)
        if missing_monitor_minerals:
            raise ValueError(
                "outputs.monitor.minerals are not postprocessing.requested_minerals: "
                + ", ".join(sorted(missing_monitor_minerals))
            )

        if self.outputs.plots.solver_dt and not self.outputs.solver_history.enabled:
            raise ValueError("solver_dt plot requires solver_history output")
        if self.outputs.plots.solver_iterations and not self.outputs.solver_history.enabled:
            raise ValueError("solver_iterations plot requires solver_history output")
        return self

    @model_validator(mode="after")
    def validate_summary_dependencies(self) -> "CaseConfig":
        """Each enabled summary requires its same-named diagnostic; surrogate export requires three."""

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
        if summaries.porosity_permeability and not post.porosity_permeability.enabled:
            raise ValueError(
                "porosity_permeability output requires postprocessing.porosity_permeability.enabled: true"
            )
        if post.surrogate_dataset.enabled:
            if not (post.reaction_rates and post.element_budget.enabled and post.carbon_inventory.enabled):
                raise ValueError(
                    "surrogate_dataset requires reaction_rates, element_budget, and carbon_inventory diagnostics"
                )
        return self

    @model_validator(mode="after")
    def validate_postprocessing_mappings(self) -> "CaseConfig":
        """Require kinetics for rates and configured minerals for every diagnostic mapping."""

        _validate_postprocessing_mappings(self)
        return self


def _species_outputs_enabled(outputs: OutputsConfig) -> bool:
    """Return whether any enabled output consumes selected aqueous species."""

    return (
        outputs.summaries.aqueous_summary
        or (outputs.timeseries.enabled and outputs.timeseries.include_species_amounts)
        or (outputs.timeseries.enabled and outputs.timeseries.include_species_molalities)
    )


def _mineral_outputs_enabled(outputs: OutputsConfig) -> bool:
    """Return whether any enabled output consumes selected minerals."""

    return (
        outputs.summaries.mineral_summary
        or (outputs.timeseries.enabled and outputs.timeseries.include_mineral_amounts)
        or (outputs.timeseries.enabled and outputs.timeseries.include_mineral_deltas)
        or (outputs.timeseries.enabled and outputs.timeseries.include_saturation_indices)
        or (outputs.plots.enabled and outputs.plots.mineral_change)
        or (outputs.plots.enabled and outputs.plots.saturation_index)
    )


def _validate_postprocessing_mappings(config: CaseConfig) -> None:
    """Reject diagnostic maps whose mineral names or prerequisites are invalid."""

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

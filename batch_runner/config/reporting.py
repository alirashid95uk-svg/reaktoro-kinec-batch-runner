"""Define scientific postprocessing, presentation, and validation selections.

Postprocessing selections determine both the quantities collected from accepted
states and their standard data products. Plot, monitor, and debug settings are
presentation or troubleshooting controls only. None of these models add solver
physics or alter accepted states.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from ._base import StrictModel
from .timestep import TimeValue


class PostprocessingConfig(StrictModel):
    """Select scientific quantities; each selection writes its standard data products."""

    requested_species: list[str] = Field(
        description=(
            "Aqueous species whose amounts and molalities are recorded in the "
            "timeseries and aqueous_summary.csv."
        )
    )
    requested_elements: list[str] = Field(
        default_factory=list,
        description="Elements whose aqueous molalities are recorded in the timeseries.",
    )
    requested_minerals: list[str] = Field(
        description=(
            "Configured minerals whose amounts, changes, and saturation indices are "
            "recorded in the timeseries and mineral_summary.csv."
        )
    )
    reaction_rates: bool = Field(
        description="Evaluate configured kinetic reaction rates and write reaction_rates.csv."
    )
    reaction_rate_validation: bool = Field(
        description=(
            "Write reaction_rate_validation.csv; requires reaction_rates."
        )
    )
    element_budget: "ElementBudgetConfig" = Field(
        description="Element-budget reconstruction diagnostic."
    )
    carbon_inventory: "CarbonInventoryConfig" = Field(
        description="Carbon-inventory reconstruction diagnostic."
    )
    mineral_volume_change: "MineralVolumeChangeConfig" = Field(
        description="Mineral-volume-change diagnostic."
    )
    regime_classification: "EnabledConfig" = Field(
        description="Enable derived reaction-regime classification."
    )
    surface_area_audit: "EnabledConfig" = Field(
        description="Enable the configured mineral surface-area audit."
    )
    workflow_comparison: "EnabledConfig" = Field(
        description="Enable reporting that compares applicable workflow stages."
    )
    secondary_mineral_assemblage: "EnabledConfig" = Field(
        description="Enable the configured secondary-mineral assemblage report."
    )
    surrogate_dataset: "SurrogateDatasetConfig" = Field(
        description="Configure export of a derived surrogate-training dataset."
    )
    porosity_permeability: "PorosityPermeabilityConfig" = Field(
        description="Configure porosity reporting and unsupported-law status output."
    )


class EnabledConfig(StrictModel):
    """Enable or disable a diagnostic that needs no additional parameters."""

    enabled: bool = Field(
        description="Enable this derived diagnostic and write its standard table."
    )


class ElementBudgetConfig(StrictModel):
    """Configure a reporting-only element inventory from explicit stoichiometry."""

    enabled: bool = Field(
        description="Enable reconstruction and write element_budget.csv."
    )
    elements: list[str] = Field(
        description="Element symbols included in the reconstructed budget."
    )
    species: dict[str, dict[str, float]] = Field(
        description="Aqueous species to non-negative element stoichiometry mappings."
    )
    minerals: dict[str, dict[str, float]] = Field(
        description="Mineral names to non-negative element stoichiometry mappings."
    )
    gas_species: dict[str, dict[str, float]] = Field(
        description="Gas species to non-negative element stoichiometry mappings."
    )

    @model_validator(mode="after")
    def validate_budget(self) -> "ElementBudgetConfig":
        """Enabled budgets require elements/mappings with non-negative known-element coefficients."""

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
    """Configure a reporting-only carbon inventory from explicit coefficients."""

    enabled: bool = Field(
        description="Enable reconstruction and write carbon_inventory.csv."
    )
    carbon_species: dict[str, float] = Field(
        description="Aqueous species to non-negative carbon coefficients."
    )
    carbon_minerals: dict[str, float] = Field(
        description="Mineral names to non-negative carbon coefficients."
    )
    carbon_gas_species: dict[str, float] = Field(
        description="Gas species to non-negative carbon coefficients."
    )

    @model_validator(mode="after")
    def validate_inventory(self) -> "CarbonInventoryConfig":
        """Enabled inventories require a non-negative mapping; disabled forbids mappings."""

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
    """Configure mineral-volume-change derivation from sourced molar volumes."""

    enabled: bool = Field(
        description="Enable derivation and write mineral_volume_change.csv."
    )
    molar_volumes_cm3_per_mol: dict[str, float] = Field(
        description="Positive mineral molar volumes in cubic centimetres per mole."
    )
    sources: dict[str, str] = Field(
        description="Mineral names to provenance text for configured molar volumes."
    )

    @model_validator(mode="after")
    def validate_volume_sources(self) -> "MineralVolumeChangeConfig":
        """Enabled molar volumes must be positive; disabled diagnostics forbid values/sources."""

        if not self.enabled:
            if self.molar_volumes_cm3_per_mol or self.sources:
                raise ValueError("disabled mineral_volume_change forbids molar volumes and sources")
            return self
        if any(value <= 0 for value in self.molar_volumes_cm3_per_mol.values()):
            raise ValueError("mineral molar volumes must be positive")
        return self


class SurrogateDatasetConfig(StrictModel):
    """Control export of derived features for downstream surrogate modelling."""

    enabled: bool = Field(description="Enable writing surrogate_dataset.csv.")
    validity_domain: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Non-empty applicability statement required when export is enabled "
            "and forbidden when disabled."
        ),
    )

    @model_validator(mode="after")
    def validate_domain(self) -> "SurrogateDatasetConfig":
        """Enabled export requires ``validity_domain``; disabled export forbids it."""

        if self.enabled and not self.validity_domain:
            raise ValueError("enabled surrogate_dataset requires validity_domain")
        if not self.enabled and self.validity_domain is not None:
            raise ValueError("disabled surrogate_dataset forbids validity_domain")
        return self


class PorosityPermeabilityConfig(StrictModel):
    """Configure porosity reporting without implying unsupported transport laws."""

    enabled: bool = Field(description="Enable writing porosity_permeability.csv.")
    bulk_volume_cm3: float | None = Field(
        default=None,
        gt=0,
        description="Optional positive bulk volume in cubic centimetres for porosity derivation.",
    )
    permeability_update_law: str | None = Field(
        default=None,
        min_length=1,
        description="Reserved update law; all non-null values are currently rejected as unsupported.",
        json_schema_extra={"x-status": "unsupported"},
    )
    capillary_entry_pressure_law: str | None = Field(
        default=None,
        min_length=1,
        description="Reserved update law; all non-null values are currently rejected as unsupported.",
        json_schema_extra={"x-status": "unsupported"},
    )

    @model_validator(mode="after")
    def validate_laws(self) -> "PorosityPermeabilityConfig":
        """Disabled reporting forbids inputs; all transport update laws remain unsupported."""

        if not self.enabled and (
            self.bulk_volume_cm3 is not None
            or self.permeability_update_law is not None
            or self.capillary_entry_pressure_law is not None
        ):
            raise ValueError("disabled porosity_permeability forbids volume and update laws")
        if self.permeability_update_law is not None or self.capillary_entry_pressure_law is not None:
            raise ValueError("permeability and capillary-entry-pressure update laws are not implemented")
        return self


class ValidationConfig(StrictModel):
    """Select an optional trusted post-run validation script.

    The hook runs only after a successful output package is complete and does
    not participate in simulation, output construction, or scientific validity.
    """

    enabled: bool = Field(description="Run the downstream validation hook after simulation.")
    script: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Project-local Python validation script; required when enabled and "
            "forbidden when disabled."
        ),
    )

    @model_validator(mode="after")
    def validate_script(self) -> "ValidationConfig":
        """Enabled validation requires ``script``; disabled validation forbids it."""

        if self.enabled and self.script is None:
            raise ValueError("enabled validation requires script")
        if not self.enabled and self.script is not None:
            raise ValueError("disabled validation forbids script")
        return self


class PlotsConfig(StrictModel):
    """Select plots generated from existing timeseries and solver-history data."""

    enabled: bool = Field(description="Enable plot generation.")
    pH: bool = Field(description="Generate the pH versus time plot.")
    mineral_change: bool = Field(description="Generate mineral change versus time plots.")
    saturation_index: bool = Field(
        description="Generate mineral saturation-index versus time plots."
    )
    solver_dt: bool = Field(
        description="Generate the attempted-timestep plot from standard solver history."
    )
    solver_iterations: bool = Field(
        description="Generate the iteration-count plot from standard solver history."
    )

    @model_validator(mode="after")
    def validate_plots(self) -> "PlotsConfig":
        """Require at least one selected plot when plot generation is enabled."""

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


class DebugConfig(StrictModel):
    """Select explicitly requested debug artifacts."""

    enabled: bool = Field(description="Enable writing selected debug artifacts.")
    mineral_connection: bool = Field(description="Write mineral-to-kinetics connection details.")
    resolved_config: bool = Field(description="Write the canonical resolved configuration.")
    final_state: bool = Field(description="Write the final Reaktoro chemical-state dump.")


class MonitorConfig(StrictModel):
    """Configure observational terminal telemetry from accepted-state events only."""

    enabled: bool = Field(default=True, description="Show the human-readable simulation monitor.")
    refresh_interval_s: float = Field(
        default=0.5,
        gt=0,
        allow_inf_nan=False,
        description="Positive wall-clock refresh interval in seconds.",
    )
    scalars: list[Literal["pH", "ionic_strength_molal", "alkalinity_eq_per_l"]] = Field(
        default_factory=lambda: ["pH"],
        description="Unique scalar names shown by the monitor; defaults to pH.",
    )
    species: list[str] = Field(
        default_factory=list,
        description="Unique monitored species already selected in postprocessing.requested_species.",
    )
    minerals: list[str] = Field(
        default_factory=list,
        description="Unique monitored minerals already selected in postprocessing.requested_minerals.",
    )
    result_times: list[TimeValue] = Field(
        default_factory=list,
        description=(
            "Existing scientific output times highlighted by the monitor; these never "
            "create solver targets."
        ),
    )

    @model_validator(mode="after")
    def validate_selections(self) -> "MonitorConfig":
        """Reject duplicate scalar, species, and mineral selections."""

        for label, values in (
            ("scalars", self.scalars),
            ("species", self.species),
            ("minerals", self.minerals),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"monitor.{label} must not contain duplicates")
        return self

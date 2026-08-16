"""Postprocessing, validation, and output configuration models."""

from __future__ import annotations

from pydantic import Field, model_validator

from ._base import StrictModel


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

"""Reproducible, non-extrapolating comparison specification."""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from .common import ArtifactIdentity, NonEmptyStr, SoftwareIdentity, StrictModel


COMPARISON_SCHEMA_VERSION = "1.0"


class UnitConversion(StrictModel):
    quantity_id: NonEmptyStr
    source_unit: NonEmptyStr
    target_unit: NonEmptyStr
    factor: float = Field(allow_inf_nan=False)
    offset: float = Field(allow_inf_nan=False)


class TimeTolerance(StrictModel):
    value: float = Field(ge=0, allow_inf_nan=False)
    unit: NonEmptyStr


class InterpolationRule(StrictModel):
    quantity_id: NonEmptyStr
    variable_class: NonEmptyStr
    method: NonEmptyStr
    derived_values_label: Literal["derived"]


class ExcludedRun(StrictModel):
    run_id: NonEmptyStr
    reason: NonEmptyStr


class ComparisonSpec(StrictModel):
    comparison_schema_version: Literal["1.0"]
    comparison_id: NonEmptyStr
    created_at_utc: AwareDatetime
    source_run_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)
    source_schema_versions: dict[str, NonEmptyStr]
    selected_quantities: tuple[NonEmptyStr, ...] = Field(min_length=1)
    unit_conversions: tuple[UnitConversion, ...]
    completion_filters: tuple[NonEmptyStr, ...]
    time_alignment_mode: Literal[
        "native_accepted_grids",
        "exact_common_timestamps",
        "initial_state",
        "final_state",
        "interpolation",
    ]
    common_time_tolerance: TimeTolerance | None
    interpolation_policy: tuple[InterpolationRule, ...]
    extrapolation_policy: Literal["forbidden"]
    excluded_runs: tuple[ExcludedRun, ...]
    created_artifacts: tuple[ArtifactIdentity, ...]
    software_identity: SoftwareIdentity

    @model_validator(mode="after")
    def enforce_safe_alignment(self) -> "ComparisonSpec":
        if len(set(self.source_run_ids)) != len(self.source_run_ids):
            raise ValueError("source_run_ids must be unique")
        if set(self.source_schema_versions) != set(self.source_run_ids):
            raise ValueError("source_schema_versions must cover every source run exactly")
        if len(set(self.selected_quantities)) != len(self.selected_quantities):
            raise ValueError("selected_quantities must be unique")
        excluded_ids = [excluded.run_id for excluded in self.excluded_runs]
        if len(excluded_ids) != len(set(excluded_ids)):
            raise ValueError("excluded run IDs must be unique")
        unknown_excluded = set(excluded_ids) - set(self.source_run_ids)
        if unknown_excluded:
            raise ValueError(f"excluded ledger references unknown runs: {sorted(unknown_excluded)}")
        unknown_conversions = {
            conversion.quantity_id for conversion in self.unit_conversions
        } - set(self.selected_quantities)
        if unknown_conversions:
            raise ValueError(f"unit conversions reference unselected quantities: {sorted(unknown_conversions)}")
        if self.time_alignment_mode == "exact_common_timestamps":
            if self.common_time_tolerance is None:
                raise ValueError("exact common timestamps require a tolerance")
        elif self.common_time_tolerance is not None:
            raise ValueError("common_time_tolerance is only valid for exact common timestamps")
        if self.time_alignment_mode == "interpolation":
            if not self.interpolation_policy:
                raise ValueError("interpolation mode requires an explicit policy")
        elif self.interpolation_policy:
            raise ValueError("interpolation policy is only valid in interpolation mode")
        return self

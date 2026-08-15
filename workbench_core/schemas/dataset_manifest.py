"""Traceable, leakage-safe dataset export manifest."""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from .common import NonEmptyStr, QuantityDefinition, Sha256, SoftwareIdentity, StrictModel


DATASET_SCHEMA_VERSION = "1.0"


class DatasetSourceRun(StrictModel):
    run_id: NonEmptyStr
    output_schema_version: NonEmptyStr
    scientific_fingerprint: Sha256


class DatasetArtifact(StrictModel):
    format: Literal["csv", "parquet"]
    path: NonEmptyStr
    sha256: Sha256


class SplitDefinition(StrictModel):
    group_column: NonEmptyStr
    algorithm: NonEmptyStr
    proportions: dict[Literal["train", "validation", "test"], float]
    seed: int = Field(ge=0)
    run_ids_by_split: dict[Literal["train", "validation", "test"], tuple[str, ...]]
    excluded_groups: tuple[NonEmptyStr, ...]
    leakage_checks: tuple[NonEmptyStr, ...]

    @model_validator(mode="after")
    def prevent_run_leakage(self) -> "SplitDefinition":
        if set(self.proportions) != {"train", "validation", "test"}:
            raise ValueError("split proportions require train, validation, and test")
        if any(value < 0 for value in self.proportions.values()) or abs(sum(self.proportions.values()) - 1.0) > 1e-12:
            raise ValueError("split proportions must be non-negative and sum to one")
        seen: set[str] = set()
        for split, run_ids in self.run_ids_by_split.items():
            overlap = seen.intersection(run_ids)
            if overlap:
                raise ValueError(f"run IDs cross dataset splits at {split}: {sorted(overlap)}")
            if len(run_ids) != len(set(run_ids)):
                raise ValueError(f"duplicate run ID within {split} split")
            seen.update(run_ids)
        return self


class ExcludedDatasetRun(StrictModel):
    run_id: NonEmptyStr
    reason: NonEmptyStr


class DatasetManifest(StrictModel):
    dataset_schema_version: Literal["1.0"]
    dataset_id: NonEmptyStr
    created_at_utc: AwareDatetime
    dataset_type: Literal[
        "final_state",
        "fixed_time",
        "time_dependent_tabular",
        "trajectory",
        "failure",
    ]
    source_study_id: str | None
    explicit_run_set_id: str | None
    source_runs: tuple[DatasetSourceRun, ...] = Field(min_length=1)
    features: tuple[QuantityDefinition, ...]
    targets: tuple[QuantityDefinition, ...]
    time_semantics: NonEmptyStr
    validity_domain: dict[str, JsonValue]
    completion_qc_filters: tuple[NonEmptyStr, ...]
    missing_value_policy: NonEmptyStr
    duplicate_policy: NonEmptyStr
    split_definition: SplitDefinition
    seed: int = Field(ge=0)
    excluded_runs: tuple[ExcludedDatasetRun, ...]
    failure_ledger_path: NonEmptyStr
    artifacts: tuple[DatasetArtifact, ...] = Field(min_length=1)
    software_identity: SoftwareIdentity

    @model_validator(mode="after")
    def require_traceable_nonleaking_sources(self) -> "DatasetManifest":
        if (self.source_study_id is None) == (self.explicit_run_set_id is None):
            raise ValueError("provide exactly one source_study_id or explicit_run_set_id")
        run_ids = [run.run_id for run in self.source_runs]
        if len(run_ids) != len(set(run_ids)):
            raise ValueError("source run IDs must be unique")
        split_runs = {
            run_id
            for values in self.split_definition.run_ids_by_split.values()
            for run_id in values
        }
        unknown = split_runs - set(run_ids)
        if unknown:
            raise ValueError(f"split references unknown source runs: {sorted(unknown)}")
        excluded_ids = [excluded.run_id for excluded in self.excluded_runs]
        if len(excluded_ids) != len(set(excluded_ids)):
            raise ValueError("excluded run IDs must be unique")
        unknown_excluded = set(excluded_ids) - set(run_ids)
        if unknown_excluded:
            raise ValueError(f"excluded ledger references unknown runs: {sorted(unknown_excluded)}")
        overlap = split_runs.intersection(excluded_ids)
        if overlap:
            raise ValueError(f"runs cannot be both split and excluded: {sorted(overlap)}")
        unaccounted = set(run_ids) - split_runs - set(excluded_ids)
        if unaccounted:
            raise ValueError(f"source runs missing from split or exclusion ledger: {sorted(unaccounted)}")
        if self.seed != self.split_definition.seed:
            raise ValueError("dataset seed must match split-definition seed")
        quantity_ids = [quantity.quantity_id for quantity in (*self.features, *self.targets)]
        if len(quantity_ids) != len(set(quantity_ids)):
            raise ValueError("feature and target quantity IDs must be unique")
        return self

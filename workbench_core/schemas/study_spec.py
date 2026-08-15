"""Versioned parameter-study specification and append/finalise manifest."""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from .common import ArtifactIdentity, NonEmptyStr, Sha256, StrictModel


STUDY_SCHEMA_VERSION = "1.0"
STUDY_MANIFEST_SCHEMA_VERSION = "1.0"


class NumericRange(StrictModel):
    minimum: float = Field(allow_inf_nan=False)
    maximum: float = Field(allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered(self) -> "NumericRange":
        if self.minimum > self.maximum:
            raise ValueError("minimum cannot exceed maximum")
        return self


class ParameterDefinition(StrictModel):
    parameter_id: NonEmptyStr
    yaml_path: tuple[str | int, ...] = Field(min_length=1)
    data_type: Literal["integer", "number", "string", "boolean"]
    scientific_meaning: NonEmptyStr
    entered_unit: str | None
    canonical_unit: str | None
    range: NumericRange | None = None
    values: tuple[JsonValue, ...] | None = None
    categories: tuple[JsonValue, ...] | None = None
    imported_values: tuple[JsonValue, ...] | None = None
    sampling_distribution: NonEmptyStr
    transform: NonEmptyStr
    provenance_requirement: NonEmptyStr
    constraint_group_membership: tuple[str, ...]

    @model_validator(mode="after")
    def require_one_value_source(self) -> "ParameterDefinition":
        sources = (self.range, self.values, self.categories, self.imported_values)
        if sum(source is not None for source in sources) != 1:
            raise ValueError(
                "parameter requires exactly one of range, values, categories, or imported_values"
            )
        if (self.entered_unit is None) != (self.canonical_unit is None):
            raise ValueError("entered_unit and canonical_unit must be supplied together")
        if len(self.constraint_group_membership) != len(set(self.constraint_group_membership)):
            raise ValueError("constraint_group_membership values must be unique")
        return self


class ConstraintDefinition(StrictModel):
    constraint_id: NonEmptyStr
    constraint_type: Literal[
        "bounds",
        "categorical_legality",
        "dependency",
        "temperature_pressure_domain",
        "co2_workflow_consistency",
        "kinetic_surface_area",
        "correlation",
        "composition_closure",
        "group_total",
        "conditional_field",
    ]
    parameter_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)
    settings: dict[str, JsonValue]


class StudyExecutionPolicy(StrictModel):
    max_workers: int = Field(ge=1)
    failure_policy: Literal[
        "stop_after_failure", "continue_after_failure", "pause_for_decision"
    ]
    allow_replicates: bool


class ImportedColumnMapping(StrictModel):
    column_name: NonEmptyStr
    parameter_id: NonEmptyStr
    entered_unit: str | None


class ProvenanceRecord(StrictModel):
    subject: NonEmptyStr
    origin: Literal[
        "bibliographic_source",
        "deterministic_derivation",
        "user_decision",
        "software_default",
        "unsupported_or_missing",
    ]
    reference: NonEmptyStr


class StudySpec(StrictModel):
    study_schema_version: Literal["1.0"]
    study_id: NonEmptyStr
    study_name: NonEmptyStr
    baseline_case_path: NonEmptyStr
    baseline_case_sha256: Sha256
    baseline_scientific_fingerprint: Sha256
    sampling_method: Literal[
        "grid", "random", "latin_hypercube", "imported_matrix", "existing_cases"
    ]
    seed: int = Field(ge=0)
    sample_count: int = Field(ge=1)
    parameters: tuple[ParameterDefinition, ...]
    constraint_groups: tuple[ConstraintDefinition, ...]
    cross_parameter_constraints: tuple[ConstraintDefinition, ...]
    generated_case_directory: NonEmptyStr
    execution_policy: StudyExecutionPolicy
    required_outputs: tuple[NonEmptyStr, ...] = Field(min_length=1)
    validity_domain: dict[str, JsonValue]
    provenance: tuple[ProvenanceRecord, ...] = Field(min_length=1)
    imported_matrix_path: str | None = None
    imported_matrix_sha256: Sha256 | None = None
    imported_column_mapping: tuple[ImportedColumnMapping, ...] | None = None
    existing_case_paths: tuple[NonEmptyStr, ...] | None = None

    @model_validator(mode="after")
    def require_unique_ids_and_constraint_references(self) -> "StudySpec":
        parameter_ids = [parameter.parameter_id for parameter in self.parameters]
        if len(parameter_ids) != len(set(parameter_ids)):
            raise ValueError("parameter_id values must be unique")
        parameter_paths = [parameter.yaml_path for parameter in self.parameters]
        if len(parameter_paths) != len(set(parameter_paths)):
            raise ValueError("varied YAML paths must be unique")
        if len(self.required_outputs) != len(set(self.required_outputs)):
            raise ValueError("required_outputs values must be unique")
        known = set(parameter_ids)
        constraints = (*self.constraint_groups, *self.cross_parameter_constraints)
        constraint_ids = [constraint.constraint_id for constraint in constraints]
        if len(constraint_ids) != len(set(constraint_ids)):
            raise ValueError("constraint_id values must be unique")
        for constraint in constraints:
            missing = set(constraint.parameter_ids) - known
            if missing:
                raise ValueError(f"constraint references unknown parameters: {sorted(missing)}")
        for parameter in self.parameters:
            actual = {
                constraint.constraint_id
                for constraint in constraints
                if parameter.parameter_id in constraint.parameter_ids
            }
            declared = set(parameter.constraint_group_membership)
            if declared != actual:
                raise ValueError(
                    f"constraint_group_membership for {parameter.parameter_id} disagrees with constraints; "
                    f"declared={sorted(declared)}, actual={sorted(actual)}"
                )
        if self.sampling_method == "imported_matrix":
            if not self.imported_matrix_path or not self.imported_matrix_sha256 or not self.imported_column_mapping:
                raise ValueError(
                    "imported_matrix requires imported_matrix_path, hash, and column mapping"
                )
            if self.existing_case_paths is not None:
                raise ValueError("imported_matrix forbids existing_case_paths")
            mapped_ids = [mapping.parameter_id for mapping in self.imported_column_mapping]
            column_names = [mapping.column_name for mapping in self.imported_column_mapping]
            if len(mapped_ids) != len(set(mapped_ids)):
                raise ValueError("imported column parameter IDs must be unique")
            if len(column_names) != len(set(column_names)):
                raise ValueError("imported column names must be unique")
            if set(mapped_ids) != known:
                raise ValueError("imported column mapping must cover every parameter exactly")
            parameters_by_id = {parameter.parameter_id: parameter for parameter in self.parameters}
            for mapping in self.imported_column_mapping:
                if mapping.entered_unit != parameters_by_id[mapping.parameter_id].entered_unit:
                    raise ValueError(
                        f"imported unit disagrees with parameter {mapping.parameter_id}"
                    )
        elif self.sampling_method == "existing_cases":
            if not self.existing_case_paths:
                raise ValueError("existing_cases requires existing_case_paths")
            if (
                self.imported_matrix_path is not None
                or self.imported_matrix_sha256 is not None
                or self.imported_column_mapping is not None
            ):
                raise ValueError("existing_cases forbids imported matrix fields")
            if self.parameters:
                raise ValueError("existing_cases forbids generated parameter definitions")
            if self.sample_count != len(self.existing_case_paths):
                raise ValueError("existing_cases sample_count must match existing_case_paths")
        else:
            if not self.parameters:
                raise ValueError(f"{self.sampling_method} requires parameter definitions")
            if (
                self.imported_matrix_path is not None
                or self.imported_matrix_sha256 is not None
                or self.imported_column_mapping is not None
                or self.existing_case_paths is not None
            ):
                raise ValueError(
                    f"{self.sampling_method} forbids imported-matrix and existing-case fields"
                )
            for parameter in self.parameters:
                if self.sampling_method == "grid" and (
                    parameter.values is None and parameter.categories is None
                ):
                    raise ValueError("grid parameters require values or categories")
                if self.sampling_method in {"random", "latin_hypercube"} and (
                    parameter.range is None and parameter.categories is None
                ):
                    raise ValueError(
                        f"{self.sampling_method} parameters require a range or categories"
                    )
        return self


class ConstraintOutcome(StrictModel):
    constraint_id: NonEmptyStr
    passed: bool
    detail: str | None = None

    @model_validator(mode="after")
    def require_failure_detail(self) -> "ConstraintOutcome":
        if not self.passed and not self.detail:
            raise ValueError("failed constraint outcome requires exact detail")
        return self


class GeneratedSampleRecord(StrictModel):
    study_id: NonEmptyStr
    sample_id: NonEmptyStr
    baseline_case_sha256: Sha256
    input_parameter_vector: dict[str, JsonValue]
    canonical_parameter_vector: dict[str, JsonValue]
    constraint_outcomes: tuple[ConstraintOutcome, ...]
    generation_outcome: Literal["generated", "rejected", "duplicate"]
    case_path: str | None = None
    case_sha256: Sha256 | None = None
    scientific_fingerprint: Sha256 | None = None
    duplicate_of_sample_id: str | None = None
    deliberate_replicate: bool
    validation_status: Literal["not_checked", "ready", "blocked"]
    validation_receipt_path: str | None = None
    run_id: str | None = None
    completion_state: str | None = None
    qc_state: str | None = None

    @model_validator(mode="after")
    def require_generated_case_identity(self) -> "GeneratedSampleRecord":
        if self.generation_outcome == "generated" and (
            self.case_path is None or self.case_sha256 is None
        ):
            raise ValueError("generated sample requires case path and hash")
        if self.generation_outcome == "duplicate" and self.duplicate_of_sample_id is None:
            raise ValueError("duplicate sample requires duplicate_of_sample_id")
        if self.generation_outcome == "rejected" and not any(
            not outcome.passed for outcome in self.constraint_outcomes
        ):
            raise ValueError("rejected sample requires a failed constraint outcome")
        if self.deliberate_replicate and self.duplicate_of_sample_id is None:
            raise ValueError("deliberate replicate requires duplicate_of_sample_id")
        if self.validation_status == "ready" and (
            self.scientific_fingerprint is None or self.validation_receipt_path is None
        ):
            raise ValueError("ready sample requires scientific fingerprint and validation receipt")
        return self


class StudyManifest(StrictModel):
    study_manifest_schema_version: Literal["1.0"]
    study_id: NonEmptyStr
    study_name: NonEmptyStr
    created_at_utc: AwareDatetime
    finalised_at_utc: AwareDatetime | None
    specification_sha256: Sha256
    generator_version: NonEmptyStr
    sampling_method: Literal[
        "grid", "random", "latin_hypercube", "imported_matrix", "existing_cases"
    ]
    seed: int = Field(ge=0)
    samples: tuple[GeneratedSampleRecord, ...] = Field(min_length=1)
    required_outputs: tuple[NonEmptyStr, ...]
    validity_domain: dict[str, JsonValue]
    dataset_exports: tuple[ArtifactIdentity, ...]
    ready: bool

    @model_validator(mode="after")
    def require_unique_samples(self) -> "StudyManifest":
        sample_ids = [sample.sample_id for sample in self.samples]
        if len(sample_ids) != len(set(sample_ids)):
            raise ValueError("sample_id values must be unique")
        if any(sample.study_id != self.study_id for sample in self.samples):
            raise ValueError("every sample must reference the manifest study_id")
        if self.finalised_at_utc is not None and self.finalised_at_utc < self.created_at_utc:
            raise ValueError("finalised_at_utc cannot precede created_at_utc")
        if self.ready:
            if self.finalised_at_utc is None:
                raise ValueError("ready study manifest requires finalised_at_utc")
            not_ready = [
                sample.sample_id
                for sample in self.samples
                if sample.generation_outcome != "rejected"
                and sample.validation_status != "ready"
            ]
            if not_ready:
                raise ValueError(f"ready study contains unvalidated samples: {not_ready}")
        return self

"""Strict source models for batch-runner Design of Experiments specifications."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


TARGET_KINDS = Literal[
    "temperature", "pressure", "redox_pe", "co2_fugacity", "co2_initial_amount",
    "brine_species_amount", "brine_element_amount", "mineral_initial_amount",
    "mineral_surface_area", "solver_duration", "solver_max_internal_steps",
    "fixed_dt", "adaptive_dt_initial", "adaptive_dt_min", "adaptive_dt_max",
    "adaptive_growth_factor", "adaptive_shrink_factor", "adaptive_max_retries",
    "error_dt_initial", "error_dt_min", "error_dt_max", "error_safety_factor",
    "error_growth_factor", "error_shrink_factor", "solver_failure_shrink_factor",
    "error_max_retries", "richardson_temporal_order", "richardson_relative_tolerance",
    "controlled_mineral_absolute_tolerance", "controlled_mineral_reference_floor",
    "hard_exhaustion_amount_tolerance", "hard_exhaustion_time_tolerance",
    "hard_exhaustion_restart_dt", "hard_exhaustion_max_localizations",
    "soft_timestep_cap_factor", "soft_max_pH_change", "soft_secondary_mineral_appearance",
    "soft_max_reaction_rate_relative_change", "soft_reaction_rate_floor",
    "pk_lgk", "pk_activation_energy", "pk_p", "pk_q", "pk_catalyst_power",
    "kinec_sigma", "kinec_A", "kinec_E", "kinec_n", "kinec_Kc",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Target(StrictModel):
    kind: TARGET_KINDS
    species: str | None = None
    element: str | None = None
    mineral: str | None = None
    record: str | None = None
    mechanism: str | None = None
    catalyst_property: str | None = None
    term: str | None = None

    @model_validator(mode="after")
    def validate_selectors(self) -> "Target":
        exact: dict[str, set[str]] = {
            "brine_species_amount": {"species"},
            "brine_element_amount": {"element"},
            "mineral_initial_amount": {"mineral"},
            "mineral_surface_area": {"mineral"},
            "controlled_mineral_absolute_tolerance": {"mineral"},
            "controlled_mineral_reference_floor": {"mineral"},
            "pk_lgk": {"record", "mechanism"},
            "pk_activation_energy": {"record", "mechanism"},
            "pk_p": {"record", "mechanism"},
            "pk_q": {"record", "mechanism"},
            "pk_catalyst_power": {"record", "mechanism", "catalyst_property"},
            "kinec_sigma": {"mineral"},
            "kinec_A": {"mineral", "term"},
            "kinec_E": {"mineral", "term"},
            "kinec_n": {"mineral", "term"},
            "kinec_Kc": {"mineral", "term"},
        }
        required = exact.get(self.kind, set())
        fields = {
            "species": self.species, "element": self.element, "mineral": self.mineral,
            "record": self.record, "mechanism": self.mechanism,
            "catalyst_property": self.catalyst_property, "term": self.term,
        }
        present = {key for key, value in fields.items() if value is not None}
        if present != required:
            raise ValueError(
                f"target {self.kind} requires selectors {sorted(required)} and forbids others; "
                f"received {sorted(present)}"
            )
        for key in required:
            if not str(fields[key]).strip():
                raise ValueError(f"target selector {key} must be non-empty")
        return self


class ExplicitValues(StrictModel):
    kind: Literal["explicit_values"]
    values: list[float | int] = Field(min_length=1)
    entered_unit: str | None = None


class DiscreteUniform(StrictModel):
    kind: Literal["discrete_uniform"]
    values: list[float | int] = Field(min_length=1)
    entered_unit: str | None = None


class Uniform(StrictModel):
    kind: Literal["uniform"]
    lower: float
    upper: float
    entered_unit: str | None = None

    @model_validator(mode="after")
    def validate_bounds(self) -> "Uniform":
        if not self.lower < self.upper:
            raise ValueError("uniform requires lower < upper")
        return self


class LogUniform(StrictModel):
    kind: Literal["log_uniform"]
    lower: float
    upper: float
    entered_unit: str | None = None

    @model_validator(mode="after")
    def validate_bounds(self) -> "LogUniform":
        if not (0.0 < self.lower < self.upper):
            raise ValueError("log_uniform requires 0 < lower < upper")
        return self


class ImportedColumn(StrictModel):
    kind: Literal["imported_column"]
    column: str = Field(min_length=1)
    entered_unit: str | None = None


SamplingDefinition = Annotated[
    ExplicitValues | DiscreteUniform | Uniform | LogUniform | ImportedColumn,
    Field(discriminator="kind"),
]


class ReportedProvenance(StrictModel):
    kind: Literal["reported"]
    source_identifier: str = Field(min_length=1)
    source_locator: str = Field(min_length=1)
    reported_value_or_range: str | float | int | list[float | int]
    reported_unit: str | None = None
    applicability_domain: str = Field(min_length=1)
    interpretation: str = Field(min_length=1)
    distribution_rationale: str | None = None


class DerivedProvenance(StrictModel):
    kind: Literal["derived"]
    source_identifiers: list[str] = Field(min_length=1)
    formula: str = Field(min_length=1)
    constants: list[dict] = Field(default_factory=list)
    applicability_domain: str = Field(min_length=1)
    distribution_rationale: str | None = None


class UserDefinedProvenance(StrictModel):
    kind: Literal["user_defined"]
    justification: str = Field(min_length=1)
    applicability_domain: str = Field(min_length=1)
    distribution_rationale: str | None = None


Provenance = Annotated[
    ReportedProvenance | DerivedProvenance | UserDefinedProvenance,
    Field(discriminator="kind"),
]


class ParameterSpec(StrictModel):
    parameter_id: str = Field(min_length=1)
    target: Target
    sampling: SamplingDefinition
    provenance: Provenance

    @model_validator(mode="after")
    def require_distribution_rationale(self) -> "ParameterSpec":
        if not self.provenance.distribution_rationale:
            raise ValueError("parameter provenance requires distribution_rationale")
        return self


class ConstraintLiteral(StrictModel):
    value: float | int | str
    unit: str | None = None


class BoundsConstraint(StrictModel):
    constraint_id: str = Field(min_length=1)
    kind: Literal["bounds"]
    parameter_id: str = Field(min_length=1)
    lower: ConstraintLiteral | None = None
    upper: ConstraintLiteral | None = None
    lower_inclusive: bool = True
    upper_inclusive: bool = True

    @model_validator(mode="after")
    def require_bound(self) -> "BoundsConstraint":
        if self.lower is None and self.upper is None:
            raise ValueError("bounds requires lower and/or upper")
        return self


class AllowedValuesConstraint(StrictModel):
    constraint_id: str = Field(min_length=1)
    kind: Literal["allowed_values"]
    parameter_id: str = Field(min_length=1)
    values: list[float | int | str] = Field(min_length=1)
    unit: str | None = None


class Predicate(StrictModel):
    left_parameter_id: str = Field(min_length=1)
    operator: Literal["eq", "ne", "lt", "le", "gt", "ge", "in", "not_in"]
    right_parameter_id: str | None = None
    value: float | int | str | None = None
    values: list[float | int | str] | None = None
    unit: str | None = None

    @model_validator(mode="after")
    def validate_rhs(self) -> "Predicate":
        choices = sum(
            item is not None for item in (self.right_parameter_id, self.value, self.values)
        )
        if choices != 1:
            raise ValueError("predicate requires exactly one RHS: parameter, value, or values")
        if self.operator in {"in", "not_in"} and self.values is None:
            raise ValueError("in/not_in predicates require values")
        if self.operator not in {"in", "not_in"} and self.values is not None:
            raise ValueError("only in/not_in predicates accept values")
        return self


class ComparisonConstraint(StrictModel):
    constraint_id: str = Field(min_length=1)
    kind: Literal["comparison"]
    predicate: Predicate


class RequiredDependencyConstraint(StrictModel):
    constraint_id: str = Field(min_length=1)
    kind: Literal["required_dependency"]
    if_all: list[Predicate] = Field(min_length=1)
    then_all: list[Predicate] = Field(min_length=1)


class ForbiddenCombinationConstraint(StrictModel):
    constraint_id: str = Field(min_length=1)
    kind: Literal["forbidden_combination"]
    all: list[Predicate] = Field(min_length=1)


Constraint = Annotated[
    BoundsConstraint | AllowedValuesConstraint | ComparisonConstraint |
    RequiredDependencyConstraint | ForbiddenCombinationConstraint,
    Field(discriminator="kind"),
]


class GridSampler(StrictModel):
    kind: Literal["grid"]


class RandomSampler(StrictModel):
    kind: Literal["random"]
    sample_count: int = Field(ge=1)
    seed: int = Field(ge=0)
    max_candidates: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_attempts(self) -> "RandomSampler":
        if self.max_candidates < self.sample_count:
            raise ValueError("max_candidates must be >= sample_count")
        return self


class LatinHypercubeSampler(StrictModel):
    kind: Literal["latin_hypercube"]
    sample_count: int = Field(ge=1)
    seed: int = Field(ge=0)


class SobolSampler(StrictModel):
    kind: Literal["sobol"]
    sample_count: int = Field(ge=1)
    seed: int = Field(ge=0)

    @model_validator(mode="after")
    def require_power_of_two(self) -> "SobolSampler":
        if self.sample_count & (self.sample_count - 1):
            raise ValueError("Sobol sample_count must be a power of two")
        return self


class ImportedMatrixSampler(StrictModel):
    kind: Literal["imported_matrix"]
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


Sampler = Annotated[
    GridSampler | RandomSampler | LatinHypercubeSampler | SobolSampler | ImportedMatrixSampler,
    Field(discriminator="kind"),
]


class BaseCaseRef(StrictModel):
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ExistingCaseRef(StrictModel):
    case_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provenance: Provenance


class GeneratedDesignSpec(StrictModel):
    mode: Literal["generated"]
    name: str = Field(min_length=1)
    base_case: BaseCaseRef
    parameters: list[ParameterSpec] = Field(min_length=1)
    sampler: Sampler
    constraints: list[Constraint] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_uniqueness(self) -> "GeneratedDesignSpec":
        parameter_ids = [item.parameter_id for item in self.parameters]
        if len(parameter_ids) != len(set(parameter_ids)):
            raise ValueError("parameter_id values must be unique")
        semantic_targets = [item.target.model_dump_json() for item in self.parameters]
        if len(semantic_targets) != len(set(semantic_targets)):
            raise ValueError("one semantic target may appear only once")
        constraint_ids = [item.constraint_id for item in self.constraints]
        if len(constraint_ids) != len(set(constraint_ids)):
            raise ValueError("constraint_id values must be unique")
        return self


class ExistingCasesDesignSpec(StrictModel):
    mode: Literal["existing_cases"]
    name: str = Field(min_length=1)
    cases: list[ExistingCaseRef] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_ids(self) -> "ExistingCasesDesignSpec":
        ids = [item.case_id for item in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("case_id values must be unique")
        return self


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader, node, deep=False):
    loader.flatten_mapping(node)
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError(
                f"duplicate YAML key {key!r} at line {key_node.start_mark.line + 1}"
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def load_design_spec(path: str | Path) -> tuple[GeneratedDesignSpec | ExistingCasesDesignSpec, bytes]:
    source = Path(path).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"DoE spec does not exist: {source}")
    source_bytes = source.read_bytes()
    raw = yaml.load(source_bytes.decode("utf-8"), Loader=_UniqueKeyLoader)
    if not isinstance(raw, dict):
        raise ValueError("DoE spec must contain a YAML mapping")
    mode = raw.get("mode")
    if mode == "generated":
        return GeneratedDesignSpec.model_validate(raw), source_bytes
    if mode == "existing_cases":
        return ExistingCasesDesignSpec.model_validate(raw), source_bytes
    raise ValueError("DoE spec mode must be generated or existing_cases")


def verify_sha256(path: str | Path, expected: str) -> str:
    source = Path(path).resolve()
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != expected:
        raise ValueError(f"SHA256 mismatch for {source}: expected {expected}, got {digest}")
    return digest

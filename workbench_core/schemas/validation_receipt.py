"""Validation receipt schema binding preflight evidence to launch inputs."""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, model_validator

from .common import (
    CodeIdentity,
    DependencyIdentity,
    EnvironmentIdentity,
    ArtifactIdentity,
    FrozenStrictModel,
    NonEmptyStr,
    Sha256,
)


VALIDATION_RECEIPT_SCHEMA_VERSION = "1.0"


class PreflightStageResult(FrozenStrictModel):
    stage: NonEmptyStr
    status: Literal["passed", "failed", "warning", "not_run"]
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def match_status_to_evidence(self) -> "PreflightStageResult":
        if self.status == "failed" and not self.errors:
            raise ValueError("failed preflight stage requires at least one error")
        if self.status == "passed" and self.errors:
            raise ValueError("passed preflight stage cannot contain errors")
        return self


class KineticMappingResult(FrozenStrictModel):
    mineral_name: NonEmptyStr
    kinetic_model: NonEmptyStr
    parameter_record: NonEmptyStr | None
    surface_area_present: bool
    mapped: bool
    reason: str | None = None

    @model_validator(mode="after")
    def require_mapping_evidence(self) -> "KineticMappingResult":
        if self.mapped and (self.parameter_record is None or not self.surface_area_present):
            raise ValueError("mapped mineral requires a parameter record and surface area")
        if not self.mapped and not self.reason:
            raise ValueError("unmapped mineral requires a reason")
        return self


class ProcessOutcome(FrozenStrictModel):
    exit_code: int | None
    termination_category: NonEmptyStr
    stderr_log_path: str | None = None


class ValidationReceipt(FrozenStrictModel):
    receipt_schema_version: Literal["1.0"]
    receipt_id: NonEmptyStr
    created_at_utc: AwareDatetime
    case_name: NonEmptyStr | None
    validated_snapshot_sha256: Sha256
    scientific_fingerprint: Sha256 | None
    operational_fingerprint: Sha256
    configuration_schema_version: NonEmptyStr
    runner_version: NonEmptyStr
    worker_protocol_version: NonEmptyStr
    solver_environment_identity: EnvironmentIdentity
    environment_evidence: ArtifactIdentity
    code_identity: CodeIdentity
    dependency_identities: tuple[DependencyIdentity, ...]
    preflight_stage_results: tuple[PreflightStageResult, ...]
    kinetic_mapping_summary: tuple[KineticMappingResult, ...]
    ready: bool
    failed_stage: str | None = None
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    process_outcome: ProcessOutcome | None = None

    @model_validator(mode="after")
    def require_coherent_outcome(self) -> "ValidationReceipt":
        if self.ready and (self.failed_stage is not None or self.errors):
            raise ValueError("ready receipt cannot contain failed_stage or errors")
        if self.ready and self.scientific_fingerprint is None:
            raise ValueError("ready receipt requires a scientific fingerprint")
        if self.ready and any(
            value is None
            for value in (
                self.solver_environment_identity.python_version,
                self.solver_environment_identity.reaktoro_version,
                self.solver_environment_identity.environment_spec_sha256,
                self.solver_environment_identity.package_inventory_sha256,
            )
        ):
            raise ValueError("ready receipt requires a complete solver environment identity")
        if self.ready and self.case_name is None:
            raise ValueError("ready receipt requires the validated case name")
        if self.ready and any(stage.status == "failed" for stage in self.preflight_stage_results):
            raise ValueError("ready receipt cannot contain a failed preflight stage")
        if not self.ready and self.failed_stage is None and not self.errors:
            raise ValueError("failed receipt requires failed_stage or errors")
        if self.ready and any(not mapping.mapped for mapping in self.kinetic_mapping_summary):
            raise ValueError("ready receipt cannot contain an unmapped kinetic mineral")
        for label, values in (
            ("dependency logical names", [item.logical_name for item in self.dependency_identities]),
            ("preflight stages", [item.stage for item in self.preflight_stage_results]),
            ("kinetic minerals", [item.mineral_name for item in self.kinetic_mapping_summary]),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} must be unique")
        return self

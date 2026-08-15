"""Shared strict types for versioned workbench records."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator


Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
NonEmptyStr = Annotated[str, Field(min_length=1)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class FrozenStrictModel(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class DependencyIdentity(FrozenStrictModel):
    logical_name: NonEmptyStr
    sha256: Sha256 | None
    source: NonEmptyStr
    version: str | None = None
    package_build: str | None = None
    hash_unavailable_reason: str | None = None

    @model_validator(mode="after")
    def require_hash_or_reason(self) -> "DependencyIdentity":
        if (self.sha256 is None) == (self.hash_unavailable_reason is None):
            raise ValueError("provide exactly one of sha256 or hash_unavailable_reason")
        if self.sha256 is None and self.source == "reaktoro_package":
            if self.version is None or self.package_build is None:
                raise ValueError(
                    "embedded Reaktoro dependency requires package version and build identity"
                )
        return self


class CodeIdentity(FrozenStrictModel):
    commit: NonEmptyStr
    dirty: bool
    relevant_source_sha256: Sha256


class EnvironmentIdentity(FrozenStrictModel):
    python_version: NonEmptyStr | None
    reaktoro_version: NonEmptyStr | None
    platform: NonEmptyStr
    environment_spec_sha256: Sha256 | None
    package_inventory_sha256: Sha256 | None


class SoftwareIdentity(FrozenStrictModel):
    workbench_version: NonEmptyStr
    python_version: NonEmptyStr
    code_identity: CodeIdentity


class ArtifactIdentity(FrozenStrictModel):
    path: NonEmptyStr
    sha256: Sha256


class QuantityDefinition(FrozenStrictModel):
    quantity_id: NonEmptyStr
    label: NonEmptyStr
    scientific_meaning: NonEmptyStr
    unit: NonEmptyStr
    value_type: NonEmptyStr
    sign_domain: NonEmptyStr
    extent: NonEmptyStr
    time_semantics: NonEmptyStr
    source_file: NonEmptyStr
    source_column: NonEmptyStr
    source_output_schema_version: NonEmptyStr


def utc_now() -> datetime:
    return datetime.now(timezone.utc)

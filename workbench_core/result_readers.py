"""Read immutable Objective 1 result packages without Reaktoro."""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import pandas as pd


SUPPORTED_OUTPUT_SCHEMAS = {"objective1_audit_v4"}


@dataclass(frozen=True)
class QuantityDescriptor:
    quantity_id: str
    label: str
    scientific_meaning: str
    unit: str
    value_type: str
    sign_domain: str
    extensive_or_intensive: str
    time_semantics: str
    source_file: str
    source_column: str
    source_output_schema_version: str
    interpolation_policy: str = "forbidden"


@dataclass(frozen=True)
class PackageStatus:
    simulation_completed: bool
    output_completeness: str
    interpretation_supported: bool
    reason: str


class ResultPackage:
    """Read-only adapter for one saved runner output directory."""

    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.manifest = _read_json(self.path / "manifest.json")
        self.diagnostics = _read_json(self.path / "diagnostics.json")
        self.run_record = _read_json(self.path / "run_record.json") or _read_json(
            self.path.parent / "run_record.json"
        )
        manifest_schema = self.manifest.get("output_schema_version")
        diagnostics_schema = self.diagnostics.get("output_schema_version")
        self.schema_version = str(
            f"schema_mismatch:{manifest_schema!r}:{diagnostics_schema!r}"
            if manifest_schema and diagnostics_schema and manifest_schema != diagnostics_schema
            else manifest_schema or diagnostics_schema or "unsupported"
        )

    @property
    def supported(self) -> bool:
        return self.schema_version in SUPPORTED_OUTPUT_SCHEMAS

    @property
    def status(self) -> PackageStatus:
        managed_completeness = self.run_record.get("output_completeness", {})
        completion_evidence = [
            value
            for value in (
                self.diagnostics.get("simulation_completed"),
                self.manifest.get("run_identity", {}).get("simulation_completed"),
                self.run_record.get("state") == "completed" if self.run_record.get("state") else None,
            )
            if isinstance(value, bool)
        ]
        completion_conflict = len(set(completion_evidence)) > 1
        completed = bool(completion_evidence) and all(completion_evidence)
        completeness_evidence = [
            str(value)
            for value in (
                managed_completeness.get("status") if isinstance(managed_completeness, dict) else managed_completeness,
                self.diagnostics.get("output_completeness", {}).get("status"),
            )
            if value
        ]
        completeness_conflict = len(set(completeness_evidence)) > 1
        completeness = completeness_evidence[0] if completeness_evidence else (
            "complete" if completed else "unknown"
        )
        managed_state = self.run_record.get("state") or self.run_record.get("termination_category")
        managed_complete = managed_state in {None, "completed"}
        supported = self.supported and completed and completeness == "complete" and managed_complete
        if completion_conflict:
            reason = "conflicting simulation-completion evidence"
        elif completeness_conflict:
            reason = "conflicting output-completeness evidence"
        elif not self.supported:
            reason = f"unsupported output schema: {self.schema_version}"
        elif not completed:
            reason = "simulation is incomplete"
        elif completeness != "complete":
            reason = f"output package is {completeness}"
        elif not managed_complete:
            reason = f"managed run state is {managed_state}"
        else:
            reason = "supported completed package"
        return PackageStatus(
            completed,
            completeness,
            supported and not completion_conflict and not completeness_conflict,
            reason,
        )

    def inventory(self) -> list[str]:
        return sorted(
            item.relative_to(self.path).as_posix()
            for item in self.path.rglob("*")
            if item.is_file()
        )

    def raw_artifacts(self) -> Iterator[Path]:
        yield from sorted(item for item in self.path.rglob("*") if item.is_file())

    @property
    def run_id(self) -> str | None:
        value = self.run_record.get("run_id") or self.manifest.get("run_identity", {}).get(
            "run_id"
        )
        return str(value) if value else None

    @property
    def scientific_fingerprint(self) -> str | None:
        value = self.run_record.get("scientific_fingerprint") or self.manifest.get(
            "scientific_fingerprint"
        )
        return str(value) if value else None

    def artifact_sha256(self, filename: str) -> str:
        digest = hashlib.sha256()
        with self._file(filename).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def table_columns(self, filename: str) -> list[str]:
        path = self._file(filename)
        with path.open(newline="", encoding="utf-8") as stream:
            return next(csv.reader(stream), [])

    def read_table(
        self,
        filename: str,
        *,
        columns: list[str] | None = None,
        nrows: int | None = None,
        allow_incomplete: bool = False,
    ) -> pd.DataFrame:
        self._require_supported()
        if not allow_incomplete and not self.status.interpretation_supported:
            raise ValueError(self.status.reason)
        return pd.read_csv(self._file(filename), usecols=columns, nrows=nrows)

    def iter_table(
        self,
        filename: str,
        *,
        columns: list[str] | None = None,
        chunksize: int = 50_000,
        allow_incomplete: bool = False,
    ) -> Iterator[pd.DataFrame]:
        self._require_supported()
        if not allow_incomplete and not self.status.interpretation_supported:
            raise ValueError(self.status.reason)
        yield from pd.read_csv(self._file(filename), usecols=columns, chunksize=chunksize)

    def quantity_descriptors(self, filename: str = "timeseries.csv") -> dict[str, QuantityDescriptor]:
        self._require_supported()
        descriptors = (
            _descriptor(column, filename, self.schema_version)
            for column in self.table_columns(filename)
        )
        return {
            descriptor.quantity_id: descriptor
            for descriptor in descriptors
            if descriptor is not None
        }

    def _file(self, filename: str) -> Path:
        path = self.path / filename
        if not path.is_file():
            raise FileNotFoundError(f"result artifact does not exist: {path}")
        return path

    def _require_supported(self) -> None:
        if not self.supported:
            raise ValueError(
                f"output schema {self.schema_version!r} is visible as raw artifacts but is not interpretable"
            )


def y_log_allowed(descriptor: QuantityDescriptor, values: pd.Series) -> bool:
    if descriptor.sign_domain in {"signed", "logarithmic", "pH"}:
        return False
    numeric = pd.to_numeric(values, errors="coerce")
    return bool(numeric.notna().all() and (numeric > 0).all())


def time_log_allowed(values: pd.Series) -> bool:
    numeric = pd.to_numeric(values, errors="coerce")
    return bool(numeric.notna().all() and (numeric > 0).all())


def _descriptor(column: str, filename: str, version: str) -> QuantityDescriptor | None:
    if column == "time_s":
        return _quantity(column, "Time", "accepted simulation time", "s", "nonnegative", "intensive", filename, version)
    if column == "time_days":
        return _quantity(column, "Time", "accepted simulation time", "day", "nonnegative", "intensive", filename, version)
    if column == "pH":
        return _quantity(column, "pH", "aqueous pH", "dimensionless", "pH", "intensive", filename, version)
    if column == "ionic_strength_molal":
        return _quantity(column, "Ionic strength", "aqueous ionic strength", "mol/kgw", "nonnegative", "intensive", filename, version)
    if column == "alkalinity_eq_per_l":
        return _quantity(column, "Alkalinity", "aqueous alkalinity", "eq/L", "signed", "intensive", filename, version)
    prefix, separator, name = column.partition("::")
    definitions = {
        "species_amount_mol": ("Species amount", "saved aqueous species amount", "mol", "nonnegative", "extensive", "forbidden"),
        "species_molality_mol_kgw": ("Species molality", "saved aqueous species molality", "mol/kgw", "nonnegative", "intensive", "forbidden"),
        "mineral_amount_mol": ("Mineral amount", "saved mineral amount", "mol", "nonnegative", "extensive", "forbidden"),
        "mineral_delta_mol": ("Mineral change", "saved mineral amount change", "mol", "signed", "extensive", "forbidden"),
        "saturation_index": ("Saturation index", "log10 saturation ratio", "dimensionless", "logarithmic", "intensive", "forbidden"),
    }
    if separator and prefix in definitions:
        label, meaning, unit, domain, extent, interpolation = definitions[prefix]
        return QuantityDescriptor(
            column,
            f"{label}: {name}",
            meaning,
            unit,
            "float",
            domain,
            extent,
            "accepted_state",
            filename,
            column,
            version,
            interpolation,
        )
    return None


def _quantity(
    quantity_id: str,
    label: str,
    meaning: str,
    unit: str,
    domain: str,
    extent: str,
    filename: str,
    version: str,
) -> QuantityDescriptor:
    return QuantityDescriptor(
        quantity_id,
        label,
        meaning,
        unit,
        "float",
        domain,
        extent,
        "accepted_state",
        filename,
        quantity_id,
        version,
    )


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    return value if isinstance(value, dict) else {}

"""Compatibility-gated comparisons of immutable saved result packages."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd

from workbench_core.result_readers import QuantityDescriptor, ResultPackage
from workbench_core.fingerprints import sha256_file
from workbench_core.persistence import atomic_write_json, require_path_outside_roots
from workbench_core.schemas.common import ArtifactIdentity, SoftwareIdentity, utc_now
from workbench_core.schemas.comparison_spec import ComparisonSpec, TimeTolerance


def compatibility_gate(
    packages: list[ResultPackage],
    quantity_id: str,
    *,
    require_complete: bool = True,
) -> dict[str, Any]:
    if len(packages) < 2:
        return {
            "compatible": False,
            "errors": ["comparison requires at least two result packages"],
            "warnings": [],
            "quantity_id": quantity_id,
            "native_domains": [],
            "native_overlap": None,
            "scientific_fingerprints": {},
            "scientific_input_differences": {},
            "provenance_differences": {},
            "sources": [],
        }
    errors: list[str] = []
    warnings: list[str] = []
    descriptors: list[QuantityDescriptor] = []
    domains: list[dict[str, Any]] = []
    fingerprints: dict[str, str | None] = {}
    sources: list[dict[str, Any]] = []
    managed_ids = [str(package.run_id) for package in packages if package.run_id is not None]
    if len(managed_ids) != len(set(managed_ids)):
        errors.append("comparison source run IDs must be unique")
    for package in packages:
        identity = package.run_id or _unmanaged_identity(package)
        fingerprints[identity] = package.scientific_fingerprint
        try:
            descriptor = package.quantity_descriptors().get(quantity_id) if package.supported else None
        except (FileNotFoundError, OSError, ValueError):
            descriptor = None
        sources.append(
            {
                "run_id": identity,
                "path": str(package.path),
                "unit": descriptor.unit if descriptor else None,
                "output_completeness": package.status.output_completeness,
                "accepted_steps": package.diagnostics.get("number_of_accepted_steps"),
                "rejected_steps": package.diagnostics.get("number_of_rejected_steps"),
                "internal_attempts": package.diagnostics.get("number_of_internal_attempts"),
                "final_time_s": package.diagnostics.get("final_time_reached_s"),
            }
        )
        if not package.supported:
            errors.append(f"{package.path}: unsupported schema {package.schema_version}")
            continue
        if require_complete and not package.status.interpretation_supported:
            errors.append(f"{package.path}: {package.status.reason}")
        try:
            columns = package.table_columns("timeseries.csv")
        except (FileNotFoundError, OSError, ValueError) as error:
            errors.append(f"{package.path}: source artifact unavailable: {error}")
            continue
        if "time_s" not in columns:
            errors.append(f"{package.path}: timeseries.csv has no time_s column")
            continue
        declared = set(package.manifest.get("output_files", ()))
        if declared and "timeseries.csv" not in declared:
            errors.append(f"{package.path}: timeseries.csv is not declared in the package inventory")
            continue
        if descriptor is None:
            errors.append(f"{package.path}: quantity unavailable: {quantity_id}")
        else:
            descriptors.append(descriptor)
            try:
                times = package.read_table(
                    "timeseries.csv", columns=["time_s"], allow_incomplete=True
                )["time_s"].dropna()
            except (OSError, ValueError, KeyError) as error:
                errors.append(f"{package.path}: native time domain unavailable: {error}")
            else:
                if times.empty:
                    errors.append(f"{package.path}: native time domain is empty")
                elif not pd.to_numeric(times, errors="coerce").map(math.isfinite).all():
                    errors.append(f"{package.path}: native time domain contains non-finite values")
                elif not times.is_monotonic_increasing or times.duplicated().any():
                    errors.append(f"{package.path}: native accepted times are not strictly monotonic")
                else:
                    domains.append(
                        {
                            "source": identity,
                            "minimum_s": float(times.min()),
                            "maximum_s": float(times.max()),
                        }
                    )
    if descriptors:
        reference = descriptors[0]
        for descriptor in descriptors[1:]:
            if (
                descriptor.scientific_meaning,
                descriptor.unit,
                descriptor.time_semantics,
            ) != (
                reference.scientific_meaning,
                reference.unit,
                reference.time_semantics,
            ):
                errors.append(f"incompatible quantity descriptor: {descriptor.quantity_id}")
    overlap = None
    if len(domains) == len(packages) and domains:
        overlap = {
            "minimum_s": max(item["minimum_s"] for item in domains),
            "maximum_s": min(item["maximum_s"] for item in domains),
        }
        if overlap["minimum_s"] > overlap["maximum_s"]:
            errors.append("source runs have no overlapping native time domain")
    known_fingerprints = {value for value in fingerprints.values() if value}
    if len(known_fingerprints) > 1:
        warnings.append("scientific fingerprints differ; inspect provenance before interpretation")
    if any(value is None for value in fingerprints.values()):
        warnings.append("one or more source packages lack a scientific fingerprint")
    return {
        "compatible": not errors,
        "errors": errors,
        "warnings": warnings,
        "quantity_id": quantity_id,
        "native_domains": domains,
        "native_overlap": overlap,
        "scientific_fingerprints": fingerprints,
        "scientific_input_differences": _section_differences(
            packages, lambda package: package.manifest.get("input_snapshot") or {}
        ),
        "provenance_differences": _section_differences(
            packages,
            lambda package: {
                "traceability": package.manifest.get("traceability") or {},
                "source_case_sha256": package.run_record.get("source_case", {}).get("sha256"),
                "snapshot_sha256": package.run_record.get("snapshot_sha256"),
            },
        ),
        "sources": sources,
    }


def compare_native(
    packages: list[ResultPackage],
    quantity_id: str,
    *,
    mode: str = "native_accepted_grids",
    common_time_tolerance_s: float = 0.0,
) -> pd.DataFrame:
    gate = compatibility_gate(packages, quantity_id)
    if not gate["compatible"]:
        raise ValueError("; ".join(gate["errors"]))
    frames = []
    for package in packages:
        frame = package.read_table("timeseries.csv", columns=["time_s", quantity_id])
        values = pd.to_numeric(frame[quantity_id], errors="coerce")
        if not values.map(math.isfinite).all():
            raise ValueError(f"{package.path}: comparison quantity contains missing or non-finite values")
        frame[quantity_id] = values
        frame.insert(0, "run_path", str(package.path))
        frames.append(frame)
    if mode == "native_accepted_grids":
        return pd.concat(frames, ignore_index=True)
    if mode == "initial_state":
        selected = [_endpoint(frame, 0.0, "initial") for frame in frames]
        return _endpoint_differences(pd.concat(selected, ignore_index=True), quantity_id)
    if mode == "final_state":
        durations = [_requested_duration(package) for package in packages]
        if not all(math.isclose(value, durations[0], abs_tol=1e-12, rel_tol=0.0) for value in durations[1:]):
            raise ValueError("final-state comparison requires one common requested endpoint")
        selected = [
            _endpoint(frame, duration, "final")
            for duration, frame in zip(durations, frames)
        ]
        return _endpoint_differences(pd.concat(selected, ignore_index=True), quantity_id)
    if mode != "exact_common_timestamps":
        raise ValueError(f"unsupported comparison mode: {mode}")
    return _common_time_differences(
        _common_times(frames, quantity_id, common_time_tolerance_s),
        quantity_id,
        str(frames[0]["run_path"].iloc[0]),
    )


def compare_interpolated(
    packages: list[ResultPackage],
    quantity_id: str,
    target_times_s: list[float],
) -> pd.DataFrame:
    gate = compatibility_gate(packages, quantity_id)
    if not gate["compatible"]:
        raise ValueError("; ".join(gate["errors"]))
    descriptor = packages[0].quantity_descriptors()[quantity_id]
    if descriptor.interpolation_policy == "forbidden":
        raise ValueError(
            f"interpolation is disabled for {quantity_id}; no approved variable-class policy exists"
        )
    rows: list[dict[str, Any]] = []
    for package in packages:
        frame = package.read_table("timeseries.csv", columns=["time_s", quantity_id]).dropna()
        minimum, maximum = frame["time_s"].min(), frame["time_s"].max()
        if any(time < minimum or time > maximum for time in target_times_s):
            raise ValueError("interpolation target would extrapolate outside a source domain")
        indexed = frame.set_index("time_s").reindex(
            sorted(set(frame["time_s"]).union(target_times_s))
        )
        values = indexed[quantity_id].interpolate(method="index")
        for time in target_times_s:
            rows.append(
                {
                    "run_path": str(package.path),
                    "time_s": time,
                    quantity_id: values.loc[time],
                    "derived_alignment": "linear_interpolation",
                }
            )
    return pd.DataFrame(rows)


def write_comparison(
    output_dir: str | Path,
    packages: list[ResultPackage],
    quantity_id: str,
    *,
    mode: str = "native_accepted_grids",
    common_time_tolerance_s: float = 0.0,
    software_identity: SoftwareIdentity | dict[str, Any],
) -> tuple[Path, Path]:
    missing_run_ids = [str(package.path) for package in packages if package.run_id is None]
    if missing_run_ids:
        raise ValueError(
            "saved comparisons require durable source run IDs; unmanaged packages: "
            + ", ".join(missing_run_ids)
        )
    run_ids = [str(package.run_id) for package in packages]
    if len(run_ids) != len(set(run_ids)):
        raise ValueError("saved comparison source run IDs must be unique")
    output = require_path_outside_roots(
        output_dir,
        [package.path for package in packages],
    )
    output.mkdir(parents=True, exist_ok=False)
    table = compare_native(
        packages,
        quantity_id,
        mode=mode,
        common_time_tolerance_s=common_time_tolerance_s,
    )
    data_path = output / "comparison.csv"
    table.to_csv(data_path, index=False)
    spec = ComparisonSpec(
        comparison_schema_version="1.0",
        comparison_id=str(uuid4()),
        created_at_utc=utc_now(),
        source_run_ids=tuple(str(package.run_id) for package in packages),
        source_schema_versions={
            str(package.run_id): package.schema_version for package in packages
        },
        selected_quantities=(quantity_id,),
        unit_conversions=(),
        completion_filters=("simulation_completed", "output_completeness=complete"),
        time_alignment_mode=mode,
        common_time_tolerance=(
            TimeTolerance(value=common_time_tolerance_s, unit="s")
            if mode == "exact_common_timestamps"
            else None
        ),
        interpolation_policy=(),
        extrapolation_policy="forbidden",
        excluded_runs=(),
        created_artifacts=(
            ArtifactIdentity(path=data_path.name, sha256=sha256_file(data_path)),
        ),
        software_identity=SoftwareIdentity.model_validate(software_identity),
    )
    spec_path = output / "comparison_spec.json"
    atomic_write_json(spec_path, spec)
    return spec_path, data_path


def reproduce_comparison(
    specification_path: str | Path,
    runs_root: str | Path,
    *,
    output_path: str | Path | None = None,
) -> Path:
    """Recreate a saved comparison table from run IDs and verify any recorded hash."""
    spec_path = Path(specification_path).resolve()
    spec = ComparisonSpec.model_validate_json(spec_path.read_bytes())
    packages_by_id = {}
    for record_path in Path(runs_root).rglob("run_record.json"):
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if record.get("run_id") in spec.source_run_ids:
            packages_by_id[record["run_id"]] = ResultPackage(
                record.get("result_package_path") or record_path.parent / "results"
            )
    missing = sorted(set(spec.source_run_ids) - set(packages_by_id))
    if missing:
        raise FileNotFoundError(f"comparison source run IDs were not found: {missing}")
    packages = [packages_by_id[run_id] for run_id in spec.source_run_ids]
    tolerance = spec.common_time_tolerance.value if spec.common_time_tolerance else 0.0
    table = compare_native(
        packages,
        spec.selected_quantities[0],
        mode=spec.time_alignment_mode,
        common_time_tolerance_s=tolerance,
    )
    destination = Path(output_path).resolve() if output_path else spec_path.parent / "comparison.csv"
    destination = require_path_outside_roots(
        destination,
        [package.path for package in packages],
    )
    table.to_csv(destination, index=False)
    expected = next(
        (artifact.sha256 for artifact in spec.created_artifacts if Path(artifact.path).name == destination.name),
        None,
    )
    if expected is not None and sha256_file(destination) != expected:
        raise ValueError("reproduced comparison hash differs from comparison_spec.json")
    return destination


def _unmanaged_identity(package: ResultPackage) -> str:
    manifest = package.path / "manifest.json"
    return (
        f"unmanaged:{package.artifact_sha256('manifest.json')}"
        if manifest.is_file()
        else "unmanaged:unknown"
    )


def _common_times(
    frames: list[pd.DataFrame], quantity_id: str, tolerance: float
) -> pd.DataFrame:
    if tolerance < 0:
        raise ValueError("common_time_tolerance_s must be non-negative")
    if not math.isfinite(tolerance):
        raise ValueError("common_time_tolerance_s must be finite")
    reference = frames[0]
    rows = []
    for time in reference["time_s"]:
        matches = []
        for frame in frames:
            distances = (frame["time_s"] - time).abs()
            index = distances.idxmin()
            if distances.loc[index] > tolerance:
                break
            matches.append(frame.loc[index])
        else:
            rows.extend(
                {
                    "run_path": match["run_path"],
                    "time_s": match["time_s"],
                    quantity_id: match[quantity_id],
                    "common_reference_time_s": time,
                }
                for match in matches
            )
    if not rows:
        raise ValueError("source runs have no exact common timestamps within the selected tolerance")
    return pd.DataFrame(rows)


def _requested_duration(package: ResultPackage) -> float:
    value = package.manifest.get("time_semantics", {}).get("duration_s")
    if value is None:
        raise ValueError(f"{package.path}: requested duration is unavailable")
    return float(value)


def _endpoint(frame: pd.DataFrame, target_s: float, label: str) -> pd.DataFrame:
    matches = frame.loc[(frame["time_s"] - target_s).abs() <= 1e-12]
    if matches.empty:
        raise ValueError(f"saved timeseries does not contain the requested {label} state at {target_s} s")
    return matches.iloc[[-1]]


def _endpoint_differences(frame: pd.DataFrame, quantity_id: str) -> pd.DataFrame:
    result = frame.copy()
    reference = float(result.iloc[0][quantity_id])
    result["absolute_difference_from_reference"] = result[quantity_id] - reference
    result["relative_difference_from_reference"] = (
        (result[quantity_id] - reference) / reference if reference != 0.0 else None
    )
    return result


def _common_time_differences(
    frame: pd.DataFrame, quantity_id: str, reference_path: str
) -> pd.DataFrame:
    result = frame.copy()
    reference = (
        result.loc[result["run_path"] == reference_path]
        .set_index("common_reference_time_s")[quantity_id]
    )
    baseline = result["common_reference_time_s"].map(reference)
    result["absolute_difference_from_reference"] = result[quantity_id] - baseline
    result["relative_difference_from_reference"] = (
        (result[quantity_id] - baseline) / baseline.where(baseline != 0.0)
    )
    return result


def _section_differences(packages: list[ResultPackage], reader) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    identities = [str(package.run_id or package.path) for package in packages]
    for package in packages:
        identity = str(package.run_id or package.path)
        for field, value in _flatten(reader(package)).items():
            values.setdefault(field, {})[identity] = value
    for by_run in values.values():
        for identity in identities:
            by_run.setdefault(identity, None)
    return {
        field: by_run
        for field, by_run in values.items()
        if len({_stable_value(value) for value in by_run.values()}) > 1
    }


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        result = {}
        for key in sorted(value):
            result.update(_flatten(value[key], f"{prefix}.{key}" if prefix else str(key)))
        return result
    if isinstance(value, list):
        result = {}
        for index, item in enumerate(value):
            result.update(_flatten(item, f"{prefix}[{index}]"))
        return result
    return {prefix: value}


def _stable_value(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)

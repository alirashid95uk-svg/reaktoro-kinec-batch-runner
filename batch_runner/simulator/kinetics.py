"""Load and attach the two supported kinetic-rate models."""

from collections.abc import Mapping
from typing import Any

import reaktoro as rkt
import yaml

from batch_runner.Kinect_Custom_Rates import KinecParams, ReactionRateModelKinec
from batch_runner.config import ResolvedCase


def load_kinetic_parameters(case: ResolvedCase) -> Any | None:
    if not case.config.kinetics.enabled:
        return None
    if case.config.kinetics.model == "palandri_kharaka":
        return rkt.Params.local(str(case.kinetics_path))
    return KinecParams.local(case.kinetics_path)


def parameter_record_names(case: ResolvedCase, params: Any | None) -> set[str]:
    if not case.config.kinetics.enabled:
        return set()
    if case.config.kinetics.model == "kinec":
        return set(params.data)

    with case.kinetics_path.open(encoding="utf-8") as stream:
        data = yaml.safe_load(stream)
    records = (
        data.get("ReactionRateModelParams", {}).get("PalandriKharaka")
        if isinstance(data, Mapping)
        else None
    )
    if not isinstance(records, Mapping):
        raise ValueError(
            "Palandri-Kharaka parameter file requires "
            "ReactionRateModelParams.PalandriKharaka records"
        )

    names: set[str] = set()
    for label, record in records.items():
        if not isinstance(record, Mapping) or not isinstance(record.get("Mineral"), str):
            raise ValueError(f"invalid Palandri-Kharaka mineral record: {label}")
        names.add(record["Mineral"])
        other_names = record.get("OtherNames", [])
        if not isinstance(other_names, list) or not all(
            isinstance(name, str) for name in other_names
        ):
            raise ValueError(f"invalid Palandri-Kharaka OtherNames list: {label}")
        names.update(other_names)
    return names


def build_rate_model(case: ResolvedCase, params: Any, mineral_name: str) -> Any:
    if case.config.kinetics.model == "palandri_kharaka":
        return rkt.ReactionRateModelPalandriKharaka(params)
    return ReactionRateModelKinec(params, mineral_name)


def uses_python_rate_callback(case: ResolvedCase) -> bool:
    return case.config.kinetics.enabled and case.config.kinetics.model == "kinec"

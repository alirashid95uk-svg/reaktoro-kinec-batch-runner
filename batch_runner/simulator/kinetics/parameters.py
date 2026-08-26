"""Load configured kinetic parameters and construct supported rate models.

The runtime supports Reaktoro's local Palandri-Kharaka records and the explicit
custom Kinec implementation.  This module selects between them from resolved
configuration only; it does not parse PHREEQC ``RATES`` blocks, infer mineral
aliases, or supply missing scientific parameters.
"""

from collections.abc import Mapping
from typing import Any

import reaktoro as rkt
import yaml

from batch_runner.config import ResolvedCase
from batch_runner.simulator.kinetics.kinec import KinecParams, ReactionRateModelKinec


def load_kinetic_parameters(case: ResolvedCase) -> Any | None:
    """Load the configured parameter file, or return ``None`` for equilibrium.

    The resolved path is passed to the selected model loader.  Parsing and I/O
    failures propagate so preparation can record the exact failed stage.
    """
    if not case.config.kinetics.enabled:
        return None
    if case.config.kinetics.model == "palandri_kharaka":
        return rkt.Params.local(str(case.kinetics_path))
    return KinecParams.local(case.kinetics_path)


def parameter_record_names(case: ResolvedCase, params: Any | None) -> set[str]:
    """Return mineral names explicitly represented by the loaded parameters.

    Palandri-Kharaka names and declared ``OtherNames`` are read for connection
    validation only.  Invalid record structure raises :class:`ValueError`;
    names are not rewritten or resolved by heuristic matching.
    """
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
    """Construct the selected Reaktoro-compatible rate model for a mineral."""
    if case.config.kinetics.model == "palandri_kharaka":
        return rkt.ReactionRateModelPalandriKharaka(params)
    return ReactionRateModelKinec(params, mineral_name)


def uses_python_rate_callback(case: ResolvedCase) -> bool:
    """Return whether enabled kinetics executes the custom Python callback."""
    return case.config.kinetics.enabled and case.config.kinetics.model == "kinec"

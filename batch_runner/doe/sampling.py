"""Deterministic Grid, Random, Latin-hypercube, Sobol, and imported-matrix sampling."""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Any, Iterable, Iterator

import numpy as np

from .models import GeneratedDesignSpec

SCIPY_QMC_VERSION = "1.16.1"


@dataclass(frozen=True)
class ResolvedParameter:
    parameter_id: str
    data_type: str
    canonical_unit: str
    sampling: dict[str, Any]
    entered_unit: str | None = None
    year_days: float | None = None


def _transform(u: float, sampling: dict[str, Any]) -> float | int:
    kind = sampling["kind"]
    if kind == "uniform":
        return sampling["lower"] + u * (sampling["upper"] - sampling["lower"])
    if kind == "log_uniform":
        return math.exp(
            math.log(sampling["lower"])
            + u * (math.log(sampling["upper"]) - math.log(sampling["lower"]))
        )
    if kind == "discrete_uniform":
        values = sampling["values"]
        return values[int(math.floor(u * len(values)))]
    raise ValueError(f"sampling kind {kind} cannot transform a unit draw")


def grid_vectors(parameters: list[ResolvedParameter]) -> Iterator[list[float | int]]:
    values: list[list[float | int]] = []
    for parameter in parameters:
        if parameter.sampling["kind"] != "explicit_values":
            raise ValueError("grid requires explicit_values for every parameter")
        values.append(list(parameter.sampling["values"]))
    for vector in product(*values):
        yield list(vector)


def random_vectors(
    parameters: list[ResolvedParameter], *, seed: int, max_candidates: int
) -> Iterator[list[float | int]]:
    rng = np.random.Generator(np.random.PCG64(seed))
    for _ in range(max_candidates):
        vector: list[float | int] = []
        for parameter in parameters:
            vector.append(_transform(float(rng.random()), parameter.sampling))
        yield vector


def _require_scipy_1161():
    import scipy
    from scipy.stats import qmc

    if scipy.__version__ != SCIPY_QMC_VERSION:
        raise RuntimeError(
            f"QMC designs require SciPy {SCIPY_QMC_VERSION}; found {scipy.__version__}"
        )
    return qmc


def lhs_vectors(
    parameters: list[ResolvedParameter], *, seed: int, sample_count: int
) -> list[list[float]]:
    for parameter in parameters:
        if parameter.data_type != "float" or parameter.sampling["kind"] not in {
            "uniform", "log_uniform"
        }:
            raise ValueError(
                "Latin Hypercube requires continuous float uniform/log_uniform parameters"
            )
    qmc = _require_scipy_1161()
    unit = qmc.LatinHypercube(
        len(parameters),
        scramble=True,
        strength=1,
        optimization=None,
        rng=np.random.Generator(np.random.PCG64(seed)),
    ).random(n=sample_count)
    return [
        [_transform(float(u), parameter.sampling) for parameter, u in zip(parameters, row)]
        for row in unit
    ]


def sobol_vectors(
    parameters: list[ResolvedParameter], *, seed: int, sample_count: int
) -> list[list[float]]:
    if sample_count < 1 or sample_count & (sample_count - 1):
        raise ValueError("Sobol sample_count must be a power of two")
    for parameter in parameters:
        if parameter.data_type != "float" or parameter.sampling["kind"] not in {
            "uniform", "log_uniform"
        }:
            raise ValueError("Sobol requires continuous float uniform/log_uniform parameters")
    qmc = _require_scipy_1161()
    m = int(math.log2(sample_count))
    unit = qmc.Sobol(
        len(parameters),
        scramble=True,
        bits=64,
        optimization=None,
        rng=np.random.Generator(np.random.PCG64(seed)),
    ).random_base2(m)
    return [
        [_transform(float(u), parameter.sampling) for parameter, u in zip(parameters, row)]
        for row in unit
    ]


def imported_vectors(
    parameters: list[ResolvedParameter], matrix_path: str | Path
) -> list[list[float | int]]:
    with Path(matrix_path).open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError("imported matrix must contain at least one data row")
    vectors: list[list[float | int]] = []
    for row_number, row in enumerate(rows, start=2):
        vector: list[float | int] = []
        for parameter in parameters:
            if parameter.sampling["kind"] != "imported_column":
                raise ValueError("imported_matrix requires imported_column for every parameter")
            column = parameter.sampling["column"]
            if column not in row or row[column] is None or row[column] == "":
                raise ValueError(
                    f"missing imported value for column {column!r} at row {row_number}"
                )
            raw = row[column]
            try:
                value: float | int = int(raw) if parameter.data_type == "int" else float(raw)
            except ValueError as error:
                raise ValueError(
                    f"invalid {parameter.data_type} in imported column {column!r} "
                    f"at row {row_number}: {raw!r}"
                ) from error
            from .targets import convert_unit
            value = convert_unit(
                value,
                parameter.entered_unit,
                parameter.canonical_unit,
                year_days=parameter.year_days,
            )
            vector.append(int(value) if parameter.data_type == "int" else float(value))
        vectors.append(vector)
    return vectors


def fixed_design_vectors(
    spec: GeneratedDesignSpec,
    parameters: list[ResolvedParameter],
    *,
    imported_matrix_path: str | Path | None = None,
) -> Iterable[list[float | int]]:
    sampler = spec.sampler
    if sampler.kind == "grid":
        return grid_vectors(parameters)
    if sampler.kind == "latin_hypercube":
        return lhs_vectors(parameters, seed=sampler.seed, sample_count=sampler.sample_count)
    if sampler.kind == "sobol":
        return sobol_vectors(parameters, seed=sampler.seed, sample_count=sampler.sample_count)
    if sampler.kind == "imported_matrix":
        if imported_matrix_path is None:
            raise ValueError("imported matrix path is required")
        return imported_vectors(parameters, imported_matrix_path)
    raise ValueError("random designs use random_vectors because acceptance controls stopping")

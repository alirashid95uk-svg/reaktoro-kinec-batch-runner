"""Shared strict models and configuration constants."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TimeUnit = Literal[
    "second",
    "seconds",
    "minute",
    "minutes",
    "hour",
    "hours",
    "day",
    "days",
    "year",
    "years",
]
WorkflowMode = Literal[
    "equilibrium_only",
    "closed_kinetics",
    "fixed_fugacity_initial_equilibrium_then_closed_kinetics",
    "fixed_fugacity_during_kinetic_steps",
]
KineticModel = Literal["palandri_kharaka", "kinec"]
DEFAULT_KINETIC_PATHS = {
    "palandri_kharaka": "data/kinetics/PalandriKharaka_local.yaml",
    "kinec": "data/kinetics/kinec_rates_minimal.yaml",
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Amount(StrictModel):
    value: float = Field(ge=0)
    unit: str = Field(min_length=1)


class SurfaceArea(StrictModel):
    value: float = Field(gt=0)
    unit: str = Field(min_length=1)

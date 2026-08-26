"""Define strict configuration primitives shared by the case schema.

The case, reporting, and timestep models build on these types.  This module
owns no simulation decisions: it supplies reusable value shapes, supported
literal values, and the project-relative defaults used while validating YAML.
"""

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
    """Reject unknown configuration fields for every source-schema model."""

    model_config = ConfigDict(extra="forbid")


class Amount(StrictModel):
    """A non-negative substance amount passed to Reaktoro with its unit."""

    value: float = Field(ge=0, description="Non-negative substance amount.")
    unit: str = Field(
        min_length=1,
        description=(
            "Reaktoro-compatible amount unit; compatibility is checked when the "
            "chemical state is constructed."
        ),
    )


class SurfaceArea(StrictModel):
    """A positive reactive surface area attached to a kinetic mineral."""

    value: float = Field(gt=0, description="Positive reactive surface-area value.")
    unit: str = Field(
        min_length=1,
        description=(
            "Reaktoro-compatible surface-area unit; compatibility is checked when "
            "kinetic reactions are constructed."
        ),
    )

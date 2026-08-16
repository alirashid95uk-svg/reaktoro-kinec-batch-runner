"""Stable public API for scientific simulation execution."""

from .kinetics import uses_python_rate_callback
from .results import PreparedSimulation, SimulationResult
from .simulation import prepare_simulation, preflight_case, run_simulation
from .solver import execute_solver

__all__ = [
    "PreparedSimulation",
    "SimulationResult",
    "execute_solver",
    "prepare_simulation",
    "preflight_case",
    "run_simulation",
    "uses_python_rate_callback",
]

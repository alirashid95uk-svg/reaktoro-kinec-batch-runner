"""Public boundary for preparing and executing one resolved batch case.

The runner calls this package after configuration loading and path resolution.
It exposes preparation, preflight, solver execution, and result records while
keeping Reaktoro construction and timestep implementations in focused
subpackages.  Callers must supply a :class:`~batch_runner.config.ResolvedCase`;
this package does not reinterpret source YAML or select scientific defaults.
"""

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

"""Public kinetic-parameter and mineral-connection boundary.

Simulation preparation uses this package to load the explicitly selected
Palandri-Kharaka or custom Kinec parameter source, verify that configured
minerals connect to thermodynamic and kinetic records, and identify runs that
need the Python callback isolation path.  Reaction attachment itself remains
visible in :mod:`batch_runner.simulator.chemistry.system`.
"""

from .mapping import build_kinetic_mapping, require_valid_kinetic_mapping
from .parameters import load_kinetic_parameters, uses_python_rate_callback

__all__ = [
    "build_kinetic_mapping",
    "load_kinetic_parameters",
    "require_valid_kinetic_mapping",
    "uses_python_rate_callback",
]

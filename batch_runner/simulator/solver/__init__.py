"""Public entry point for explicit equilibrium and kinetic solver execution.

The simulation orchestrator supplies a resolved case, constructed Reaktoro
system and state, plus observational callbacks.  The implementation dispatches
to fixed, legacy feasibility-adaptive, or Richardson error-controlled stepping
without allowing output or monitoring code to influence scientific decisions.
"""

from .execution import execute_solver

__all__ = ["execute_solver"]

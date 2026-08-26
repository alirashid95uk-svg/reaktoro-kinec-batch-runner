"""Qt-free scientific core for YAML-defined Reaktoro batch simulations.

Public construction, execution, and output boundaries live in the focused
``config``, ``simulator``, and ``outputs`` packages. User interfaces launch the
core but do not own its scientific behaviour.
"""

OUTPUT_SCHEMA_VERSION = "objective1_audit_v4"

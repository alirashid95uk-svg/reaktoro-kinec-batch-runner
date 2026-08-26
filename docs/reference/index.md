# Python Module Map

The API pages render the current source docstrings with mkdocstrings. The
stable package entry points are the preferred integration boundary; controller
and configuration implementation modules are included where their scientific
or numerical responsibilities need to remain visible.

| Area | Responsibility |
| --- | --- |
| [Configuration](configuration-api.md) | Strict case models, validation, loading, and deterministic resolution |
| [Simulation](simulation-api.md) | Reaktoro construction, preparation, execution, and accepted-state results |
| [Solver controllers](solver-api.md) | Equilibrium, fixed, legacy adaptive, and error-controlled timestep execution |
| [Outputs](outputs-api.md) | Output-package assembly and deterministic scientific tables |
| [Worker protocol](protocol-api.md) | Optional machine-readable progress events and cooperative cancellation |

The [Architecture](../dev/architecture.md) page describes how these modules
connect without duplicating their callable documentation.


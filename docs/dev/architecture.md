# Batch Runner Architecture

`batch_runner` is the authoritative, Qt-free scientific execution package.
Its public package interfaces are intentionally small:

```python
from batch_runner.config import CaseConfig, ResolvedCase, load_case, resolve_case
from batch_runner.simulator import execute_solver, prepare_simulation, preflight_case, run_simulation
from batch_runner.outputs import write_kinetic_mapping, write_outputs
from batch_runner.protocol import ProtocolEmitter, cancellation_requested
```

CLI, Workbench, and legacy launcher code should use these interfaces. Direct
imports from implementation modules are reserved for focused tests and code
inside the same package.

## Package Map

```text
batch_runner/
├── config/                 strict YAML schema, loading, and resolution
│   ├── case.py             scientific case models and cross-feature validation
│   ├── timestep.py         solver workflow and timestep schema
│   ├── reporting.py        postprocessing, validation, and output schema
│   ├── loading.py          duplicate-key and placeholder-safe YAML loading
│   └── resolution.py       ResolvedCase, paths, Decimal schedules, hashes
├── simulator/
│   ├── chemistry/          database, conditions, system, state, observations
│   ├── kinetics/           parameter loading, mapping, Kinec adapter
│   ├── solver/             execution, equilibrium, fixed/adaptive controllers
│   ├── results.py          preparation and result records
│   ├── diagnostics.py      lifecycle diagnostics and failed results
│   └── simulation.py       preparation, streaming, and run orchestration
├── outputs/
│   ├── writer.py           package writing and partial-output policy
│   ├── tables.py           deterministic base CSV schemas and rows
│   ├── audits.py           rate, inventory, budget, and target-audit tables
│   ├── derived.py          explicit derived summaries and dataset rows
│   ├── plots.py            canonical Matplotlib plots
│   └── manifest.py         traceable result manifest
└── protocol.py             versioned worker events and cancellation-file check
```

## Scientific Execution Flow

```text
YAML
→ load and validate CaseConfig
→ resolve paths and timestep schedules into ResolvedCase
→ load the explicit PHREEQC database and kinetic parameters
→ validate mineral mappings
→ build ChemicalSystem and ChemicalState
→ scientifically required initial equilibrium when configured
→ fixed or adaptive solver controller
→ accepted-state observations and lifecycle diagnostics
→ config-controlled output package
```

`solver/execution.py` owns the public `execute_solver(...)` contract and
visible stage order. `solver/runtime.py` holds only the mutable counters,
callbacks, and schedule cursors shared by those stages. Fixed and adaptive
controllers own their respective loops; direct Reaktoro solver calls remain in
`solver/calls.py`. Solver-success handling, rollback, record fields, target
landing, and cancellation boundaries remain explicit.

Dependencies flow from config to simulator to outputs. Simulator code never
imports output writers. `batch_runner` and `workbench_core` contain no Qt;
process ownership remains in `runner.py`, `workbench_core`, `workbench`, and
the preserved legacy launcher according to their existing roles.

Workbench code follows the same responsibility boundaries without changing
its public imports:

```text
workbench_core/operations/     locks, preparation, execution, queues, recovery
workbench/views/pages/         one module per Workbench page plus shared helpers
```

`workbench_core.operations` remains the headless process-control interface.
`workbench.views.pages` remains the presentation interface used by the main
window; Qt does not cross into `batch_runner` or `workbench_core`.

When adding a feature, extend the existing responsible module. Do not add a
registry, backend factory, plugin layer, or simulator class hierarchy.

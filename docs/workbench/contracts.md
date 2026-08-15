# Workbench operational contracts

This document describes the implemented v1 contracts. Strict Pydantic schemas
under `workbench_core/schemas` are authoritative.

## Artifact authority and identity

- Source cases remain user-owned YAML.
- Validation works on an immutable byte-identical snapshot.
- A ready receipt binds snapshot SHA-256, canonical scientific fingerprint,
  operational fingerprint, dependency identities, runner code identity,
  configuration and protocol versions, exact solver environment, all preflight
  stages, and kinetic mappings.
- Prelaunch recomputes the snapshot, dependency, code, scientific, and
  environment identities. Any mismatch blocks execution.
- Prepared runs use fresh `runs/<case>/<UUID>/` directories. Existing output
  directories are never reused.
- A load-time source hash is carried into output provenance, so later source
  mutation cannot be misreported as the executed dependency.

Scientific fingerprints exclude approved operational fields such as output
paths and identifiers but include canonical resolved configuration,
thermodynamic and kinetic identities, relevant runner-code identity, solver
environment identity, and schema version. Operational fingerprints identify
the concrete run operation and path context.

## Record versions and state machines

`run_record.json` uses `run_schema_version: 1.0`. Its lifecycle is validated:

```text
created -> validating -> blocked_preflight | ready
ready -> starting -> running
running -> completed | partial_numerical_failure | solver_failure_at_start
        | cancelled_cleanly | cancel_requested_solver_unresponsive
        | force_terminated | native_crash | controller_failure
        | chemistry_completed_output_incomplete | interrupted_by_host
        | indeterminate
```

Terminal states require a finish time and matching termination category.
`completed` requires complete outputs. Chemistry completion and output
completion remain separate.

Queue records use `queue_schema_version: 1.0`; queue states are `created`,
`ready`, `running`, `paused`, `completed`, and `failed`. Entry states are
`planned`, `queued`, `starting`, `running`, explicit pause/cancel scheduling
requests, `cancelled_before_start`, and `finished`. Entry ID, order, and run ID
must be unique. Every transition is checked before atomic persistence.

Validation receipts, comparison specifications, study specifications and
manifests, dataset manifests, report specifications, and worker events each
use version `1.0`. The active scientific output schema is
`objective1_audit_v4`.

## Worker protocol

Machine events are one JSON object per stdout line and human diagnostics use
stderr. Every event carries protocol version, event type, UTC timestamp, run
ID, case ID, producer, strictly increasing producer sequence, and payload.
Worker-owned events cover environment, stages, validation, mapping, simulation,
progress, checkpoints, warnings, outputs, completion, and failure. Controller-
owned events cover process creation/start/exit, cancel/unresponsive state,
force requests, actual terminate/kill actions, protocol errors, and controller
errors.

Malformed lines, wrong identities, duplicate or decreasing worker sequences,
and unknown event types are recorded as protocol problems and cannot crash the
GUI. Worker and controller events share the durable `events.jsonl` stream but
retain independent producer identities.

## Result and comparison contract

Result readers are immutable, schema-gated, and chunked. Unsupported packages
remain available as raw artifacts but cannot be interpreted. Quantity
descriptors carry identity, label, scientific meaning, unit, value type, sign
domain, extensive/intensive class, accepted-state semantics, source artifact,
source column, source schema, and interpolation policy.

Every derived comparison, dataset, and report target must be outside all
immutable source result-package or source-artifact directories. The headless
writers enforce this before creating a directory, so GUI and CLI callers cannot
write derived files into recorded evidence packages.

Comparison v1 supports native accepted grids, initial state, requested final
state, and exact common timestamps with an explicit tolerance. It checks
schema, completeness, quantity identity, units, time semantics, native-domain
overlap, source inventory, and fingerprint differences. Each export writes
`comparison.csv` and `comparison_spec.json`; `compare-reproduce` resolves the
recorded run IDs and verifies the reproduced CSV hash.

## Study contract

Study v1 supports grid, seeded random, Latin-hypercube, imported-matrix, and
existing-case sources. Only the explicit approved YAML path registry may vary.
Definitions carry type, meaning, entered and canonical units, range/categories,
distribution, transform, provenance requirement, and constraint memberships.
Dependency and composition constraints run before case persistence. Silent
renormalisation and silent repair are prohibited. A full authoritative
preflight is mandatory for the baseline and every generated case. The
append/finalise manifest retains rejected, duplicate, validation, run, and QC
states.

## Dataset contract

Dataset v1 supports final-state, native fixed-time, time-dependent tabular,
trajectory, and separate failure datasets. Features and targets are explicit
quantity descriptors. The gate requires managed run identity, supported and
complete results, authoritative Objective 1 audit success, available finite
quantities, optional validity-domain evidence, explicit QC rules, and an
explicit duplicate policy. Fixed-time selection uses only saved timestamps
within the stated tolerance; interpolation and extrapolation are forbidden.

Splits use deterministic SHA-256 assignment over unioned group identities.
Runs, requested study/scenario groups, and replicate scientific fingerprints
are unioned before assignment. The manifest records run IDs by split and the
separate exclusion ledger. CSV and Parquet hashes are authoritative.

## Reports and migration

Run, diagnosis, comparison, study, and dataset reports are derived only from
explicit saved artifacts. Every report writes Markdown, self-contained HTML,
PDF, and a JSON generation specification with source and output hashes. It does
not embed hidden scientific data.

`Simulation launcher/` and the direct `runner.py` CLI remain unchanged entry
points and regression references. The PySide6 bootstrap is the documented
default only because preflight, resolved scientific payload, mineral mapping,
output schema, numerical CSVs, summaries, and diagnostics were demonstrated
equivalent. Operational identifiers, paths, and timestamps are allowed to
differ. The legacy launcher is not deleted.

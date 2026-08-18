# Complexity Boundary

The canonical architecture and forbidden-complexity rules are in `AGENTS.md`.
Do not maintain a second exhaustive list here.

The practical rule is simple: preserve the visible
YAML -> validation -> system/state -> solver -> observations -> outputs flow and
reuse existing project infrastructure before adding abstraction.

A new abstraction is justified only when it removes real repeated complexity
without hiding scientific settings, units, Reaktoro objects, solver behaviour,
or provenance.

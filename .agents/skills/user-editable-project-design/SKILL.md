---
name: user-editable-project-design
description: Use for cross-module feature or architecture design where placement, ownership, or complexity is genuinely in question. Do not use for routine local feature implementation with an obvious existing module.
---

# User-Editable Project Design

Use this skill when deciding where a feature belongs or when a proposal risks
creating competing architecture.

## Design Rules

- Preserve the existing YAML -> validation -> system/state -> solver ->
  observations -> outputs flow.
- Reuse existing runner, Workbench, study, event, diagnostics, and output
  infrastructure before creating new layers.
- Give each feature one clear configuration owner, one execution owner, and one
  observable output/diagnostic effect.
- Prefer short modules and simple functions.
- Keep direct Reaktoro syntax and scientific settings visible.
- Optional behaviour should be explicitly disableable when it is truly optional.
- Add only focused tests that protect the feature's behaviour.

Avoid plugin managers, hidden registries, generic backend systems, abstract
simulator engines, dynamic imports for core execution, dependency-injection
containers, broad exception swallowing, and silent fallback.

`runner.py` remains orchestration only.

Do not invoke this skill simply because a feature is new. If the correct module
and contract are already obvious, use the domain-specific skill and implement
the smallest clean change.

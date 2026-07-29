---
name: user-editable-project-design
description: Use when designing, adding, or reviewing project features; keep the batch runner readable, explicit, optional-feature aware, and maintainable without Codex.
---

# User-Editable Project Design

The user must be able to understand, modify, and extend this project without
Codex.

## Design Rules

- Prefer short modules and simple Python functions.
- Give every feature one clear config location.
- Give every feature one clear execution module.
- Give every feature one clear output effect.
- Make every optional feature explicitly disableable.
- Use only minimal tests that directly protect the feature being added.
- Do not create large testing loops, broad test harnesses, or excessive test
  infrastructure.
- Keep direct Reaktoro syntax visible.
- Raise specific errors with actionable context.
- Add comments only for scientific, unit, or non-obvious implementation
  decisions.

## Forbidden Patterns

- generic backend system;
- plugin manager;
- hidden registry;
- abstract simulator architecture;
- dynamic imports for core execution;
- dependency injection container;
- broad exception swallowing;
- silent fallback;
- random example configs.

`runner.py` must remain orchestration only. `runner.py` must not contain
scientific logic.

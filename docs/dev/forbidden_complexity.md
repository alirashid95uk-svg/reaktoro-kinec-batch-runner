# Forbidden Complexity

This project is a simple Reaktoro batch simulation runner. Do not add
complexity that hides Reaktoro, scientific settings, or execution flow.

## Forbidden

- generic backend system;
- plugin manager;
- hidden registry;
- abstract simulator architecture;
- dynamic imports for core execution;
- dependency injection container;
- broad exception swallowing;
- silent fallback;
- random example configs.

## Preferred Alternatives

- Use one readable YAML format.
- Use direct `PhreeqcDatabase` and Reaktoro calls.
- Use short modules and simple Python functions.
- Validate explicitly and fail with actionable errors.
- Keep optional behavior explicit and disableable.
- Record scientific provenance and units.

The execution chain is a design guide, not a reason to build complicated
architecture.

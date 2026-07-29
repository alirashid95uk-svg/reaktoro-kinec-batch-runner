# Adding a New Feature

Add features only when they fit the supported batch-simulation scope and have
source-supported scientific inputs.

## Required Process

1. Add one clear config field or block.
2. Add explicit validation, including incompatible and missing-input cases.
3. Add deterministic preprocessing if units or derived quantities are needed.
4. Add execution logic in the correct focused module.
5. Add output or diagnostics logic if the feature changes observable results.
6. Add only minimal tests that directly protect the feature.
7. Update developer documentation and source/provenance notes.

```text
Do not add features by directly editing runner.py.
```

`runner.py` must remain orchestration only. `runner.py` must not contain
scientific logic.

## Required Design Check

Before implementation, state:

- why the feature belongs in batch scope;
- which config block owns it;
- which module executes it;
- which output or diagnostic shows its effect;
- how it is disabled if optional;
- which source supports its scientific values and assumptions;
- which minimal test directly protects it.

Do not create large testing loops, broad test harnesses, or excessive test
infrastructure.

# Adding a New Feature

Use `AGENTS.md` as the project-wide authority. Do not repeat its scientific,
architecture, or verification rules here.

For a routine feature with an obvious existing owner, use the relevant domain
skill and make the smallest clean change.

Use `user-editable-project-design` only when feature ownership or architecture
is genuinely unclear or the proposal could create a competing subsystem.

For a complex feature, keep one compact specification:

- Goal
- Required behaviour
- Interface/configuration
- Acceptance criteria
- Non-goals

Implementation should reuse existing runner, Workbench, study, event,
diagnostics, and output infrastructure where applicable. `runner.py` remains
orchestration only.

Verification follows the risk-based rules in `AGENTS.md`; documentation-only or
local non-behavioural work does not require scientific runtime probes or a full
test suite.

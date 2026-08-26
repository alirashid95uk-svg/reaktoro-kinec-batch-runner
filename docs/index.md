# Reaktoro Batch Runner Documentation

This site is a browsable view of the repository's authoritative Python source
and configuration models. It does not define a second runtime contract.

Use these sections according to the question you need to answer:

- [Architecture](dev/architecture.md) explains the execution boundaries and
  scientific data flow.
- [Python API](reference/index.md) renders module and callable documentation
  directly from source docstrings.
- [Configuration Reference](generated/configuration.md) is generated from the
  Pydantic case models and their validation metadata.
- [CLI Reference](generated/cli.md) is generated from the same parser and
  configuration-reference projection used at runtime.
- [Limitations and Scope](dev/project_scope.md) states what the batch simulator
  does and does not implement.

Scientific inputs, units, defaults, and supported features remain authoritative
only where the runtime schema and implementation define them. Generated pages
make those definitions discoverable; they do not replace validation or
scientific verification.


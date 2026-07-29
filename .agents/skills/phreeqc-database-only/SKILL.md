---
name: phreeqc-database-only
description: Use when implementing or reviewing thermodynamic database configuration and loading; allow only explicit embedded or local PHREEQC-style databases.
---

# PHREEQC Database Only

PHREEQC-style databases only. Load them with `PhreeqcDatabase`.

## Allowed Configurations

```yaml
database:
  source: embedded
  name: phreeqc.dat
```

```yaml
database:
  source: local
  path: data/thermo/Kinec_v3_4.dat
```

## Rules

- Require `database.source` to be exactly `embedded` or `local`.
- Require an explicit embedded database name.
- Require an explicit local database path and verify that it exists before
  loading.
- Use `PhreeqcDatabase(name)` or `PhreeqcDatabase.withName(name)` for an
  embedded database.
- Use `PhreeqcDatabase.fromFile(path)` for a local database.
- Fail loudly on missing, invalid, or contradictory configuration.
- No generic database backend.
- No automatic database selection.
- No fallback database.
- If a local database path fails, stop and report the exact path.

`data/thermo/Kinec_v3_4.dat` is thermodynamic input only. It must not be
treated as runtime kinetic-rate input.

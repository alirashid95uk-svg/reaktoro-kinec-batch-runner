"""Load the explicitly resolved PHREEQC thermodynamic database.

This is the sole runtime database-loading boundary used by simulation
preparation.  It selects either the resolved local file or an exact embedded
Reaktoro database name; no search, format conversion, or fallback occurs.
"""

from typing import Any

import reaktoro as rkt

from batch_runner.config import ResolvedCase


def load_database(case: ResolvedCase) -> Any:
    """Return the configured Reaktoro ``PhreeqcDatabase``.

    Raises:
        RuntimeError: A local PHREEQC file cannot be loaded.
        ValueError: The requested embedded database name is unavailable.
    """
    if case.config.database.source == "local":
        path = str(case.database_path)
        try:
            return rkt.PhreeqcDatabase.fromFile(path)
        except RuntimeError as exc:
            raise RuntimeError(f"failed to load local PHREEQC database: {path}") from exc

    name = case.config.database.name
    if name not in rkt.PhreeqcDatabase.namesEmbeddedDatabases():
        raise ValueError(f"embedded PHREEQC database does not exist: {name}")
    return rkt.PhreeqcDatabase.withName(name)

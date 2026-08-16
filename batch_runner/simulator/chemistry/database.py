"""PHREEQC database loading."""

from typing import Any

import reaktoro as rkt

from batch_runner.config import ResolvedCase


def load_database(case: ResolvedCase) -> Any:
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

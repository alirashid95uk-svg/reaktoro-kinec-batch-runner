"""Reliable ChemicalState snapshots for failed-step rollback."""

from typing import Any

import reaktoro as rkt


def snapshot_state(state: Any) -> Any:
    return rkt.ChemicalState(state)

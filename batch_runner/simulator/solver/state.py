"""Reliable ChemicalState snapshots for future rejected-step rollback."""

from typing import Any

import reaktoro as rkt


def snapshot_state(state: Any) -> Any:
    return rkt.ChemicalState(state)

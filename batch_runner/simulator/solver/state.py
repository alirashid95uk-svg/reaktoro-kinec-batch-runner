"""Create independent Reaktoro ``ChemicalState`` snapshots for rollback.

Every mutating kinetic trial must start from a copy produced here.  Controllers
restore the live state from that snapshot before retrying or reporting a failed
attempt, so rejected trials never advance accepted chemical time.
"""

from typing import Any

import reaktoro as rkt


def snapshot_state(state: Any) -> Any:
    """Return an independent Reaktoro copy of *state* suitable for ``assign``."""
    return rkt.ChemicalState(state)

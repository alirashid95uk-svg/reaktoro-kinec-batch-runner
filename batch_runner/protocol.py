"""Emit the stable machine-readable worker event protocol.

The CLI uses :class:`ProtocolEmitter` when JSONL mode is requested.  Each event
is one compact, immediately flushed stdout line carrying protocol version, run
identity, sequence, UTC timestamp, and payload.  Human terminal presentation
and ``simulation.log`` are separate consumers and must not contaminate this
stream.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO


PROTOCOL_VERSION = "1.0"


class ProtocolEmitter:
    """Write ordered worker events to a text stream.

    Disabled emitters are no-ops.  A broken downstream pipe disables future
    emission instead of terminating chemistry; other serialization or I/O
    errors propagate because they indicate a malformed event or stream failure.
    """

    def __init__(
        self,
        *,
        enabled: bool,
        run_id: str,
        case_id: str,
        stream: TextIO | None = None,
    ) -> None:
        self.enabled = enabled
        self.run_id = run_id
        self.case_id = case_id
        self.stream = stream or sys.stdout
        self.sequence_number = 0

    def emit(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        """Write and flush one versioned JSON object followed by a newline.

        Sequence numbers increase only for enabled emission attempts.  The
        caller owns the event vocabulary and payload schema; this method adds
        the common envelope without mutating the supplied payload.
        """
        if not self.enabled:
            return
        self.sequence_number += 1
        event = {
            "protocol_version": PROTOCOL_VERSION,
            "event_type": event_type,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "case_id": self.case_id,
            "sequence_number": self.sequence_number,
            "producer": "worker",
            "payload": payload or {},
        }
        try:
            json.dump(event, self.stream, separators=(",", ":"))
            self.stream.write("\n")
            self.stream.flush()
        except BrokenPipeError:
            self.enabled = False


def cancellation_requested(cancel_file: Path | None) -> bool:
    """Return whether the controller's cooperative-cancellation sentinel exists.

    The check is observational and does not delete the sentinel.  Callers poll
    it only at safe boundaries; it cannot interrupt a native Reaktoro call.
    """
    return cancel_file is not None and cancel_file.is_file()

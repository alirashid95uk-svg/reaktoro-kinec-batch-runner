"""Versioned JSONL events for optional worker-process monitoring."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO


PROTOCOL_VERSION = "1.0"


class ProtocolEmitter:
    """Write one immediately flushed worker event per stdout line."""

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
    """Return whether the controller's cooperative-cancellation sentinel exists."""
    return cancel_file is not None and cancel_file.is_file()

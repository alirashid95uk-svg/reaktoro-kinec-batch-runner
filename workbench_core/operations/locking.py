"""Exclusive project-controller ownership."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from workbench_core.schemas.common import utc_now


class ProjectControlLock:
    """One OS-released controller lock for a project; no stale PID ownership."""

    def __init__(self, project_root: str | Path):
        self.path = Path(project_root).resolve() / ".workbench" / "control.lock"
        self.token = str(uuid4())
        self._stream = None
        self._borrowed = False

    def __enter__(self) -> "ProjectControlLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        delegated = os.environ.get("REAKTORO_PROJECT_CONTROL_TOKEN")
        if delegated:
            try:
                with self.path.open("rb") as owner_stream:
                    owner_stream.seek(1)
                    owner = json.loads(owner_stream.read().decode("utf-8"))
            except (OSError, ValueError, TypeError):
                owner = {}
            if owner.get("token") == delegated:
                self.token = delegated
                self._borrowed = True
                return self
        stream = self.path.open("a+b")
        try:
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            stream.close()
            raise RuntimeError(
                f"another workbench controller owns the project lock: {self.path}"
            ) from error
        payload = json.dumps(
            {"pid": os.getpid(), "token": self.token, "created_at_utc": utc_now().isoformat()}
        ).encode("utf-8")
        stream.seek(1)
        stream.truncate()
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
        stream.seek(0)
        self._stream = stream
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        if self._borrowed:
            self._borrowed = False
            return
        if self._stream is None:
            return
        try:
            self._stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
        finally:
            self._stream.close()
            self._stream = None

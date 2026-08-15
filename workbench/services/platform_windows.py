"""Small boundary for Windows-only desktop and process-tree operations."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def open_folder(path: str | Path) -> None:
    target = str(Path(path).resolve())
    if os.name == "nt":
        os.startfile(target)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", target])


def force_kill_process_tree(pid: int) -> subprocess.CompletedProcess[str]:
    """Kill an owned process tree and return verified command completion evidence."""
    if pid < 1:
        raise ValueError("pid must be positive")
    if os.name == "nt":
        return subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=15,
            check=False,
        )
    return subprocess.run(
        ["kill", "-KILL", str(pid)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

from __future__ import annotations

import json
import platform
import sys
from pathlib import Path

import reaktoro as rkt


EXPECTED_PYTHON = (3, 11)
EXPECTED_REAKTORO = "2.13.0"


def main() -> None:
    info = {
        "python": platform.python_version(),
        "reaktoro": rkt.__version__,
        "platform": platform.platform(),
    }
    print(json.dumps(info, indent=2))
    if sys.version_info[:2] != EXPECTED_PYTHON:
        raise SystemExit(
            f"expected Python {EXPECTED_PYTHON[0]}.{EXPECTED_PYTHON[1]}, got {platform.python_version()}"
        )
    if rkt.__version__ != EXPECTED_REAKTORO:
        raise SystemExit(f"expected Reaktoro {EXPECTED_REAKTORO}, got {rkt.__version__}")
    output_dir = Path("results")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "environment.json").write_text(
        json.dumps(info, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

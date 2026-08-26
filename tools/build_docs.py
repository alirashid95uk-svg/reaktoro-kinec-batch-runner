"""Generate source-derived reference pages and build the MkDocs site strictly."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GENERATED_DIR = PROJECT_ROOT / "docs" / "generated"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def generate_reference_pages() -> tuple[Path, Path]:
    """Write disposable configuration and CLI pages from their live sources."""

    from batch_runner.cli import render_cli_markdown
    from batch_runner.config.reference import render_markdown_reference

    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    configuration = GENERATED_DIR / "configuration.md"
    cli = GENERATED_DIR / "cli.md"
    configuration.write_text(render_markdown_reference(), encoding="utf-8")
    cli.write_text(render_cli_markdown(), encoding="utf-8")
    return configuration, cli


def main() -> None:
    """Generate reference pages, then fail on any MkDocs build warning."""

    generate_reference_pages()
    subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "--strict"],
        cwd=PROJECT_ROOT,
        check=True,
    )


if __name__ == "__main__":
    main()

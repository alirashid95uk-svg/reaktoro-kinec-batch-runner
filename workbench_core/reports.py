"""Headless reports derived only from saved specifications and artifacts."""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

import markdown
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib import colors
from workbench_core.fingerprints import sha256_file
from workbench_core.persistence import (
    atomic_write_json,
    atomic_write_text,
    require_path_outside_roots,
)
from workbench_core.schemas.common import SoftwareIdentity


REPORT_TYPES = {"run", "diagnosis", "comparison", "study", "dataset"}


def generate_report(
    report_type: str,
    source_artifacts: Iterable[str | Path],
    output_dir: str | Path,
    *,
    title: str | None = None,
    software_identity: dict[str, Any] | None = None,
    report_id: str | None = None,
    created_at_utc: str | None = None,
) -> dict[str, Path]:
    """Create Markdown, self-contained HTML, PDF, and a provenance specification."""
    if report_type not in REPORT_TYPES:
        raise ValueError(f"unsupported report type: {report_type}")
    sources = [Path(item).resolve() for item in source_artifacts]
    missing = [str(path) for path in sources if not path.is_file()]
    if missing:
        raise FileNotFoundError("report source artifact(s) missing: " + ", ".join(missing))
    output = require_path_outside_roots(
        output_dir,
        [path.parent for path in sources],
    )
    output.mkdir(parents=True, exist_ok=False)
    report_title = title or f"{report_type.title()} report"
    source_records = [
        {
            "logical_name": path.name,
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sources
    ]
    body = _markdown_body(report_title, report_type, sources, source_records)
    markdown_path = output / "report.md"
    html_path = output / "report.html"
    pdf_path = output / "report.pdf"
    atomic_write_text(markdown_path, body)
    atomic_write_text(html_path, _html_document(report_title, body))
    _write_pdf(pdf_path, report_title, body)
    spec = {
        "report_schema_version": "1.0",
        "report_id": report_id or str(uuid4()),
        "report_type": report_type,
        "title": report_title,
        "created_at_utc": created_at_utc or datetime.now(timezone.utc).isoformat(),
        "source_artifacts": source_records,
        "generation": {
            "markdown": "Python-Markdown",
            "html": "self-contained HTML",
            "pdf": "ReportLab derivative from report.md",
        },
        "software_identity": SoftwareIdentity.model_validate(software_identity).model_dump(mode="json"),
        "created_artifacts": {},
    }
    for path in (markdown_path, html_path, pdf_path):
        spec["created_artifacts"][path.name] = sha256_file(path)
    spec_path = output / "report_spec.json"
    atomic_write_json(spec_path, spec)
    return {
        "spec": spec_path,
        "markdown": markdown_path,
        "html": html_path,
        "pdf": pdf_path,
    }


def reproduce_report(
    specification: str | Path,
    source_roots: Iterable[str | Path],
    output_dir: str | Path,
) -> dict[str, Path]:
    """Recreate a report by resolving each hash-identified source under explicit roots."""
    spec = json.loads(Path(specification).read_text(encoding="utf-8"))
    if spec.get("report_schema_version") != "1.0":
        raise ValueError("unsupported report specification version")
    roots = [Path(root).resolve() for root in source_roots]
    sources = []
    for record in spec.get("source_artifacts", []):
        matches = {
            path.resolve()
            for root in roots
            for path in root.rglob(str(record["logical_name"]))
            if path.is_file() and sha256_file(path) == record["sha256"]
        }
        if len(matches) != 1:
            raise ValueError(
                f"expected one source matching {record['logical_name']} and {record['sha256']}, found {len(matches)}"
            )
        sources.append(matches.pop())
    return generate_report(
        spec["report_type"],
        sources,
        output_dir,
        title=spec["title"],
        software_identity=spec["software_identity"],
        report_id=spec["report_id"],
        created_at_utc=spec["created_at_utc"],
    )


def _markdown_body(
    title: str,
    report_type: str,
    sources: list[Path],
    records: list[dict[str, Any]],
) -> str:
    lines = [
        f"# {title}",
        "",
        f"Report type: `{report_type}`",
        "",
        "> Scientific scope: saved batch-geochemistry artifacts are not reactive-transport or fracture-sealing evidence.",
        "",
        "## Source artifacts",
        "",
        "| Logical name | SHA-256 | Bytes |",
        "|---|---|---:|",
    ]
    lines.extend(
        f"| {record['logical_name']} | `{record['sha256']}` | {record['size_bytes']} |"
        for record in records
    )
    lines.extend(["", "## Saved evidence", ""])
    for path in sources:
        lines.extend(_artifact_summary(path))
    return "\n".join(lines).rstrip() + "\n"


def _artifact_summary(path: Path) -> list[str]:
    lines = [f"### {path.name}", ""]
    if path.suffix.lower() == ".json":
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            value = None
        if isinstance(value, dict):
            for key in (
                "run_id",
                "termination_category",
                "output_completeness",
                "output_schema_version",
                "simulation_completed",
                "dataset_type",
                "study_id",
                "comparison_id",
            ):
                if key in value:
                    lines.append(f"- {key}: `{value[key]}`")
            lines.append("")
            return lines
    if path.suffix.lower() in {".csv", ".parquet"}:
        lines.extend(
            [
                f"Tabular artifact retained at `{path.name}`; the report does not embed a hidden copy.",
                "",
            ]
        )
    else:
        lines.extend(["Artifact retained without reinterpretation.", ""])
    return lines


def _html_document(title: str, body: str) -> str:
    content = markdown.markdown(body, extensions=["tables", "fenced_code"])
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title>"
        "<style>body{font:16px/1.5 system-ui,sans-serif;max-width:72rem;margin:auto;padding:2rem;}"
        "table{border-collapse:collapse}th,td{border:1px solid #777;padding:.4rem;text-align:left}"
        "code{overflow-wrap:anywhere}</style></head><body>"
        f"{content}</body></html>"
    )


def _write_pdf(path: Path, title: str, body: str) -> None:
    styles = getSampleStyleSheet()
    story = [Paragraph(html.escape(title), styles["Title"]), Spacer(1, 12)]
    table_style = styles["BodyText"].clone("ReportTable")
    table_style.fontSize = 7.5
    table_style.leading = 9
    table_style.wordWrap = "CJK"
    lines = body.splitlines()[1:]
    index = 0
    while index < len(lines):
        raw = lines[index]
        text = raw.strip()
        if text.startswith("|") and index + 1 < len(lines) and lines[index + 1].strip().startswith("|---"):
            rows = [_markdown_table_row(text, table_style)]
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append(_markdown_table_row(lines[index].strip(), table_style))
                index += 1
            table = Table(rows, colWidths=(38 * mm, 105 * mm, 20 * mm), repeatRows=1)
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EDF2")),
                        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#777777")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 4),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ]
                )
            )
            story.extend([table, Spacer(1, 8)])
            continue
        if not text or text.startswith("|---"):
            index += 1
            continue
        if text.startswith("#"):
            level = min(len(text) - len(text.lstrip("#")), 3)
            story.append(Paragraph(html.escape(text.lstrip("# ")), styles[f"Heading{level}"]))
        else:
            cleaned = re.sub(r"[`|]", "", text.lstrip(">- "))
            story.append(Paragraph(html.escape(cleaned), styles["BodyText"]))
        story.append(Spacer(1, 4))
        index += 1
    SimpleDocTemplate(
        str(path),
        pagesize=A4,
        title=title,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
    ).build(
        story,
        onFirstPage=_pdf_footer,
        onLaterPages=_pdf_footer,
        canvasmaker=partial(Canvas, invariant=1),
    )


def _markdown_table_row(line: str, style) -> list[Paragraph]:
    return [Paragraph(html.escape(cell.strip().strip("`")), style) for cell in line.strip("|").split("|")]


def _pdf_footer(canvas, document) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#555555"))
    canvas.drawRightString(A4[0] - 20 * mm, 10 * mm, f"Page {document.page}")
    canvas.restoreState()

"""Normalize source documents into ordered, located blocks.

A block is the unit of provenance: every extracted requirement cites a block id and quotes text that
must exist in that block. Headings build a section path so locators read like "3 Functional
requirements > 3.2 Task titles".
"""

from __future__ import annotations

import io
import re
from typing import Any

import docx
from docx.document import Document as DocxDocument
from docx.table import Table
from docx.text.paragraph import Paragraph

HEADING_RE = re.compile(r"^(heading|title)\s*(\d*)$", re.IGNORECASE)
VTT_TIME = re.compile(r"^\d{2}:\d{2}(:\d{2})?[.,]\d{3}\s+-->\s+")
SPEAKER = re.compile(r"^\s*(?:\[(?P<ts>[\d:.,]+)\]\s*)?(?P<speaker>[A-Z][\w .'-]{0,40}?):\s+(?P<text>.+)$")


def _block(blocks: list[dict[str, Any]], kind: str, text: str, path: list[str], **extra: Any) -> None:
    text = re.sub(r"[ \t\u00a0]+", " ", text).strip()
    if not text:
        return
    blocks.append(
        {
            "block_id": f"b{len(blocks) + 1:04d}",
            "kind": kind,
            "ordinal": len(blocks) + 1,
            "section_path": list(path),
            "text": text,
            **extra,
        }
    )


def _heading_level(p: Paragraph) -> int | None:
    name = (p.style.name if p.style is not None else "") or ""
    m = HEADING_RE.match(name.strip())
    if not m:
        return None
    return 0 if m.group(1).lower() == "title" else int(m.group(2) or 1)


def _is_list(p: Paragraph) -> bool:
    name = ((p.style.name if p.style is not None else "") or "").lower()
    if "list" in name:
        return True
    ppr = p._p.pPr
    return ppr is not None and ppr.numPr is not None


def normalize_docx(data: bytes) -> dict[str, Any]:
    document: DocxDocument = docx.Document(io.BytesIO(data))
    blocks: list[dict[str, Any]] = []
    path: list[str] = []
    levels: list[int] = []
    warnings: list[str] = []
    tables = 0
    body = document.element.body
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(child, document)
            text = p.text
            level = _heading_level(p)
            if level is not None and text.strip():
                while levels and levels[-1] >= level:
                    levels.pop()
                    path.pop()
                _block(blocks, "heading", text, path, level=level)
                levels.append(level)
                path.append(text.strip())
            elif _is_list(p):
                _block(blocks, "list_item", text, path)
            else:
                _block(blocks, "paragraph", text, path)
        elif tag == "tbl":
            tables += 1
            table = Table(child, document)
            header: list[str] | None = None
            for r_idx, row in enumerate(table.rows):
                try:
                    cells = [c.text.strip() for c in row.cells]
                except IndexError:
                    warnings.append(
                        f"Table {tables} row {r_idx + 1} has merged cells that could not be read."
                    )
                    continue
                # python-docx repeats merged cells; collapse consecutive duplicates
                dedup = [c for i, c in enumerate(cells) if i == 0 or c != cells[i - 1]]
                if r_idx == 0:
                    header = dedup
                    _block(blocks, "table_header", " | ".join(dedup), path, table=tables, row=0)
                else:
                    text = " | ".join(
                        f"{h}: {c}" if header and i < len(header) and h else c
                        for i, (h, c) in enumerate(zip(header or [""] * len(dedup), dedup, strict=False))
                    )
                    _block(blocks, "table_row", text, path, table=tables, row=r_idx, cells=dedup)
    props = document.core_properties
    metadata = {
        "title": props.title or None,
        "author": props.author or None,
        "created": props.created.isoformat() if props.created else None,
        "modified": props.modified.isoformat() if props.modified else None,
        "tables": tables,
        "headings": sum(1 for b in blocks if b["kind"] == "heading"),
    }
    if not blocks:
        warnings.append("No text content was found in the document.")
    return {"format": "docx", "metadata": metadata, "blocks": blocks, "warnings": warnings}


def normalize_text(data: bytes, *, kind: str) -> dict[str, Any]:
    text = data.decode("utf-8-sig")
    blocks: list[dict[str, Any]] = []
    path: list[str] = []
    warnings: list[str] = []
    speakers: set[str] = set()
    current_ts: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.upper() == "WEBVTT" or line.isdigit():
            continue
        if VTT_TIME.match(line):
            current_ts = line.split()[0]
            continue
        md = re.match(r"^(#{1,6})\s+(.*)$", line)
        if md:
            level = len(md.group(1))
            path = [*path[: level - 1], md.group(2).strip()]
            _block(blocks, "heading", md.group(2), path[:-1], level=level)
            continue
        m = SPEAKER.match(line) if kind == "transcript" else None
        if m:
            speakers.add(m.group("speaker").strip())
            _block(
                blocks,
                "utterance",
                m.group("text"),
                path,
                speaker=m.group("speaker").strip(),
                timestamp=m.group("ts") or current_ts,
            )
        elif re.match(r"^([-*•]|\d+[.)])\s+", line):
            _block(blocks, "list_item", re.sub(r"^([-*•]|\d+[.)])\s+", "", line), path)
        else:
            _block(blocks, "paragraph", line, path)
    if kind == "transcript" and not speakers:
        warnings.append("No speaker labels were recognized; utterances are treated as plain paragraphs.")
    return {
        "format": "text",
        "metadata": {"speakers": sorted(speakers), "lines": len(text.splitlines())},
        "blocks": blocks,
        "warnings": warnings,
    }

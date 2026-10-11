"""Malicious and malformed uploads are rejected before storage or parsing (threat model T6)."""

from __future__ import annotations

import zipfile

import pytest

from agentic_sdlc.core.errors import ValidationFailed
from agentic_sdlc.documents.normalize import normalize_docx
from agentic_sdlc.documents.validation import validate_upload
from tests.helpers import SAMPLE_BRD, make_docx, make_zip

MINIMAL = {"[Content_Types].xml": b"<Types/>", "word/document.xml": b"<w:document/>"}


def test_valid_docx_and_text_accepted() -> None:
    assert validate_upload("brd.docx", SAMPLE_BRD.read_bytes()).format == "docx"
    assert validate_upload("notes.txt", b"Alice: we need exports").format == "text"


@pytest.mark.parametrize(
    ("name", "data", "message"),
    [
        ("x.docx", b"", "empty"),
        ("x.docx", b"PK\x03\x04garbage", "corrupt"),
        ("x.exe", b"MZ\x90\x00", "unsupported"),
        ("x.zip", make_zip(MINIMAL), "only .docx"),
        ("x.docx", make_zip({"word/document.xml": b"<x/>"}), "missing required parts"),
        ("x.docx", make_zip({**MINIMAL, "../../etc/passwd": b"x"}), "unsafe path"),
        ("x.docx", make_zip({**MINIMAL, "/abs/path": b"x"}), "unsafe path"),
        ("x.docx", make_zip({**MINIMAL, "word/vbaProject.bin": b"x"}), "macro"),
        ("x.txt", b"hello\x00world", "binary"),
        ("x.txt", "café".encode("latin-1"), "UTF-8"),
        ("x.txt", b"   \n  ", "empty"),
    ],
)
def test_rejected_uploads(name: str, data: bytes, message: str) -> None:
    with pytest.raises(ValidationFailed, match=message):
        validate_upload(name, data)


def test_zip_bomb_rejected() -> None:
    bomb = make_zip({**MINIMAL, "word/media/bomb.bin": b"\x00" * 30_000_000})
    with pytest.raises(ValidationFailed, match=r"compression ratio|size limit"):
        validate_upload("bomb.docx", bomb)


def test_oversized_upload_rejected() -> None:
    with pytest.raises(ValidationFailed, match="too large"):
        validate_upload("big.txt", b"a" * (10 * 1024 * 1024 + 1))


def test_external_entities_are_not_resolved(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """An XXE payload inside document.xml must not read local files."""
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET-VALUE")
    base = make_docx("placeholder")
    src = zipfile.ZipFile(__import__("io").BytesIO(base))
    entries = {n: src.read(n) for n in src.namelist()}
    xml = entries["word/document.xml"].decode()
    doctype = f'<!DOCTYPE w:document [<!ENTITY xxe SYSTEM "file://{secret}">]>'
    xml = xml.replace("?>", "?>" + doctype, 1).replace("placeholder", "&xxe;")
    entries["word/document.xml"] = xml.encode()
    payload = make_zip(entries)
    validate_upload("xxe.docx", payload)  # structurally valid
    try:
        normalized = normalize_docx(payload)
    except Exception:
        return  # refusing to parse is also acceptable
    assert "TOP-SECRET-VALUE" not in str(normalized)

"""Minimum magic-byte validation for uploaded project files.

The client ``Content-Type`` header is never consulted. Extensions with no
reliable signature (plain text, RTF, ODT, legacy DOC/XLS/PPT, TAR, WEBP, SVG,
CAD formats) are accepted without a signature check and stored opaquely.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

_ZIP_PREFIXES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")

_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    ".pdf": (b"%PDF-",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".zip": _ZIP_PREFIXES,
    ".docx": _ZIP_PREFIXES,
    ".xlsx": _ZIP_PREFIXES,
    ".pptx": _ZIP_PREFIXES,
    ".gz": (b"\x1f\x8b",),
    ".tgz": (b"\x1f\x8b",),
}

# OOXML top-level members expected inside the (zip) container.
_OOXML_MARKERS = {".docx": "word/", ".xlsx": "xl/", ".pptx": "ppt/"}


def signature_ok(extension: str, head: bytes) -> bool:
    """True if ``head`` matches a known signature for ``extension`` -- or if the
    extension has no reliable signature (then it is accepted)."""
    prefixes = _SIGNATURES.get(extension.lower())
    if not prefixes:
        return True
    return any(head.startswith(p) for p in prefixes)


def ooxml_structure_ok(path: Path, extension: str) -> bool:
    """True for non-OOXML extensions. For .docx/.xlsx/.pptx, opens the container
    read-only (never extracts) and checks the central directory contains
    ``[Content_Types].xml`` plus the expected top-level prefix."""
    marker = _OOXML_MARKERS.get(extension.lower())
    if marker is None:
        return True
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
    except Exception:
        return False
    if "[Content_Types].xml" not in names:
        return False
    return any(n.startswith(marker) for n in names)

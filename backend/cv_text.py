from pathlib import Path

from pypdf import PdfReader


def extract_text(path: Path) -> str:
    """Best-effort plain-text extraction from an uploaded CV PDF. Returns "" on failure
    (scanned/image-only PDFs, corrupt files) rather than raising."""
    try:
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    except Exception:
        return ""

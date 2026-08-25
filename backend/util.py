import html
import re

_BLOCK_BREAK_RE = re.compile(r"(?i)<br\s*/?>|</p>|</li>|</div>|</h[1-6]>")
_TAG_RE = re.compile(r"<[^>]+>")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def strip_html(text: str) -> str:
    """Best-effort HTML -> readable plain text for job descriptions pulled from
    ATS APIs (Greenhouse/Lever/etc. return rich HTML, not plain text)."""
    if not text:
        return ""
    # Some sources (Greenhouse) double-encode entities: "&amp;lt;p&amp;gt;" for "<p>",
    # so a stray "&nbsp;" can still remain after a single pass. Unescaping is
    # idempotent on already-resolved text, so run it twice to be safe.
    text = html.unescape(html.unescape(text))
    text = _BLOCK_BREAK_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = _BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()

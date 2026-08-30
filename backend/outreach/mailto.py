"""Build an encoded ``mailto:`` URL.

This module -- and this whole package -- has no mail-transport code at all: no
sockets are opened to a mail server, no messages are transmitted. The only
artefact is a string the frontend hands to the operating system's mail client.

Everything except ``@`` in the address is percent-encoded, so a crafted subject
or body cannot inject extra ``mailto`` parameters (``&cc=``, a ``\\r\\n``
header, ...). The body is normalized to canonical CRLF line endings (RFC 6068);
the subject is flattened to a single line.
"""

from __future__ import annotations

from urllib.parse import quote


def build_mailto_url(email: str, subject: str, body: str) -> str:
    to = quote((email or "").strip(), safe="@")
    subj = quote(" ".join((subject or "").splitlines()).strip(), safe="")
    crlf_body = (
        (body or "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "\r\n")
    )
    bod = quote(crlf_body, safe="")
    return f"mailto:{to}?subject={subj}&body={bod}"

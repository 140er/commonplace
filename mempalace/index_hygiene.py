"""Pure, opt-in base64 detection for index text, never for stored transcripts.

Call on a whole message before chunking. Maximal runs are checked as a unit:
mixed alphabets, bad padding and noncanonical encodings cannot yield a valid
substring. This is a conservative encoding heuristic, not a file validator.
No production ingest path uses it until the recall gate passes.
"""

from __future__ import annotations

import base64
import binascii
import math
import re
from collections import Counter
from dataclasses import dataclass

_RUN = re.compile(r"[A-Za-z0-9+/_=\-]{200,}")
_STANDARD = re.compile(r"[A-Za-z0-9+/]{200,}={0,2}\Z")
_URLSAFE = re.compile(r"[A-Za-z0-9_\-]{200,}={0,2}\Z")


@dataclass(frozen=True)
class Base64Span:
    """Half-open character offsets in the original message, plus index metadata."""

    start: int
    end: int
    mime: str
    decoded_bytes: int

    @property
    def placeholder(self) -> str:
        return f"[base64 {self.mime} {self.decoded_bytes} B]"


def _mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    return "binary"


def find_base64_spans(text: str) -> list[Base64Span]:
    """Find canonical base64 runs with at least 200 payload chars and entropy > 4.5.

    Standard and URL-safe alphabets are accepted separately, with zero to two
    padding characters. The total length must be divisible by four, including
    for unpadded URL-safe runs. Whitespace/wrapped payloads are separate runs.
    MIME labels come only from decoded magic bytes, never a data URI's claim.
    """
    spans = []
    for match in _RUN.finditer(text):
        run = match.group()
        if len(run) % 4:
            continue
        if _STANDARD.fullmatch(run):
            altchars = None
        elif _URLSAFE.fullmatch(run):
            altchars = b"-_"
        else:
            continue
        try:
            decoded = base64.b64decode(run, altchars=altchars, validate=True)
        except binascii.Error:
            continue
        if base64.b64encode(decoded, altchars=altchars).decode("ascii") != run:
            continue
        counts = Counter(run)
        entropy = -sum((n / len(run)) * math.log2(n / len(run)) for n in counts.values())
        if entropy > 4.5:
            spans.append(Base64Span(match.start(), match.end(), _mime(decoded), len(decoded)))
    return spans


def index_text(text: str) -> str:
    """Return a separate index-only view; callers must retain ``text`` verbatim."""
    parts = []
    end = 0
    for span in find_base64_spans(text):
        parts.extend((text[end : span.start], span.placeholder))
        end = span.end
    parts.append(text[end:])
    return "".join(parts)

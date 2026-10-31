"""Pure base64 detection for index text, never for stored transcripts.

Call on a whole message before chunking. Maximal runs are checked as a unit:
mixed alphabets cannot yield a valid substring. An equals sign before a run
is a delimiter (``key=payload``), while trailing equals signs are padding.
This is an encoding heuristic, not a file validator or proof of binary data.
Core storage uses this detector for index views; stored documents stay verbatim.
"""

from __future__ import annotations

import base64
import binascii
import math
import re
from collections import Counter
from dataclasses import dataclass

_RUN = re.compile(r"(?<![A-Za-z0-9+/_\-])[A-Za-z0-9+/_\-]{200,}={0,2}(?![A-Za-z0-9+/_=\-])")
_CANDIDATE_RUN = re.compile(r"(?<![A-Za-z0-9+/_\-])[A-Za-z0-9+/_\-]{200,}=*")
_STANDARD = re.compile(r"[A-Za-z0-9+/]{200,}={0,2}\Z")
_URLSAFE = re.compile(r"[A-Za-z0-9_\-]{200,}={0,2}\Z")

# Public index-only format. ``binary`` is a classification, not a MIME type.
PLACEHOLDER_FORMAT = "[base64 {media_type} {decoded_bytes} B]"
PLACEHOLDER_RE = re.compile(
    r"\[base64 (?P<media_type>image/(?:png|jpeg|gif|webp|svg\+xml)|"
    r"application/(?:pdf|zip|gzip)|font/woff2|binary) "
    r"(?P<decoded_bytes>0|[1-9][0-9]*) B\]"
)


@dataclass(frozen=True)
class Base64Span:
    """Half-open character offsets in the original message, plus index metadata."""

    start: int
    end: int
    media_type: str
    decoded_bytes: int

    @property
    def placeholder(self) -> str:
        return PLACEHOLDER_FORMAT.format(
            media_type=self.media_type, decoded_bytes=self.decoded_bytes
        )


def _media_type(data: bytes) -> str:
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
    if data.startswith(b"wOF2"):
        return "font/woff2"
    if re.match(rb"\s*(?:<\?xml[^>]*>\s*)?<svg(?:\s|>)", data[:2048]):
        return "image/svg+xml"
    if data.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        return "application/zip"
    if data.startswith(b"\x1f\x8b\x08"):
        return "application/gzip"
    return "binary"


def base64_candidate_ranges(text: str) -> list[tuple[int, int]]:
    """Maximal base64-shaped runs, including rejected padding/alphabet/length.

    Chunking must preserve a whole-input rejection as well as an acceptance:
    a locally valid substring cannot override the decision on its whole run.
    """
    return [match.span() for match in _CANDIDATE_RUN.finditer(text)]


def find_base64_spans(text: str) -> list[Base64Span]:
    """Find canonical base64 runs with at least 200 payload chars and entropy > 4.5.

    Standard and URL-safe alphabets are accepted separately, with zero to two
    padding characters. The total length must be divisible by four, including
    for unpadded URL-safe runs. Whitespace/wrapped payloads are separate runs.
    Media labels come from decoded headers, never a data URI's claim.
    Wrapped 64/76-column payloads are deliberately left in. High-entropy
    alphabetic/base32 identifiers and words glued to a run may satisfy this
    same encoding rule; corpus recall/token gates remain necessary.
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
            spans.append(Base64Span(match.start(), match.end(), _media_type(decoded), len(decoded)))
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

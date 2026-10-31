"""One reversible stored-text policy for every backend.

Postgres cannot store NUL in text, a lone surrogate cannot be UTF-8 encoded,
and a raw NUL corrupts Chroma's FTS index. A string holding either, or one
that already starts with the escape prefix, is stored as the prefix plus a
backslash escape, and the record carries ``FLAG`` in its metadata. A read
decodes only flagged records, and in them only prefixed strings, so text
that merely looks escaped is returned as written. The flag never reaches
callers. Ids and metadata keys that cannot be stored are rejected, never
rewritten.

``FLAG`` is reserved: a write that carries it has it dropped and is encoded
like any other, so a caller cannot pass escaped-looking text off as stored
form. Code that copies raw rows between palaces (repair, migrate) decodes
them first.

Every stored string that starts with the prefix was written by the encoder,
so a reader that cannot see the record's flag (an aggregate over one column)
may decode a single value with :func:`decode_value`. Filters pushed down to
a backend compare stored form, so their operands go through
:func:`encode_where` / :func:`encode_filter`; a filter evaluated in Python
on decoded rows keeps the caller's values.

A lone surrogate (a stray source byte) leaves the process one of three ways.
Between MemPalace processes (proxy to hub requests, daemon, peer sync, the
revision log) :func:`dumps_internal` keeps it as an exact ``\\udcXX``
escape. A response to an external MCP client goes through
:func:`dumps_client`, which shows it as U+FFFD, because some clients' JSON
parsers reject a lone-surrogate escape outright; the stored text keeps the
byte, and ``mempalace_get_drawer(include_source_bytes=True)`` returns it.
A service that only takes valid Unicode (an embedder, an LLM API) gets
``config.strip_lone_surrogates``.
"""

import hashlib
import json
import logging
import re
import sys
import threading
import time
from bisect import bisect_left, bisect_right
from collections.abc import Mapping

from .index_hygiene import run_touches_word
from .metadata_types import coerce_metadata_types

FLAG = "_mempalace_escaped"
INDEX_SPANS = "_mempalace_index_spans"
KEYWORD_ONLY = "_mempalace_keyword_only"
RESERVED_METADATA = frozenset({FLAG, KEYWORD_ONLY, INDEX_SPANS})
INDEX_VERSION = 1
logger = logging.getLogger(__name__)
_hint_warning_times = {}
_hint_warning_lock = threading.Lock()
_BASE64_FRAGMENT = re.compile(r"(?:[A-Za-z0-9+/]+|[A-Za-z0-9_\-]+)={0,2}\Z")
_PREFIX = "\x01mpesc:"
_LONE_SURROGATE = re.compile(r"[\ud800-\udfff]")
_BAD = re.compile(r"[\x00\ud800-\udfff]")
_ESCAPE = re.compile(r"[\\\x00\ud800-\udfff]")
_UNESCAPE = re.compile(r"\\(\\|0|u[0-9A-F]{4})")


class StoredTextError(ValueError):
    """An id or metadata key cannot be stored without changing it."""


def _needs_escape(text: str) -> bool:
    return text.startswith(_PREFIX) or _BAD.search(text) is not None


def _escape_char(match) -> str:
    char = match.group()
    if char == "\\":
        return "\\\\"
    if char == "\x00":
        return "\\0"
    return f"\\u{ord(char):04X}"


def _unescape_seq(match) -> str:
    seq = match.group(1)
    if seq == "\\":
        return "\\"
    if seq == "0":
        return "\x00"
    return chr(int(seq[1:], 16))


def _encode(value, changed: list):
    if isinstance(value, str):
        if not _needs_escape(value):
            return value
        changed.append(True)
        return _PREFIX + _ESCAPE.sub(_escape_char, value)
    if isinstance(value, (list, tuple)):
        return type(value)(_encode(item, changed) for item in value)
    return value


def _decode(value):
    if isinstance(value, str):
        if not value.startswith(_PREFIX):
            return value
        return _UNESCAPE.sub(_unescape_seq, value[len(_PREFIX) :])
    if isinstance(value, list):
        return [_decode(item) for item in value]
    return value


def _check_key(key) -> None:
    if isinstance(key, str) and _BAD.search(key):
        raise StoredTextError(
            f"{key!r} cannot be stored: NUL and lone surrogates are rejected, not rewritten"
        )


def encode_record(document, metadata):
    """Return ``(document, metadata)`` in stored form.

    ``metadata`` is ``None`` only when it was ``None`` and nothing needed
    escaping.
    """
    if metadata and FLAG in metadata:
        metadata = {key: value for key, value in metadata.items() if key != FLAG}
    changed: list = []
    document = _encode(document, changed)
    encoded = {}
    for key, value in (metadata or {}).items():
        _check_key(key)
        encoded[key] = _encode(value, changed)
    if changed:
        encoded[FLAG] = 1
    elif metadata is None:
        return document, None
    return document, encoded


def decode_value(value):
    """Decode one stored value read without its record's flag."""
    return _decode(value)


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _warn_index_hint(message: str) -> None:
    """At most one warning of each kind per minute, even during bulk repair."""
    now = time.monotonic()
    with _hint_warning_lock:
        if now - _hint_warning_times.get(message, float("-inf")) < 60:
            return
        _hint_warning_times[message] = now
    logger.warning(message)


class IndexSource:
    """Detect once on whole content, then project into unchanged chunk ranges."""

    def __init__(self, text: str, metadata=None):
        from .index_hygiene import base64_candidate_ranges, find_base64_spans

        self.text = text
        self._inherited_spans = None
        if (metadata or {}).get(INDEX_SPANS):
            try:
                self._inherited_spans = _validated_index_spans(text, metadata[INDEX_SPANS])
            except (ValueError, TypeError, KeyError, RecursionError):
                _warn_index_hint("Ignoring stale or invalid index span hint")
        self.spans = find_base64_spans(text)
        self.candidates = base64_candidate_ranges(text)
        self._set_offsets()

    def _set_offsets(self):
        self._ends = [span.end for span in self.spans]
        self._starts = [span.start for span in self.spans]
        self._candidate_starts = [start for start, _ in self.candidates]
        self._candidate_ends = [end for _, end in self.candidates]

    def metadata(self, start: int, end: int, metadata=None) -> dict:
        """Copy metadata with this range's acceptance or rejection provenance.

        A removed hint is sent as a tombstone for backends that merge metadata.
        Empty span lists preserve whole-input rejections across chunk boundaries.
        """
        result = dict(metadata or {})
        if INDEX_SPANS in result:
            result[INDEX_SPANS] = None
        if self._inherited_spans is not None:
            spans = [
                [
                    max(start, a) - start,
                    min(end, b) - start,
                    placeholder,
                    (emit and start <= a < end) or a <= start and end <= b,
                ]
                for a, b, placeholder, emit in self._inherited_spans
                if a < end and start < b and self.text[max(start, a)] != "="
            ]
            result[INDEX_SPANS] = json.dumps(
                {"v": INDEX_VERSION, "sha256": _text_hash(self.text[start:end]), "spans": spans},
                separators=(",", ":"),
            )
            return result
        # Validated runs have padding only at the end; padding-only slices stay literal.
        spans = [
            [
                max(start, s.start) - start,
                min(end, s.end) - start,
                s.placeholder,
                start <= s.start < end or (s.start <= start and end <= s.end),
            ]
            for s in self.spans[bisect_right(self._ends, start) : bisect_left(self._starts, end)]
            if self.text[max(start, s.start)] != "="
        ]
        touches_run = bisect_right(self._candidate_ends, start) < bisect_left(
            self._candidate_starts, end
        )
        if spans or touches_run:
            result[INDEX_SPANS] = json.dumps(
                {"v": INDEX_VERSION, "sha256": _text_hash(self.text[start:end]), "spans": spans},
                separators=(",", ":"),
            )
        return result

    def _align(self, view: str):
        """Map ranges across the whitespace edits conversation chunkers make.

        Streaming alignment uses bounded extra memory. It is a rare fallback;
        ordinary slices use substring offsets without a Python character walk.
        """
        from .index_hygiene import Base64Span

        boundaries = {}
        for start, end in self.candidates:
            boundaries[start] = boundaries[end - 1] = None
        for span in self.spans:
            boundaries[span.start] = boundaries[span.end - 1] = None
        original_pos = projected_pos = 0
        while True:
            while original_pos < len(self.text) and self.text[original_pos].isspace():
                original_pos += 1
            while projected_pos < len(view) and view[projected_pos].isspace():
                projected_pos += 1
            if original_pos == len(self.text) and projected_pos == len(view):
                break
            if (
                original_pos == len(self.text)
                or projected_pos == len(view)
                or self.text[original_pos] != view[projected_pos]
            ):
                raise ValueError("chunker changed non-whitespace source text")
            if original_pos in boundaries:
                boundaries[original_pos] = projected_pos
            original_pos += 1
            projected_pos += 1

        def mapped_range(start, end):
            mapped_start, mapped_end = boundaries[start], boundaries[end - 1]
            if (
                mapped_start is None
                or mapped_end is None
                or mapped_end + 1 - mapped_start != end - start
            ):
                raise ValueError("chunker changed a base64-shaped run")
            return mapped_start, mapped_end + 1

        self.spans = [
            Base64Span(*mapped_range(s.start, s.end), s.media_type, s.decoded_bytes)
            for s in self.spans
        ]
        self.candidates = [mapped_range(start, end) for start, end in self.candidates]
        self.text = view
        self._set_offsets()


def annotate_chunks(text: str, chunks: list) -> list:
    """Attach index provenance without letting a hint failure prevent storage."""
    try:
        source = IndexSource(text)
        if not source.candidates:
            return chunks
        cursor = 0
        for chunk in chunks:
            content = chunk["content"]
            start = text.find(content, cursor)
            if start < 0:
                source._align("".join(item["content"] for item in chunks))
                position = 0
                for item in chunks:
                    end = position + len(item["content"])
                    item.pop(INDEX_SPANS, None)
                    item.update(source.metadata(position, end))
                    position = end
                return chunks
            chunk.update(source.metadata(start, start + len(content)))
            cursor = start + len(content)
    except Exception:
        _warn_index_hint("Could not project index span hints; storing chunks without hints")
        for chunk in chunks:
            chunk.pop(INDEX_SPANS, None)
    return chunks


def chunk_index_metadata(chunk: dict) -> dict:
    return {INDEX_SPANS: chunk[INDEX_SPANS]} if INDEX_SPANS in chunk else {}


def user_metadata(metadata):
    """Strip server-owned drawer markers; raw internal copies retain them."""
    if isinstance(metadata, Mapping):
        return {key: value for key, value in metadata.items() if key not in RESERVED_METADATA}
    if isinstance(metadata, (list, tuple)):
        return type(metadata)(user_metadata(item) for item in metadata)
    return metadata


public_metadata = user_metadata
_DRAWER_WRITE_TOOLS = frozenset(
    {
        "mempalace_add_drawer",
        "mempalace_update_drawer",
        "mempalace_mine",
        "mempalace_diary_write",
    }
)
_DRAWER_READ_TOOLS = frozenset(
    {
        "mempalace_get_drawer",
        "mempalace_get_drawers",
        "mempalace_list_drawers",
        "mempalace_search",
        "mempalace_diary_read",
        "mempalace_check_duplicate",
        "mempalace_delete_drawer",
        "mempalace_delete_drawers",
    }
)


def user_tool_arguments(arguments, *, tool_name=None):
    """Clean drawer metadata without rewriting event/artifact JSON payloads."""
    if tool_name is not None and tool_name not in _DRAWER_WRITE_TOOLS:
        return dict(arguments)
    clean = user_metadata(arguments)
    clean.pop("lexical_only", None)
    clean.pop("_mirror_unit", None)
    for key in ("metadata", "metadatas"):
        if key in clean:
            clean[key] = user_metadata(clean[key])
    return clean


def public_tool_result(result, *, tool_name=None):
    if tool_name is not None and tool_name not in _DRAWER_READ_TOOLS | _DRAWER_WRITE_TOOLS:
        return result
    if isinstance(result, dict):
        return {
            key: user_metadata(value)
            if key in {"metadata", "metadatas", "deleted_meta"}
            else public_tool_result(value)
            for key, value in result.items()
        }
    if isinstance(result, (list, tuple)):
        return type(result)(public_tool_result(item) for item in result)
    return result


def _validated_index_spans(text: str, hint: str) -> list:
    from .index_hygiene import PLACEHOLDER_RE, find_base64_spans

    data = json.loads(hint)
    if data["v"] != INDEX_VERSION or data["sha256"] != _text_hash(text):
        raise ValueError("version or content changed")
    cursor = 0
    for start, end, placeholder, emit in data["spans"]:
        if not (
            type(start) is int
            and type(end) is int
            and cursor <= start < end <= len(text)
            and isinstance(placeholder, str)
            and _BASE64_FRAGMENT.fullmatch(text[start:end])
            and type(emit) is bool
            and (emit or (start == 0 and end < len(text)))
            and not run_touches_word(text, start, end)
        ):
            raise ValueError("invalid span")
        match = PLACEHOLDER_RE.fullmatch(placeholder)
        if match is None:
            raise ValueError("invalid placeholder")
        size = int(match["decoded_bytes"])
        # The detector requires 200 payload characters (150 bytes).
        # A full run must fit in a Python bytes object and cover this
        # fragment, including only the padding its byte count permits.
        if not (
            150 <= size <= sys.maxsize
            and end - start <= 4 * ((size + 2) // 3)
            and text[start:end].count("=") <= -size % 3
        ):
            raise ValueError("invalid placeholder size")
        if emit and end < len(text):
            # Emitting spans with a visible end are complete runs,
            # even at offset zero. They must satisfy the detector's rule,
            # including its threshold, alphabets, pad bits and entropy.
            spans = find_base64_spans(text[start:end])
            if len(spans) != 1 or spans[0].placeholder != placeholder:
                raise ValueError("invalid complete run")
        cursor = end
    return data["spans"]


def index_text(stored: str, metadata=None, *, stored_form=True) -> str:
    """The words of a stored document for a full-text index.

    NUL and lone surrogates become spaces: the index never returns text, and
    neither can be tokenized or UTF-8 encoded.
    """
    from .index_hygiene import index_text as detect_index_text

    text = _decode(stored) if stored_form else stored
    hint = (metadata or {}).get(INDEX_SPANS)
    if hint:
        try:
            parts, cursor = [], 0
            for start, end, placeholder, emit in _validated_index_spans(text, hint):
                parts.extend((text[cursor:start], placeholder if emit else ""))
                cursor = end
            parts.append(text[cursor:])
            return _BAD.sub(" ", "".join(parts))
        except (ValueError, TypeError, KeyError, RecursionError):
            _warn_index_hint("Ignoring stale or invalid index span hint")
    return _BAD.sub(" ", detect_index_text(text))


_EQUALITY_OPS = ("$eq", "$ne", "$in", "$nin")


def encode_filter(expected):
    """Stored form of one field's filter operand: a value or ``{op: value}``.

    Only equality operands are encoded; an escaped value has no useful order,
    so a range comparison keeps the caller's value.
    """
    if isinstance(expected, dict):
        return {
            op: _encode(value, []) if op in _EQUALITY_OPS else value
            for op, value in expected.items()
        }
    return _encode(expected, [])


def encode_where(where):
    """A metadata ``where`` with every equality operand in stored form."""
    if not isinstance(where, dict):
        return where
    out = {}
    for key, expected in where.items():
        if key in ("$and", "$or") and isinstance(expected, list):
            out[key] = [encode_where(clause) for clause in expected]
        elif key.startswith("$"):
            out[key] = expected
        else:
            out[key] = encode_filter(expected)
    return out


def decode_record(document, metadata):
    """Reverse :func:`encode_record` and drop the flag."""
    if not metadata or FLAG not in metadata:
        return document, metadata
    metadata = dict(metadata)
    if not metadata.pop(FLAG):
        return document, metadata
    return _decode(document), {key: _decode(value) for key, value in metadata.items()}


def prepare_stored_write(documents, ids, metadatas=None):
    """Encode a write batch and reject ids that cannot be stored.

    ``documents`` may be ``None`` (a metadata-only update) or a bare string
    (Chroma's one-or-many shape); either comes back in the same shape.
    Metadata comes back as ``None`` only when none was passed and nothing was
    escaped. Otherwise every record gets a dict, and a record with nothing of
    its own gets ``{FLAG: 1}``: a spare flag is harmless, since only prefixed
    strings are decoded, and merging backends keep older escaped values
    readable.
    """
    single = isinstance(documents, str)
    ids = [ids] if isinstance(ids, str) else list(ids)
    docs = [documents] if single else documents
    for doc_id in ids:
        if not isinstance(doc_id, str) or not doc_id:
            raise StoredTextError("id must be a non-empty string")
        _check_key(doc_id)
    metas = metadatas if metadatas is not None else [None] * len(ids)
    out_docs, out_metas = [], []
    for doc, meta in zip(docs if docs is not None else [None] * len(ids), metas):
        doc, meta = encode_record(doc, coerce_metadata_types(meta))
        out_docs.append(doc)
        out_metas.append(meta)
    if metadatas is None:
        if all(meta is None for meta in out_metas):
            out_metas = None
        else:
            out_metas = [meta if meta is not None else {FLAG: 1} for meta in out_metas]
    if documents is None:
        out_docs = None
    elif single:
        out_docs = out_docs[0]
    return out_docs, ids, out_metas


def restore_rows(documents, metadatas):
    """Decode parallel ``documents``/``metadatas`` lists, nested one level for
    query results. Either list may be empty when it was not requested."""
    if metadatas and isinstance(metadatas[0], list):
        pairs = [
            restore_rows(docs, metas)
            for docs, metas in zip(documents or [[]] * len(metadatas), metadatas)
        ]
        return (
            [docs for docs, _ in pairs] if documents else documents,
            [metas for _, metas in pairs],
        )
    if not metadatas:
        return documents, metadatas
    docs = documents if documents else [None] * len(metadatas)
    pairs = [decode_record(doc, meta) for doc, meta in zip(docs, metadatas)]
    return (
        [doc for doc, _ in pairs] if documents else documents,
        [meta for _, meta in pairs],
    )


def _surrogate_escape(match) -> str:
    return f"\\u{ord(match.group()):04x}"


# Sent by a MemPalace process calling the hub's MCP endpoint, so the hub
# answers with :func:`dumps_internal` instead of :func:`dumps_client`.
EXACT_WIRE_HEADER = "X-MemPalace-Exact"


def dumps_internal(obj, **kwargs) -> str:
    """JSON for a hop between MemPalace processes, exact.

    Non-ASCII stays as written and each lone surrogate becomes a ``\\udcXX``
    escape, so the text encodes as UTF-8 and the reader's ``json.loads``
    gets the surrogate back.
    """
    text = json.dumps(obj, ensure_ascii=False, **kwargs)
    return _LONE_SURROGATE.sub(_surrogate_escape, text)


def dumps_client(obj, **kwargs) -> str:
    """JSON for an external MCP client: each lone surrogate shown as U+FFFD.

    Some clients' JSON parsers reject a lone-surrogate escape and with it the
    whole response. The display is lossy; the stored text is not.
    """
    return _LONE_SURROGATE.sub("�", json.dumps(obj, ensure_ascii=False, **kwargs))

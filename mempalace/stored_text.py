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
from bisect import bisect_left, bisect_right

from .metadata_types import coerce_metadata_types

FLAG = "_mempalace_escaped"
INDEX_SPANS = "_mempalace_index_spans"
INDEX_VERSION = 1
logger = logging.getLogger(__name__)
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


class IndexSource:
    """Detect once on whole content, then project into unchanged chunk ranges."""

    def __init__(self, text: str):
        from .index_hygiene import find_base64_spans

        self.text = text
        self.spans = find_base64_spans(text)
        self._ends = [span.end for span in self.spans]
        self._starts = [span.start for span in self.spans]

    def metadata(self, start: int, end: int, metadata=None) -> dict:
        """Copy metadata, replacing any old hint with this range's provenance."""
        result = {k: v for k, v in (metadata or {}).items() if k != INDEX_SPANS}
        spans = [
            [
                max(start, s.start) - start,
                min(end, s.end) - start,
                s.placeholder,
                start <= s.start < end or (s.start <= start and end <= s.end),
            ]
            for s in self.spans[bisect_right(self._ends, start) : bisect_left(self._starts, end)]
            if s.start < end and s.end > start
        ]
        if spans:
            result[INDEX_SPANS] = json.dumps(
                {"v": INDEX_VERSION, "sha256": _text_hash(self.text[start:end]), "spans": spans},
                separators=(",", ":"),
            )
        return result


def annotate_chunks(text: str, chunks: list) -> list:
    """Attach whole-source provenance to ordered, non-overlapping text slices."""
    source = IndexSource(text)
    if not source.spans:
        return chunks
    cursor = 0
    for chunk in chunks:
        content = chunk["content"]
        start = text.find(content, cursor)
        if start < 0:
            # Conversation chunkers trim whitespace around turn headers and
            # merge small units. Preserve their established documents while
            # mapping the original validated runs into the concatenated view.
            # A run contains no whitespace, so those edits cannot split it.
            from .index_hygiene import Base64Span

            view = "".join(chunk["content"] for chunk in chunks)
            mapped, position = [], 0
            for span in source.spans:
                payload = text[span.start : span.end]
                found = view.find(payload, position)
                if found < 0:
                    raise ValueError("validated span missing from chunked source")
                mapped.append(
                    Base64Span(found, found + len(payload), span.media_type, span.decoded_bytes)
                )
                position = found + len(payload)
            source.text = view
            source.spans = mapped
            source._starts = [span.start for span in mapped]
            source._ends = [span.end for span in mapped]
            position = 0
            for item in chunks:
                end = position + len(item["content"])
                item.pop(INDEX_SPANS, None)
                item.update(source.metadata(position, end))
                position = end
            return chunks
        chunk.update(source.metadata(start, start + len(content)))
        cursor = start + len(content)
    return chunks


def chunk_index_metadata(chunk: dict) -> dict:
    return {INDEX_SPANS: chunk[INDEX_SPANS]} if INDEX_SPANS in chunk else {}


def public_metadata(metadata):
    """Drop index provenance at client boundaries, retaining raw internal copies."""
    return {k: v for k, v in (metadata or {}).items() if k != INDEX_SPANS}


def strip_index_hints(value):
    """Remove reserved provenance from untrusted arguments or public results."""
    if isinstance(value, dict):
        return {k: strip_index_hints(v) for k, v in value.items() if k != INDEX_SPANS}
    if isinstance(value, list):
        return [strip_index_hints(v) for v in value]
    return value


def index_text(stored: str, metadata=None, *, stored_form=True) -> str:
    """The words of a stored document for a full-text index.

    NUL and lone surrogates become spaces: the index never returns text, and
    neither can be tokenized or UTF-8 encoded.
    """
    from .index_hygiene import PLACEHOLDER_RE, index_text as detect_index_text

    text = _decode(stored) if stored_form else stored
    hint = (metadata or {}).get(INDEX_SPANS)
    if hint:
        try:
            data = json.loads(hint)
            if data["v"] != INDEX_VERSION or data["sha256"] != _text_hash(text):
                raise ValueError("version or content changed")
            parts, cursor = [], 0
            for start, end, placeholder, emit in data["spans"]:
                if not (
                    type(start) is int
                    and type(end) is int
                    and cursor <= start < end <= len(text)
                    and isinstance(placeholder, str)
                    and PLACEHOLDER_RE.fullmatch(placeholder)
                    and type(emit) is bool
                ):
                    raise ValueError("invalid span")
                parts.extend((text[cursor:start], placeholder if emit else ""))
                cursor = end
            parts.append(text[cursor:])
            return _BAD.sub(" ", "".join(parts))
        except (ValueError, TypeError, KeyError):
            logger.warning("Ignoring stale or invalid index span hint")
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
    return _LONE_SURROGATE.sub(
        "�", json.dumps(strip_index_hints(obj), ensure_ascii=False, **kwargs)
    )

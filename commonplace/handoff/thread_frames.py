"""Versioned, lossless block frames for the opt-in thread store.

Frames contain length-prefixed surrogatepass strings and a binary span list.
Offsets refer to the original byte stream, never to inline escape markers.
Only long base64-shaped runs are extracted; decoding is used only when the
standard base64 encoder reproduces the original spelling exactly.
"""

from __future__ import annotations

import base64
import hashlib
import re
import struct

import zstandard as zstd

BLOCK_BYTES = 64 * 1024
MIN_TRAIN_BYTES = 128 * 1024
_U64 = struct.Struct("<Q")
_SPAN = struct.Struct("<Q32s")
_BASE64 = re.compile(rb"(?<![A-Za-z0-9+/_=-])[A-Za-z0-9+/_-]{256,}={0,}(?![A-Za-z0-9+/_=-])")


def utf8(text: str) -> bytes:
    return text.encode("utf-8", "surrogatepass")


def string(data):
    return data.decode("utf-8", "surrogatepass") if isinstance(data, bytes) else data


def sql_string(text: str):
    """SQLite TEXT cannot encode surrogates; BLOB preserves those fields."""
    return utf8(text) if any(0xD800 <= ord(char) <= 0xDFFF for char in text) else text


def blob_value(payload: bytes) -> tuple[str, bytes, bytes]:
    try:
        decoded = base64.b64decode(payload, validate=True)
    except ValueError:
        decoded = None
    kind = "decoded" if decoded is not None and base64.b64encode(decoded) == payload else "text"
    data = decoded if kind == "decoded" else payload
    return kind, data, hashlib.sha256(kind.encode("ascii") + data).digest()


def pack_strings(values: list[str]) -> bytes:
    return b"".join(_U64.pack(len(data)) + data for data in map(utf8, values))


def unpack_strings(data: bytes) -> list[str]:
    result = []
    cursor = 0
    while cursor < len(data):
        if len(data) - cursor < _U64.size:
            raise ValueError("Truncated thread frame length")
        length = _U64.unpack_from(data, cursor)[0]
        cursor += _U64.size
        end = cursor + length
        if end > len(data):
            raise ValueError("Truncated thread frame string")
        result.append(string(data[cursor:end]))
        cursor = end
    return result


def encode_frame(values: list[str], put_blob) -> bytes:
    pieces, spans = [], []
    offset = 0
    for value in values:
        data = utf8(value)
        pieces.append(_U64.pack(len(data)))
        offset += _U64.size
        cursor = 0
        for match in _BASE64.finditer(data):
            kind, payload, key = blob_value(match.group())
            put_blob(key, kind, payload)
            spans.append(_SPAN.pack(offset + match.start(), key))
            pieces.append(data[cursor : match.start()])
            cursor = match.end()
        pieces.append(data[cursor:])
        offset += len(data)
    return _U64.pack(len(spans)) + b"".join(spans) + b"".join(pieces)


def decode_frame(frame: bytes, get_blob) -> list[str]:
    if len(frame) < _U64.size:
        raise ValueError("Truncated thread frame")
    count = _U64.unpack_from(frame)[0]
    header_end = _U64.size + count * _SPAN.size
    if header_end > len(frame):
        raise ValueError("Truncated thread spans")
    pieces = []
    cursor, original = header_end, 0
    for index in range(count):
        offset, key = _SPAN.unpack_from(frame, _U64.size + index * _SPAN.size)
        length = offset - original
        if length < 0 or cursor + length > len(frame):
            raise ValueError("Invalid thread span offset")
        pieces.append(frame[cursor : cursor + length])
        kind, data = get_blob(key)
        if hashlib.sha256(kind.encode("ascii") + data).digest() != key:
            raise ValueError("Thread blob digest mismatch")
        payload = base64.b64encode(data) if kind == "decoded" else data
        pieces.append(payload)
        cursor += length
        original = offset + len(payload)
    pieces.append(frame[cursor:])
    return unpack_strings(b"".join(pieces))


def compress(frame: bytes, dictionary: bytes | None) -> bytes:
    value = zstd.ZstdCompressionDict(dictionary) if dictionary is not None else None
    return zstd.ZstdCompressor(level=3, dict_data=value).compress(frame)


def decompress(frame: bytes, dictionary: bytes | None) -> bytes:
    value = zstd.ZstdCompressionDict(dictionary) if dictionary is not None else None
    return zstd.ZstdDecompressor(dict_data=value).decompress(frame)

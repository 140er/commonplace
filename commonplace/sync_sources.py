"""Read selected native sources verbatim; persist only operation metadata."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path

from .capture_client import CHUNK_BYTES, MAX_RECORD_BYTES, CaptureError, files
from .onboarding import POINTER
from .sync_protocol import (
    CURSOR_MESSAGE,
    MEMORY_FORMAT,
    MEMORY_IMPORT_SOURCE,
    PROBE_KEY_ALLOWLIST,
    TRANSCRIPT_SOURCES,
    memory_thread_id,
    native_identity,
    native_source,
)
from .sync_state import Op, body_digest

MEMORY_BYTES = 400_000


class SourceError(ValueError):
    """Source diagnostics never include paths or user content."""


class SliceConflict(SourceError):
    """An immutable pending operation no longer matches its source bytes."""


@dataclass(frozen=True)
class Source:
    source: str
    path: str
    native_id: str
    size: int


def _open(path):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise SourceError("Symlinked source refused")
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    )
    stream = os.fdopen(descriptor, "rb")
    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
        stream.close()
        raise SourceError("Source must be a regular file")
    return stream


def discover(config) -> list[Source]:
    """Enumerate capture identities so a caller can fetch missing server cursors."""
    result, seen = [], set()
    home = Path(config.get("home", Path.home()))
    defaults = {"claude-code": home / ".claude/projects", "codex": home / ".codex/sessions"}
    for selected in config.get("sources", []):
        source = selected["source"]
        if source == "cursor":
            raise SourceError(CURSOR_MESSAGE)
        if source not in TRANSCRIPT_SOURCES:
            raise SourceError("Unsupported transcript source")
        root = Path(selected.get("root") or defaults.get(source, "")).expanduser().absolute()
        if source == "t3" and not selected.get("root"):
            raise SourceError("Select an explicit native transcript directory")
        try:
            for path in files(root):
                with _open(path) as stream:
                    raw = stream.read(MAX_RECORD_BYTES)
                    size = os.fstat(stream.fileno()).st_size
                underlying = native_source(source, "auto", raw.decode("utf-8", errors="ignore"))
                identity = native_identity(raw, underlying)
                if identity is not None and (underlying, identity) not in seen:
                    seen.add((underlying, identity))
                    result.append(Source(underlying, str(path), identity, size))
        except CaptureError:
            raise SourceError("Select a regular transcript directory") from None
    return result


def _hash_prefix(stream, length):
    digest = hashlib.sha256()
    while length:
        raw = stream.read(min(length, CHUNK_BYTES))
        if not raw:
            raise SliceConflict("Source prefix changed; saved cursor kept")
        digest.update(raw)
        length -= len(raw)
    return digest.hexdigest()


def _capture_body(source, native_id, offset, prefix, raw, op_id):
    return {
        "source": source,
        "thread_id": native_id,
        "offset": offset,
        "prefix_sha256": prefix,
        "transcript": raw.decode("utf-8"),
        "client_op_id": op_id,
    }


def _memory_body(native_id, raw):
    return {
        "source": MEMORY_IMPORT_SOURCE,
        "threads": [
            {"thread_id": native_id, "transcript": raw.decode("utf-8"), "format": MEMORY_FORMAT}
        ],
    }


def body(op):
    """Reconstruct the exact request; appends after its immutable slice are allowed."""
    try:
        with _open(op.path) as stream:
            prefix = _hash_prefix(stream, op.offset)
            raw = stream.read(op.length)
        if len(raw) != op.length or hashlib.sha256(raw).hexdigest() != op.slice_sha256:
            raise SliceConflict("Pending source slice changed; operation kept")
        request = (
            _capture_body(op.source, op.native_id, op.offset, prefix, raw, op.op_id)
            if op.kind == "capture"
            else _memory_body(op.native_id, raw)
        )
        if body_digest(request) != op.body_sha256:
            raise SliceConflict("Pending request changed; operation kept")
        return request
    except (OSError, UnicodeError, SourceError) as error:
        if isinstance(error, SliceConflict):
            raise
        raise SliceConflict("Pending source unavailable; operation kept") from None


def _op(kind, source, path, native_id, offset, raw, prefix=None):
    op_id = uuid.uuid4().hex
    request = (
        _capture_body(source, native_id, offset, prefix, raw, op_id)
        if kind == "capture"
        else _memory_body(native_id, raw)
    )
    return Op(
        op_id,
        kind,
        source,
        str(path),
        native_id,
        offset,
        len(raw),
        hashlib.sha256(raw).hexdigest(),
        body_digest(request),
    )


def _capture_op(selected, state, target):
    cursor = state.cursor(selected.source, selected.path, selected.native_id)
    if cursor is None:
        return None
    with _open(selected.path) as stream:
        before = os.fstat(stream.fileno())
        offset = cursor["offset"]
        if offset > target or before.st_size < target:
            raise SliceConflict("Source shrank; saved cursor kept")
        prefix = _hash_prefix(stream, offset)
        if prefix != cursor["prefix_sha256"]:
            raise SliceConflict("Source prefix changed; saved cursor kept")
        raw = stream.read(min(target - offset, CHUNK_BYTES + MAX_RECORD_BYTES + 1))
        after = os.fstat(stream.fileno())
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        return None
    # A request ends on a complete physical record. Keep an unflushed UTF-8 tail local.
    end = raw.rfind(b"\n", 0, CHUNK_BYTES + MAX_RECORD_BYTES + 1) + 1
    if end == 0:
        if len(raw) > MAX_RECORD_BYTES:
            raise SourceError("Native record too large; use an explicit import")
        return None
    # Bound ordinary chunks, extending through at most one whole record.
    if len(raw) > CHUNK_BYTES:
        boundary = raw.find(b"\n", CHUNK_BYTES)
        if boundary != -1:
            end = boundary + 1
    return _op(
        "capture", selected.source, selected.path, selected.native_id, offset, raw[:end], prefix
    )


def _manifest_hashes(home):
    manifest = home / ".commonplace/onboarding.json"
    if not manifest.exists():
        return set()
    try:
        with _open(manifest) as stream:
            raw = stream.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError
        entries = json.loads(raw)["files"]
        return {row["original_sha256"] for row in entries.values() if "original_sha256" in row}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise SourceError("Onboarding manifest unavailable; memory scan refused") from None


def _memory_ops(config, state, now, targets):
    if not config.get("memories"):
        return
    home = Path(config.get("home", Path.home())).absolute()
    private = home / ".commonplace"
    originals = _manifest_hashes(home)
    for selected in config.get("memories", []):
        root = Path(selected["root"]).expanduser().absolute()
        if any(p.is_symlink() for p in (root, *root.parents)) or not root.is_dir():
            raise SourceError("Select a regular memory directory")
        for path in sorted(root.glob(selected.get("glob", "**/*.md"))):
            if path == private or private in path.parents:
                continue
            if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
                continue
            with _open(path) as stream:
                before = os.fstat(stream.fileno())
                if before.st_size > MEMORY_BYTES or now - before.st_mtime < 60:
                    continue
                if targets is not None and targets.get(str(path)) != before.st_size:
                    continue
                raw = stream.read(MEMORY_BYTES + 1)
                after = os.fstat(stream.fileno())
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                continue
            digest = hashlib.sha256(raw).hexdigest()
            if not raw or raw == POINTER.encode() or digest in originals:
                continue
            label, relpath = selected["label"], path.relative_to(root).as_posix()
            native_id = memory_thread_id(label, relpath, digest)
            if state.memory_seen(label, relpath, digest):
                continue
            yield _op("import", MEMORY_IMPORT_SOURCE, path, native_id, 0, raw)


def scan(config, state, *, now, targets=None) -> list[Op]:
    """Queue immutable metadata, captures first, bounded by the cycle's file ends.

    Unknown cursors are skipped. Call discover(), fetch GET /api/capture for each
    State.cursor(...) == None, and store_cursor() before scanning. targets maps
    absolute path strings to byte lengths; config['targets'] is also supported.
    """
    targets = targets if targets is not None else config.get("targets")
    occupied = {(op.source, op.native_id) for op in state.outbox_all()}
    for selected in discover(config):
        if (selected.source, selected.native_id) in occupied:
            continue
        target = selected.size if targets is None else targets.get(selected.path)
        if target is None:
            continue
        try:
            op = _capture_op(selected, state, target)
        except SliceConflict:
            state.set_status(selected.source, selected.path, selected.native_id, "conflict")
            raise
        if op:
            state.outbox_add(op)
            occupied.add((op.source, op.native_id))
    for op in _memory_ops(config, state, now, targets):
        if (op.source, op.native_id) not in occupied:
            state.outbox_add(op)
            occupied.add((op.source, op.native_id))
    return state.outbox_pending()


def snapshot_targets(config):
    """Freeze byte ends at cycle start for captures and selected memory files."""
    targets = {source.path: source.size for source in discover(config)}
    for selected in config.get("memories", []):
        root = Path(selected["root"]).expanduser().absolute()
        if any(p.is_symlink() for p in (root, *root.parents)):
            raise SourceError("Symlinked source refused")
        for path in root.glob(selected.get("glob", "**/*.md")):
            if not any(p.is_symlink() for p in (path, *path.parents)) and path.is_file():
                targets[str(path)] = path.stat().st_size
    return targets


def probe(path) -> dict:
    """Report JSON structure without values, filenames or unknown key names."""
    with _open(path) as stream:
        raw = stream.read(MAX_RECORD_BYTES + 1)
    if len(raw) > MAX_RECORD_BYTES:
        raise SourceError("Probe input too large")
    try:
        documents = [json.loads(raw)]
    except (ValueError, UnicodeError, RecursionError):
        try:
            documents = [json.loads(line) for line in raw.splitlines() if line.strip()]
        except (ValueError, UnicodeError, RecursionError):
            raise SourceError("Probe requires JSON or JSONL") from None
    keys, types = set(), set()
    pending = list(documents)
    while pending:
        value = pending.pop()
        types.add(type(value).__name__)
        if isinstance(value, dict):
            keys.update(
                key if key in PROBE_KEY_ALLOWLIST else hashlib.sha256(key.encode()).hexdigest()[:12]
                for key in value
            )
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return {"records": len(documents), "keys": sorted(keys), "types": sorted(types)}

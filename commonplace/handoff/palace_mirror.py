"""Mirror stored threads into the workspace palace. Owner: part T03.

Each chunk becomes one drawer in wing ``MIRROR_WING`` (``private_threads``), room
``<stored thread id>``, verbatim chunk text, so normal Memory search finds
old threads too. Written through the gateway's own worker client
(``call_tool(principal, "mempalace_add_drawer", args)``), so authorship is
server-stamped. Idempotent: a retry adds no duplicate drawers.

Best effort: the lossless copy is ``thread_store``; a mirror failure is
logged and reported, it does not fail the handoff.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from typing import Callable

from mempalace.lexical import ranking_stop_words, rerank_merged_hits
from mempalace.stored_text import INDEX_SPANS, IndexSource

from .models import CHUNK_MAX_CHARS, MIRROR_WING, RecallHit, StoredThread
from .thread_store import Chunk

CallTool = Callable  # (principal, tool_name, arguments) -> parsed result dict


class MirrorError(RuntimeError):
    """Memory could not mirror or search a stored thread."""


@dataclass(frozen=True)
class MirrorUnit(Chunk):
    message_class: str
    index_spans: str | None = None


def message_class(message) -> str:
    if message.kind == "tool_call":
        return "tool_call"
    if message.kind == "tool_result":
        return "tool_output"
    if message.kind == "system" or message.role == "system":
        return "system"
    return "conversation"


def class_units(messages: list, max_chars: int = CHUNK_MAX_CHARS) -> list[MirrorUnit]:
    """Keep whole messages together where possible, without mixing classes.

    Long messages retain one header, then verbatim continuation slices. Index
    provenance is computed before slicing, so a short continuation cannot
    independently accept or reject a base64 fragment.
    """
    if max_chars <= 0:
        raise ValueError("Chunk size must be positive")
    units = []
    pending = []
    length = 0
    kind = ""

    def append(text, start, end, kind, source=None, offset=0):
        source = source or IndexSource(text)
        part = (
            units[-1].index + 1
            if units and (units[-1].seq_start, units[-1].seq_end) == (start, end)
            else 0
        )
        units.append(
            MirrorUnit(
                part,
                start,
                end,
                text,
                kind,
                source.metadata(offset, offset + len(text)).get(INDEX_SPANS),
            )
        )

    def flush():
        nonlocal length
        if pending:
            append("\n".join(text for _, text in pending), pending[0][0], pending[-1][0], kind)
            pending.clear()
            length = 0

    for message in messages:
        next_kind = message_class(message)
        prefix = f"[{message.seq}] {message.role} {message.kind}"
        if message.name:
            prefix += f" {message.name}"
        text = prefix + ": " + message.text
        if pending and (next_kind != kind or length + 1 + len(text) > max_chars):
            flush()
        kind = next_kind
        if len(text) > max_chars:
            source = IndexSource(text)
            for offset in range(0, len(text), max_chars):
                append(
                    text[offset : offset + max_chars],
                    message.seq,
                    message.seq,
                    kind,
                    source,
                    offset,
                )
        else:
            length += len(text) + bool(pending)
            pending.append((message.seq, text))
    flush()
    return units


def _call(call_tool: CallTool, principal, name: str, arguments: dict, *, missing_ok=False) -> dict:
    try:
        result = call_tool(principal, name, arguments)
    except Exception as exc:
        raise MirrorError("Memory worker call failed") from exc
    if missing_ok and isinstance(result, dict) and result.get("error") == "No palace found":
        return {"drawers": [], "total": 0}
    if (
        not isinstance(result, dict)
        or result.get("error")
        or result.get("success") is False
        or result.get("isError")
    ):
        raise MirrorError("Memory worker returned an error")
    return result


def _legacy_mirrored_seq(call_tool, principal, stored):
    """Read existing origin metadata once; never retrofit mirrored drawers."""
    offset = 0
    completed = -1
    while True:
        page = _call(
            call_tool,
            principal,
            "mempalace_list_drawers",
            {"wing": MIRROR_WING, "room": stored.id, "limit": 100, "offset": offset},
            missing_ok=True,
        )
        drawers = page.get("drawers")
        if not isinstance(drawers, list):
            raise MirrorError("Memory worker returned invalid drawer metadata")
        for drawer in drawers:
            if drawer.get("wing") != MIRROR_WING or drawer.get("room") != stored.id:
                continue
            metadata = drawer.get("metadata") or {}
            source = metadata.get("source_file", "")
            if not isinstance(source, str) or not source.startswith(f"handoff:{stored.id}#"):
                continue
            label = _range_label(
                {"claimed_by": metadata.get("claimed_by") or metadata.get("added_by")}
            )
            if label is not None:
                completed = max(completed, label[1])
        offset += len(drawers)
        if not drawers or offset >= page.get("total", offset):
            return min(completed, stored.message_count - 1)


def mirror_pending(call_tool, principal, stored, store):
    """Resume a durable batch, then mirror later appends at a new boundary."""
    progress = store.mirror_progress(stored.id)
    bootstrap = _legacy_mirrored_seq(call_tool, principal, stored) if progress is None else -1
    drawer_ids = []
    while True:
        completed, through = store.begin_mirror(
            stored.id, stored.message_count - 1, bootstrap=bootstrap
        )
        if completed >= stored.message_count - 1:
            return drawer_ids
        messages = store.messages(stored.id, completed + 1, through)
        if not messages or messages[0].seq != completed + 1 or messages[-1].seq != through:
            raise MirrorError("Stored thread has an incomplete mirror range")
        drawer_ids.extend(mirror(call_tool, principal, stored, class_units(messages)))
        store.finish_mirror(stored.id, through)


def mirror(call_tool: CallTool, principal, stored: StoredThread, chunks: list) -> list[str]:
    """Return one drawer id per input chunk; identical text reuses an id."""
    if chunks and isinstance(chunks[0], MirrorUnit):
        drawer_ids = []
        for offset in range(0, len(chunks), 64):
            batch = chunks[offset : offset + 64]
            result = _call(
                call_tool,
                principal,
                "write_mirror_units",
                {
                    "thread_id": stored.id,
                    "units": [
                        {
                            "seq_start": unit.seq_start,
                            "seq_end": unit.seq_end,
                            "message_class": unit.message_class,
                            "part": unit.index,
                            "text": unit.text,
                            "index_spans": unit.index_spans,
                        }
                        for unit in batch
                    ],
                },
            )
            ids = result.get("drawer_ids")
            if (
                not isinstance(ids, list)
                or len(ids) != len(batch)
                or not all(isinstance(i, str) and i for i in ids)
            ):
                raise MirrorError("Memory worker returned invalid mirror ids")
            drawer_ids.extend(ids)
        return drawer_ids
    drawer_ids = []
    mirrored = {}
    for chunk in chunks:
        if chunk.text in mirrored:
            # Preserve the input-to-drawer mapping without another write.
            # The lossless store retains each repeated piece's full origin.
            drawer_ids.append(mirrored[chunk.text])
            continue
        try:
            chunk.text.encode("utf-8")
        except UnicodeEncodeError as exc:
            # add_drawer strips lone surrogates; fail instead of mirroring
            # different words from the lossless thread store.
            raise MirrorError("Memory cannot mirror this text verbatim") from exc
        arguments = {
            "wing": MIRROR_WING,
            "room": stored.id,
            "content": chunk.text,
            "source_file": f"handoff:{stored.id}#{chunk.index}",
            "added_by": f"handoff:{chunk.seq_start}-{chunk.seq_end}:{len(chunk.text)}",
        }
        result = _call(
            call_tool,
            principal,
            "mempalace_add_drawer",
            arguments,
        )
        drawer_id = result.get("drawer_id")
        if not isinstance(drawer_id, str) or not drawer_id:
            raise MirrorError("Memory worker returned no entry id")
        mirrored[chunk.text] = drawer_id
        drawer_ids.append(drawer_id)
    return drawer_ids


_RANGE = re.compile(r"handoff:([0-9]+)-([0-9]+):([0-9]+)\Z")
_SEQUENCE = re.compile(r"^\[([0-9]+)\] ", re.MULTILINE)


def _range_label(hit: dict):
    label = hit.get("claimed_by", "")
    match = _RANGE.fullmatch(label) if isinstance(label, str) else None
    if match is None:
        return None
    start, end, length = map(int, match.groups())
    if start > end:
        raise MirrorError("Memory entry has an invalid message range")
    return start, end, length


def _score(hit: dict) -> float:
    for key, sign in (("score", 1), ("similarity", 1), ("distance", -1)):
        value = hit.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return sign * float(value)
    return 0.0


def _chunk_text(call_tool: CallTool, principal, hit: dict, label, hydrated: dict) -> str:
    text = hit.get("text")
    if not isinstance(text, str):
        raise MirrorError("Memory entry has no text")
    if label is None or len(text) == label[2]:
        return text

    # Search reports the logical drawer id even when the worker split the
    # mirrored chunk. Fetch it once to reassemble the complete original text.
    drawer_id = hit.get("drawer_id")
    if not isinstance(drawer_id, str) or not drawer_id:
        raise MirrorError("Memory entry has no id")
    if drawer_id not in hydrated:
        hydrated[drawer_id] = _call(
            call_tool, principal, "mempalace_get_drawer", {"drawer_id": drawer_id}
        )
    result = hydrated[drawer_id]
    metadata = result.get("metadata")
    if not isinstance(metadata, dict):
        raise MirrorError("Memory entry has no origin")
    if (
        result.get("wing") != MIRROR_WING
        or result.get("room") != hit["room"]
        or metadata.get("source_file") != (hit.get("source_path") or hit.get("source_file"))
    ):
        raise MirrorError("Memory entry not found")
    text = result.get("content")
    if not isinstance(text, str) or len(text) != label[2]:
        raise MirrorError("Memory entry is incomplete")
    return text


def palace_search(
    call_tool: CallTool, principal, stored_ids: list[str], query: str, limit: int = 8
) -> list[RecallHit]:
    """Default hybrid search over mirrored chunks of ``stored_ids`` only."""
    if limit <= 0:
        return []
    fetch_limit = min(limit * 4, 50)
    hits = {}
    components = {}
    hydrated = {}
    first = None
    for stored_id in dict.fromkeys(stored_ids):
        result = _call(
            call_tool,
            principal,
            "mempalace_search",
            {
                "query": query,
                "wing": MIRROR_WING,
                "room": stored_id,
                "limit": fetch_limit,
                "include_facts": False,
            },
        )
        if first is None:
            first = result
        results = result.get("results")
        if not isinstance(results, list):
            raise MirrorError("Memory worker returned invalid search results")
        origin = re.compile(
            r"handoff:"
            + re.escape(stored_id)
            + r"#(?:v2:[0-9]+-[0-9]+:(?:conversation|tool_call|tool_output|system):)?([0-9]+)\Z"
        )
        for hit in results:
            if not isinstance(hit, dict):
                raise MirrorError("Memory worker returned an invalid search entry")
            # WorkerClient bypasses token policy: never return another room's
            # content, even if a backend accidentally ignores a search filter.
            if hit.get("wing") != MIRROR_WING or hit.get("room") != stored_id:
                continue
            source = hit.get("source_path") or hit.get("source_file")
            match = origin.fullmatch(source) if isinstance(source, str) else None
            if match is None:
                continue
            index = int(match.group(1))
            label = _range_label(hit)
            text = _chunk_text(call_tool, principal, hit, label, hydrated)
            if label is not None:
                start, end = label[:2]
            else:
                sequences = [int(seq) for seq in _SEQUENCE.findall(text)]
                if not sequences:
                    raise MirrorError("Memory entry has no message range")
                start, end = min(sequences), max(sequences)
            recalled = RecallHit(stored_id, index, start, end, _score(hit), text)
            key = (stored_id, source)
            if key not in hits or recalled.score > hits[key].score:
                hits[key] = recalled
                components[key] = {**hit, "text": text}
    rerank_merged_hits(
        (first or {}).get("query", query),
        list(components.values()),
        stop_words=ranking_stop_words(first or {}),
    )
    for key, hit in components.items():
        hits[key] = replace(hits[key], score=_score(hit))
    unique = []
    for hit in sorted(hits.values(), key=lambda hit: hit.score, reverse=True):
        if not any(
            hit.stored_thread_id == previous.stored_thread_id
            and hit.seq_start <= previous.seq_end
            and previous.seq_start <= hit.seq_end
            for previous in unique
        ):
            unique.append(hit)
    return unique[:limit]

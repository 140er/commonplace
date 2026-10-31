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
from typing import Callable

from .models import MIRROR_WING, RecallHit, StoredThread

CallTool = Callable  # (principal, tool_name, arguments) -> parsed result dict


class MirrorError(RuntimeError):
    """Memory could not mirror or search a stored thread."""


def _call(call_tool: CallTool, principal, name: str, arguments: dict) -> dict:
    try:
        result = call_tool(principal, name, arguments)
    except Exception as exc:
        raise MirrorError("Memory worker call failed") from exc
    if (
        not isinstance(result, dict)
        or result.get("error")
        or result.get("success") is False
        or result.get("isError")
    ):
        raise MirrorError("Memory worker returned an error")
    return result


def mirror(call_tool: CallTool, principal, stored: StoredThread, chunks: list) -> list[str]:
    """Return one drawer id per input chunk; identical text reuses an id."""
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
        result = _call(
            call_tool,
            principal,
            "mempalace_add_drawer",
            {
                "wing": MIRROR_WING,
                "room": stored.id,
                "content": chunk.text,
                "source_file": f"handoff:{stored.id}#{chunk.index}",
                # add_drawer has no arbitrary metadata argument. The worker
                # retains this label as claimed_by, separately from the
                # verified author. Keep ranges for continuation chunks (which
                # may have no [seq] header), and length to detect worker splits.
                "added_by": f"handoff:{chunk.seq_start}-{chunk.seq_end}:{len(chunk.text)}",
            },
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
    for key, sign in (("similarity", 1), ("score", 1), ("distance", -1)):
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
    """Semantic search over mirrored chunks of ``stored_ids`` only."""
    if limit <= 0:
        return []
    fetch_limit = min(limit * 4, 50)
    hits = {}
    hydrated = {}
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
        results = result.get("results")
        if not isinstance(results, list):
            raise MirrorError("Memory worker returned invalid search results")
        origin = re.compile(r"handoff:" + re.escape(stored_id) + r"#([0-9]+)\Z")
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
            key = (stored_id, index)
            if key not in hits or recalled.score > hits[key].score:
                hits[key] = recalled
    return sorted(hits.values(), key=lambda hit: hit.score, reverse=True)[:limit]

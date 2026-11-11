"""Path contracts: the repo paths a card declares it will edit.

A card's contract is a short list of glob patterns (``src/api/**``, ``docs/*.md``). Two
cards whose contracts can match the same file must not run at the same time; the
orchestrator start (``commonplace.orch.runner_bridge``) uses ``first_overlap`` for that.
A card without a contract claims nothing and is never refused by this check.

Matching is deliberately conservative: when two patterns *might* match one path they
overlap. A false overlap only serialises two cards; a false "disjoint" is the collision
this check exists to prevent.
"""

from __future__ import annotations

import fnmatch
import json

from commonplace.extensions import ToolError

MAX_GLOBS = 32
MAX_GLOB_LENGTH = 200
_WILDCARDS = frozenset("*?[")


def normalize(value) -> list[str]:
    """Validated contract: a list of relative, forward-slash globs, de-duplicated in
    order. An empty list clears the contract. Raises ``ToolError`` otherwise."""
    if not isinstance(value, list) or len(value) > MAX_GLOBS:
        raise ToolError(f"no: paths must be a list of at most {MAX_GLOBS} globs")
    globs: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ToolError("no: paths must be a list of strings")
        glob = item.strip()
        while glob.startswith("./"):
            glob = glob[2:]
        glob = glob.rstrip("/") if glob != "/" else glob
        if not glob or len(glob) > MAX_GLOB_LENGTH or any(ord(c) < 32 for c in glob):
            raise ToolError(f"no: a path glob must be 1-{MAX_GLOB_LENGTH} printable characters")
        if glob.startswith("/") or "\\" in glob or ".." in glob.split("/"):
            raise ToolError("no: a path glob is relative to the repo root (no /, \\ or ..)")
        if glob not in globs:
            globs.append(glob)
    return globs


def column_update(args) -> dict:
    """``{"paths": <stored JSON>}`` when ``args`` carries a contract, else nothing."""
    return {"paths": json.dumps(normalize(args["paths"]))} if "paths" in args else {}


def _segments_overlap(a: str, b: str) -> bool:
    if a == b:
        return True
    a_wild, b_wild = bool(_WILDCARDS & set(a)), bool(_WILDCARDS & set(b))
    if a_wild and b_wild:
        return True  # two patterns may share a name; assume they do
    if a_wild:
        return fnmatch.fnmatchcase(b, a)
    if b_wild:
        return fnmatch.fnmatchcase(a, b)
    return False


def globs_overlap(a: str, b: str) -> bool:
    """True when some path could match both globs. ``**`` matches any depth, and a glob
    that ends where the other goes deeper owns that directory (``docs`` vs
    ``docs/a.md``)."""
    left, right = a.split("/"), b.split("/")
    for x, y in zip(left, right):
        if x == "**" or y == "**":
            return True
        if not _segments_overlap(x, y):
            return False
    return True


def first_overlap(mine, theirs) -> tuple[str, str] | None:
    """The first ``(my glob, their glob)`` pair that can match the same path, or None.
    Either side empty means no contract, so no overlap."""
    for a in mine or ():
        for b in theirs or ():
            if globs_overlap(a, b):
                return a, b
    return None


def parse(raw) -> list[str]:
    """Stored column value (JSON text or NULL) to a glob list; unreadable means none."""
    if not raw:
        return []
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return []
    return [g for g in value if isinstance(g, str)] if isinstance(value, list) else []

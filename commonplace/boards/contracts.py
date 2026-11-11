"""Path contracts: the repo paths a card declares it will edit.

A card's contract is a short list of glob patterns (``src/api/**``, ``docs/*.md``,
``src/*.{ts,tsx}``). Two cards whose contracts can match the same file must not run at
the same time; the start guards (``commonplace.orch.runner_bridge.contract_refusal``)
use ``first_overlap`` for that. A card without a contract claims nothing and is never
refused by this check.

Matching is deliberately conservative: when two patterns *might* match one path they
overlap. A false overlap only serialises two cards; a false "disjoint" is the collision
this check exists to prevent. So:

- globs are canonical before they are compared: empty and ``.`` segments are dropped,
  ``name/..`` collapses, and the repo root (``.``) is ``**``;
- ``{a,b}`` alternatives are expanded (at most ``MAX_EXPANSIONS``; beyond that, or when
  unbalanced, braces count as wildcards);
- names compare case-insensitively, because a checkout on a case-insensitive file system
  maps ``Docs/a.md`` and ``docs/a.md`` to one file.
"""

from __future__ import annotations

import fnmatch
import json

from commonplace.extensions import ToolError

MAX_GLOBS = 32
MAX_GLOB_LENGTH = 200
MAX_EXPANSIONS = 64
_WILDCARDS = frozenset("*?[{}")


def _canonical(glob: str, *, strict: bool) -> list[str]:
    """Segments of ``glob`` without empty and ``.`` parts, ``name/..`` collapsed. A
    ``..`` that has no literal segment to cancel (it would leave the repo, or follows a
    wildcard) is refused when ``strict``, and otherwise widened to ``**``."""
    out: list[str] = []
    for segment in glob.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if out and not _WILDCARDS & set(out[-1]):
                out.pop()
                continue
            if strict:
                raise ToolError(
                    "no: a path glob must stay inside the repo (no leading or wildcard ..)"
                )
            return ["**"]
        out.append(segment)
    return out


def normalize(value) -> list[str]:
    """Validated contract: a list of relative, canonical, forward-slash globs,
    de-duplicated in order. An empty list clears the contract. Raises ``ToolError``
    otherwise."""
    if not isinstance(value, list) or len(value) > MAX_GLOBS:
        raise ToolError(f"no: paths must be a list of at most {MAX_GLOBS} globs")
    globs: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ToolError("no: paths must be a list of strings")
        glob = item.strip()
        if not glob or len(glob) > MAX_GLOB_LENGTH or any(ord(c) < 32 for c in glob):
            raise ToolError(f"no: a path glob must be 1-{MAX_GLOB_LENGTH} printable characters")
        if glob.startswith("/") or "\\" in glob:
            raise ToolError("no: a path glob is relative to the repo root (no / or \\)")
        canonical = "/".join(_canonical(glob, strict=True)) or "**"  # "." is the repo root
        if canonical not in globs:
            globs.append(canonical)
    return globs


def column_update(args) -> dict:
    """``{"paths": <stored JSON>}`` when ``args`` carries a contract, else nothing."""
    return {"paths": json.dumps(normalize(args["paths"]))} if "paths" in args else {}


def _expand(glob: str) -> list[str]:
    """``{a,b}`` alternatives of ``glob`` (nested braces included). A group that is
    unbalanced or has no comma stays as it is, and so does ``glob`` when it would expand
    to more than ``MAX_EXPANSIONS`` globs; leftover braces then count as wildcards."""
    pending, done = [glob], []
    while pending:
        current = pending.pop()
        start = current.find("{")
        if start < 0:
            done.append(current)
            continue
        depth, commas, end = 0, [], -1
        for i in range(start, len(current)):
            char = current[i]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
            elif char == "," and depth == 1:
                commas.append(i)
        if end < 0 or not commas:
            done.append(current)
            continue
        bounds = [start, *commas, end]
        head, tail = current[:start], current[end + 1 :]
        pending.extend(head + current[a + 1 : b] + tail for a, b in zip(bounds, bounds[1:]))
        if len(done) + len(pending) > MAX_EXPANSIONS:
            return [glob]
    return done


def _segments_overlap(a: str, b: str) -> bool:
    a, b = a.casefold(), b.casefold()
    if a == b:
        return True
    if "{" in a or "}" in a or "{" in b or "}" in b:
        return True  # braces that could not be expanded: may match anything
    a_wild, b_wild = bool(_WILDCARDS & set(a)), bool(_WILDCARDS & set(b))
    if a_wild and b_wild:
        return True  # two patterns may share a name; assume they do
    if a_wild:
        return fnmatch.fnmatchcase(b, a)
    if b_wild:
        return fnmatch.fnmatchcase(a, b)
    return False


def _overlap(a: str, b: str) -> bool:
    left, right = _canonical(a, strict=False), _canonical(b, strict=False)
    for x, y in zip(left, right):
        if x == "**" or y == "**":
            return True
        if not _segments_overlap(x, y):
            return False
    return True


def globs_overlap(a: str, b: str) -> bool:
    """True when some path could match both globs. ``**`` matches any depth, a glob that
    ends where the other goes deeper owns that directory (``docs`` vs ``docs/a.md``), and
    the repo root overlaps everything."""
    return any(_overlap(x, y) for x in _expand(a) for y in _expand(b))


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

"""Path contracts: the repo paths a card declares it will edit.

A card's contract is a short list of glob patterns (``src/api/**``, ``docs/*.md``,
``src/*.{ts,tsx}``). Two cards whose contracts can match the same file must not run at
the same time; the start guards (``commonplace.orch.runner_bridge.contract_refusal``)
use ``first_overlap`` for that. A card without a contract claims nothing and is never
refused by this check.

Matching is deliberately conservative: when two patterns *might* match one path they
overlap. A false overlap only serialises two cards; a false "disjoint" is the collision
this check exists to prevent. So:

- globs are stored canonical: empty and ``.`` segments are dropped, ``name/..``
  collapses, the repo root (``.``) is ``**``, and ``{a,b}`` alternatives are expanded
  into separate globs (``MAX_STORED`` in all). A ``..`` that would leave the repo, an
  unbalanced brace group and an over-large expansion are refused at store time;
- a stored value that is not canonical anyway (written outside ``normalize``) is read
  defensively: an unreadable value, an unbalanced group, an over-cap expansion and a
  ``..`` that cannot collapse all count as ``**``, so they overlap everything;
- names compare case-insensitively, because a checkout on a case-insensitive file system
  maps ``Docs/a.md`` and ``docs/a.md`` to one file;
- one comparison is bounded (``MAX_COMPARISONS`` glob pairs). Beyond that it fails
  closed (``TooLarge``) instead of holding the runner lock.
"""

from __future__ import annotations

import fnmatch
import functools
import json

from commonplace.extensions import ToolError

MAX_GLOBS = 32  # globs in one update
MAX_EXPANSIONS = 64  # alternatives one glob may expand to
MAX_STORED = 64  # globs stored per card, after expansion
MAX_GLOB_LENGTH = 200
MAX_COMPARISONS = 200_000  # glob pairs compared by one start
_WILDCARDS = frozenset("*?[{}")
ANY = ("**",)


class TooLarge(Exception):
    """A start would compare more glob pairs than ``MAX_COMPARISONS``."""


class Budget:
    """Glob pairs one start may still compare."""

    def __init__(self, pairs: int = MAX_COMPARISONS):
        self.left = pairs

    def take(self, pairs: int) -> None:
        self.left -= pairs
        if self.left < 0:
            raise TooLarge


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


def _expand(glob: str, *, strict: bool) -> list[str]:
    """``{a,b}`` alternatives of ``glob`` (nested groups included). An unbalanced group,
    a group without a comma, a stray brace or more than ``MAX_EXPANSIONS`` alternatives is
    refused when ``strict``, and otherwise stands for ``**`` (it matches anything)."""
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
            return _unexpandable("unbalanced or comma-less brace group", strict)
        bounds = [start, *commas, end]
        head, tail = current[:start], current[end + 1 :]
        pending.extend(
            reversed([head + current[a + 1 : b] + tail for a, b in zip(bounds, bounds[1:])])
        )
        if len(done) + len(pending) > MAX_EXPANSIONS:
            return _unexpandable(f"more than {MAX_EXPANSIONS} brace alternatives", strict)
    if any("}" in alternative for alternative in done):
        return _unexpandable("stray closing brace", strict)
    return done


def _unexpandable(reason: str, strict: bool) -> list[str]:
    if strict:
        raise ToolError(f"no: path glob braces are not usable ({reason})")
    return ["**"]


def normalize(value) -> list[str]:
    """Validated contract: a list of relative, canonical, brace-free, forward-slash globs,
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
        for alternative in _expand(glob, strict=True):
            canonical = "/".join(_canonical(alternative, strict=True)) or "**"  # "." is the root
            if canonical not in globs:
                globs.append(canonical)
    if len(globs) > MAX_STORED:
        raise ToolError(f"no: paths expand to more than {MAX_STORED} globs")
    return globs


def column_update(args) -> dict:
    """``{"paths": <stored JSON>}`` when ``args`` carries a contract, else nothing."""
    return {"paths": json.dumps(normalize(args["paths"]))} if "paths" in args else {}


@functools.lru_cache(maxsize=8192)
def _alternatives(glob: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """``(alternative, casefolded canonical segments)`` for each way ``glob`` can read."""
    return tuple(
        (alternative, tuple(s.casefold() for s in _canonical(alternative, strict=False)))
        for alternative in _expand(glob, strict=False)
    )


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


def _overlap(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
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
    return any(_overlap(x, y) for _, x in _alternatives(a) for _, y in _alternatives(b))


def first_overlap(mine, theirs, budget: Budget | None = None) -> tuple[str, str] | None:
    """The first ``(my glob, their glob)`` pair that can match the same path, or None.
    Either side empty means no contract, so no overlap. Each glob is expanded once; with a
    ``budget`` the pairs are counted first and ``TooLarge`` is raised when they exceed it."""
    left = [(g, s) for glob in mine or () for g, s in _alternatives(glob)]
    right = [(g, s) for glob in theirs or () for g, s in _alternatives(glob)]
    if budget is not None:
        budget.take(len(left) * len(right))
    for a, x in left:
        for b, y in right:
            if _overlap(x, y):
                return a, b
    return None


def parse(raw) -> list[str] | None:
    """Stored column value (JSON text or NULL) to a glob list. NULL, empty and ``[]`` are
    no contract (``[]``); a value that is not a JSON list of strings is unreadable
    (``None``), and callers treat that as ``**`` rather than as no contract."""
    if not raw:
        return []
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    if not isinstance(value, list) or not all(isinstance(g, str) for g in value):
        return None
    return value

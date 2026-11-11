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
- one start spends a budget of ``MAX_STEPS`` work units, set from timings on one host
  (see SYSTEM.md for the measured figures; a slower host takes proportionally longer).
  It bills every running card's stored size (``size_units``) before that card's contract
  is parsed or expanded, ``COMPILE_UNITS`` for each wildcard segment it has to compile,
  and each comparison as pairs times the shallower side's deepest glob times the heaviest
  segment of either side (one unit per segment plus one per ``SEGMENT_CHARS`` characters,
  because a segment's cost grows with its length). Beyond the budget it fails closed
  (``TooLarge``) instead of holding the runner lock.
"""

from __future__ import annotations

import fnmatch
import functools
import json
import re

from commonplace.extensions import ToolError

MAX_GLOBS = 32  # globs in one update
MAX_EXPANSIONS = 64  # alternatives one glob may expand to
MAX_STORED = 64  # globs stored per card, after expansion
MAX_GLOB_LENGTH = 200
MAX_STEPS = 2_500_000  # work units one start may spend (the measured time is in the docs)
SEGMENT_CHARS = 32  # characters that add one work unit to a segment
COMPILE_UNITS = 150  # work units billed for compiling one wildcard segment
MAX_MATCHERS = 8192  # compiled wildcard segments kept
_WILDCARDS = frozenset("*?[{}")
ANY = ("**",)


class TooLarge(Exception):
    """A start would compare more work units than ``MAX_STEPS``."""


class Budget:
    """Work units one start may still compare."""

    def __init__(self, steps: int = MAX_STEPS):
        self.left = steps

    def take(self, steps: int) -> None:
        self.left -= steps
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
        options = [current[a + 1 : b] for a, b in zip(bounds, bounds[1:])]
        if strict and any(option.startswith("/") for option in options):
            raise ToolError("no: a path glob is relative to the repo root (no / or \\)")
        pending.extend(reversed([head + option + tail for option in options]))
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
            if alternative.startswith("/"):  # an empty group option can leave one in front
                raise ToolError("no: a path glob is relative to the repo root (no / or \\)")
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
        (alternative, tuple(_segment(s) for s in _canonical(alternative, strict=False)))
        for alternative in _expand(glob, strict=False)
    )


def _segment(text: str) -> tuple[str, bool]:
    """A casefolded segment and whether it holds a wildcard, decided once per glob so a
    comparison builds no sets."""
    text = text.casefold()
    return text, bool(_WILDCARDS & set(text))


def _heaviest(segments) -> int:
    """Work units of the longest segment of a glob."""
    return max((1 + len(text) // SEGMENT_CHARS for text, _ in segments), default=1)


_MATCHERS: dict = {}  # wildcard segment -> compiled matcher; bounded, see _matcher


def _matcher(pattern: str):
    """The compiled matcher of a wildcard segment (what ``fnmatch.fnmatchcase`` builds).
    Compiling is the expensive part, so ``_compile_charge`` bills it before it happens."""
    found = _MATCHERS.get(pattern)
    if found is None:
        if len(_MATCHERS) >= MAX_MATCHERS:
            _MATCHERS.clear()
        found = _MATCHERS[pattern] = re.compile(fnmatch.translate(pattern)).match
    return found


def _overlap(left, right) -> bool:
    for (a, a_wild), (b, b_wild) in zip(left, right):
        if a == "**" or b == "**":
            return True
        if a == b:
            continue
        if a_wild and b_wild:
            continue  # two patterns may share a name; assume they do
        if a_wild:
            if _matcher(a)(b):
                continue
        elif b_wild:
            if _matcher(b)(a):
                continue
        return False
    return True


def globs_overlap(a: str, b: str) -> bool:
    """True when some path could match both globs. ``**`` matches any depth, a glob that
    ends where the other goes deeper owns that directory (``docs`` vs ``docs/a.md``), and
    the repo root overlaps everything."""
    return any(_overlap(x, y) for _, x in _alternatives(a) for _, y in _alternatives(b))


def size_units(stored) -> int:
    """Work units to read a stored contract (JSON text or a list of globs): one, plus one
    per glob, per ``/`` and per ``SEGMENT_CHARS`` characters. A start bills this for every
    running card before it parses or expands that card's contract."""
    text = stored if isinstance(stored, str) else json.dumps(stored)
    return 1 + text.count('"') // 2 + text.count("/") + len(text) // SEGMENT_CHARS


def _prepare(globs):
    """``(alternative, segments)`` pairs of ``globs``, the deepest glob and the heaviest
    segment (work units), all computed once per contract."""
    pairs = [(g, s) for glob in globs or () for g, s in _alternatives(glob)]
    depth = max((len(x) for _, x in pairs), default=0)
    weight = max((_heaviest(x) for _, x in pairs), default=1)
    return pairs, depth, weight


def _compile_charge(pairs, budget: Budget) -> None:
    """Bill, and build, the matcher of every wildcard segment not compiled yet."""
    for _, segments in pairs:
        for text, wild in segments:
            if wild and text != "**" and text not in _MATCHERS:
                budget.take(COMPILE_UNITS)
                _matcher(text)


def _first(left, right, budget: Budget | None):
    (a_pairs, a_depth, a_weight), (b_pairs, b_depth, b_weight) = left, right
    if not a_pairs or not b_pairs:
        return None
    if budget is not None:
        _compile_charge(b_pairs, budget)
        depth = max(min(a_depth, b_depth), 1)
        budget.take(len(a_pairs) * len(b_pairs) * depth * max(a_weight, b_weight))
    for a, x in a_pairs:
        for b, y in b_pairs:
            if _overlap(x, y):
                return a, b
    return None


def first_overlap(mine, theirs, budget: Budget | None = None) -> tuple[str, str] | None:
    """The first ``(my glob, their glob)`` pair that can match the same path, or None.
    Either side empty means no contract, so no overlap. With a ``budget`` the work is
    charged first and ``TooLarge`` is raised when it exceeds the budget: each wildcard
    segment that is not compiled yet (``COMPILE_UNITS``), then the pairs times the shallower
    side's deepest glob times the heaviest segment of either side."""
    left = _prepare(mine)
    if budget is not None:
        _compile_charge(left[0], budget)
    return _first(left, _prepare(theirs), budget)


def first_overlap_running(mine, running, budget: Budget):
    """Check ``mine`` against the stored contracts of the running cards, ``(key, stored)``
    each. Every card is billed its stored size (``size_units``) before its contract is
    parsed or expanded, then its compiles and comparisons. Returns ``(key, globs, pair)``
    for the first overlap (an unreadable stored contract reads as ``**``) or None; raises
    ``TooLarge`` with the ``key`` of the card the budget ran out on."""
    budget.take(size_units(mine))
    left = _prepare(mine)
    _compile_charge(left[0], budget)
    for key, stored in running:
        try:
            budget.take(size_units(stored))
            globs = parse(stored)
            if globs is None:
                globs = list(ANY)
            found = _first(left, _prepare(globs), budget)
        except TooLarge as exc:
            exc.key = key
            raise
        if found is not None:
            return key, globs, found
    return None


def parse(raw) -> list[str] | None:
    """Stored column value (JSON text or NULL) to a glob list. Only NULL and ``[]`` are no
    contract; anything that is not a JSON list of strings, an empty string included, is
    unreadable (``None``), and callers treat that as ``**`` rather than as no contract."""
    if raw is None:
        return []
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    if not isinstance(value, list) or not all(isinstance(g, str) for g in value):
        return None
    return value

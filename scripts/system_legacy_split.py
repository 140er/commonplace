#!/usr/bin/env python3
"""Move the legacy ``### `` decisions of ``SYSTEM.md`` into ``docs/system/<slug>.md`` files.

``extract`` rewrites ``SYSTEM.md`` in place: every selected ``### <title>`` section (the
heading line through its last non-blank line, ``####`` subsections included) becomes a
two-line stub that keeps the heading, so its anchor and place in the index do not change,
and the section text moves byte for byte into ``docs/system/<slug>.md``. A manifest
(``tests/fixtures/system_legacy_part<N>.json``) records title, slug, size and SHA-256 per
section.  ``verify`` rebuilds the original ``SYSTEM.md`` from the stubs and the files and
compares it, byte for byte, with the file at a git revision, so a dropped, altered or
duplicated section fails.

    python scripts/system_legacy_split.py extract --base origin/develop --part 1 --of 4
    python scripts/system_legacy_split.py verify  --base <manifest "base" sha> --part 1

Selection is by order and size: part ``k`` of ``n`` takes the contiguous run of sections
whose cumulative size falls into the k-th n-th of all ``### `` bytes, so the parts together
cover every section exactly once. Sections already moved (stubs) are skipped.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

SYSTEM = "SYSTEM.md"
SYSTEM_DIR = "docs/system"
MANIFEST_DIR = "tests/fixtures"
STUB_PREFIX = "Moved to "
STUB_SUFFIX = "; edit it there."
SLUG_MAX = 56
_SLUG_JUNK = re.compile(r"[^a-z0-9]+")
_STUB = re.compile(
    r"^### (?P<title>.+)\n\nMoved to \[`docs/system/(?P<slug>[a-z0-9-]+)\.md`\]"
    r"\(docs/system/(?P=slug)\.md\); edit it there\.$",
    re.MULTILINE,
)


@dataclass
class Section:
    title: str
    start: int  # first line index of the heading
    end: int  # one past the last non-blank line
    text: str  # heading through last non-blank line, joined with newlines, no trailing newline

    @property
    def size(self) -> int:
        return len(self.text.encode()) + 1


def sections_of(text: str) -> list[Section]:
    """Every ``### `` section (stubs included); ``## `` and ``### `` headings end a section."""
    lines = text.split("\n")
    heads = [i for i, line in enumerate(lines) if line.startswith(("## ", "### "))]
    found = []
    for pos, start in enumerate(heads):
        if not lines[start].startswith("### "):
            continue
        stop = heads[pos + 1] if pos + 1 < len(heads) else len(lines)
        end = stop
        while end > start + 1 and not lines[end - 1].strip():
            end -= 1
        found.append(Section(lines[start][4:].strip(), start, end, "\n".join(lines[start:end])))
    return found


def is_stub(section: Section) -> bool:
    return _STUB.fullmatch(section.text) is not None


def slug_of(title: str, taken: set[str]) -> str:
    base = _SLUG_JUNK.sub("-", title.lower()).strip("-")
    if len(base) > SLUG_MAX:
        base = base[:SLUG_MAX].rsplit("-", 1)[0] or base[:SLUG_MAX]
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    taken.add(slug)
    return slug


def stub_for(title: str, slug: str) -> str:
    link = f"docs/system/{slug}.md"
    return f"### {title}\n\n{STUB_PREFIX}[`{link}`]({link}){STUB_SUFFIX}"


def select(sections: list[Section], part: int, of: int) -> list[Section]:
    """Contiguous run of the ``part``-th of ``of`` size slices (1-based) over all sections."""
    total = sum(s.size for s in sections)
    chosen, seen = [], 0
    for section in sections:
        slot = min(of, seen * of // total + 1)
        if slot == part and not is_stub(section):
            chosen.append(section)
        seen += section.size
    return chosen


def plan(text: str, taken: set[str]) -> dict[int, str]:
    """Slug of every section still in full, keyed by its heading line, assigned in file order.

    One pass over all sections, so a duplicated title gets ``-2`` on its second occurrence and
    no part can collide with another part or with a file already in ``docs/system/``.
    """
    taken = set(taken)
    return {s.start: slug_of(s.title, taken) for s in sections_of(text) if not is_stub(s)}


def split_text(text: str, part: int, of: int, taken: set[str]):
    """``(index_text, [(Section, slug)])``: the text with part ``part`` of ``of`` stubbed out."""
    slugs = plan(text, taken)
    chosen = select(sections_of(text), part, of)
    lines = text.split("\n")
    for section in sorted(chosen, key=lambda s: s.start, reverse=True):
        lines[section.start : section.end] = stub_for(section.title, slugs[section.start]).split(
            "\n"
        )
    return "\n".join(lines), [(s, slugs[s.start]) for s in chosen]


def rebuild(index_text: str, fragments: dict[str, str]) -> str:
    """The text before the split: each stub replaced by its file's section text."""
    return _STUB.sub(lambda match: fragments[match["slug"]].rstrip("\n"), index_text)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _git_show(repo: Path, ref: str, path: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), "show", f"{ref}:{path}"], capture_output=True, check=True
    )
    return out.stdout.decode()


def _rev(repo: Path, ref: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", ref], capture_output=True, check=True
    )
    return out.stdout.decode().strip()


def load_fragments(repo: Path) -> dict[str, str]:
    return {p.stem: p.read_text() for p in sorted((repo / SYSTEM_DIR).glob("*.md"))}


def extract(repo: Path, base: str, part: int, of: int) -> dict:
    """Split part ``part`` of ``of`` out of ``SYSTEM.md`` at ``base`` and write the result."""
    original = _git_show(repo, base, SYSTEM)
    taken = {p.stem for p in (repo / SYSTEM_DIR).glob("*.md")}
    index, moved = split_text(original, part, of, taken)
    occurrences = Counter(s.title for s in sections_of(original))
    entries = []
    for section, slug in moved:
        (repo / SYSTEM_DIR / f"{slug}.md").write_text(section.text + "\n")
        entries.append(
            {
                "title": section.title,
                "slug": slug,
                "occurrences": occurrences[section.title],
                "bytes": section.size,
                "sha256": sha(section.text + "\n"),
            }
        )
    (repo / SYSTEM).write_text(index)
    manifest = {
        "base": _rev(repo, base),
        "base_sha256": sha(original),
        "part": part,
        "of": of,
        "sections": entries,
    }
    path = repo / MANIFEST_DIR / f"system_legacy_part{part}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def verify(repo: Path, base: str, part: int) -> list[str]:
    """Problems rebuilding the original ``SYSTEM.md`` at ``base``; ``[]`` means lossless."""
    path = repo / MANIFEST_DIR / f"system_legacy_part{part}.json"
    if not path.exists():
        return [f"{path.name}: manifest missing"]
    manifest = json.loads(path.read_text())
    fragments = load_fragments(repo)
    problems = []
    for entry in manifest["sections"]:
        body = fragments.get(entry["slug"])
        if body is None:
            problems.append(f"{entry['slug']}: file missing")
        elif sha(body) != entry["sha256"]:
            problems.append(f"{entry['slug']}: text differs from the extracted section")
    index = (repo / SYSTEM).read_text()
    stubs = [m["slug"] for m in _STUB.finditer(index)]
    if sorted(stubs) != sorted(e["slug"] for e in manifest["sections"]):
        problems.append("stubs in SYSTEM.md differ from the manifest (dropped or duplicated)")
    if problems:
        return problems
    original = _git_show(repo, base, SYSTEM)
    if sha(original) != manifest["base_sha256"]:
        problems.append("base revision holds a different SYSTEM.md than the extraction saw")
    if rebuild(index, fragments).encode() != original.encode():
        problems.append("rebuilt SYSTEM.md differs from the base revision")
    return problems


def tree_problems(repo: Path) -> list[str]:
    """One-to-one check of the moved sections that survives later edits of their text.

    Every manifest entry has exactly one stub in ``SYSTEM.md`` and exactly one file that
    still opens with its heading and has a body; no moved title is back in ``SYSTEM.md`` in
    full, no second file carries the heading, and no two files in ``docs/system/`` are equal.
    """
    problems: list[str] = []
    fragments = load_fragments(repo)
    index = (repo / SYSTEM).read_text()
    stubs = Counter(m["slug"] for m in _STUB.finditer(index))
    listed: Counter = Counter()
    titles: Counter = Counter()
    occurrences: dict[str, int] = {}
    for path in sorted((repo / MANIFEST_DIR).glob("system_legacy_part*.json")):
        for entry in json.loads(path.read_text())["sections"]:
            slug, title = entry["slug"], entry["title"]
            listed[slug] += 1
            titles[title] += 1
            occurrences[title] = max(occurrences.get(title, 0), entry["occurrences"])
            body = fragments.get(slug)
            if body is None:
                problems.append(f"{slug}: file missing")
                continue
            first, _, rest = body.partition("\n")
            if first != f"### {title}":
                problems.append(f"{slug}: does not open with '### {title}'")
            elif not rest.strip():
                problems.append(f"{slug}: has no text under its heading")
    for slug, count in listed.items():
        if count > 1:
            problems.append(f"{slug}: listed {count} times in the manifests")
        if stubs[slug] != 1:
            problems.append(f"{slug}: expected one stub in {SYSTEM}, found {stubs[slug]}")
    for slug in stubs.keys() - listed.keys():
        problems.append(f"{slug}: stub in {SYSTEM} without a manifest entry")
    full = Counter(s.title for s in sections_of(index) if not is_stub(s))
    for title, count in titles.items():
        if full[title] > occurrences[title] - count:
            problems.append(f"'{title}': still in {SYSTEM} in full")
    headed = Counter(body.partition("\n")[0][4:] for body in fragments.values())
    for title, count in titles.items():
        if headed[title] != count:
            problems.append(f"'{title}': {headed[title]} files carry the heading, expected {count}")
    by_text: dict[str, list[str]] = {}
    for slug, body in fragments.items():
        by_text.setdefault(sha(body), []).append(slug)
    for slugs in by_text.values():
        if len(slugs) > 1:
            problems.append(f"identical files: {', '.join(sorted(slugs))}")
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=("extract", "verify"))
    parser.add_argument("--repo", default=".")
    parser.add_argument("--base", default="origin/develop")
    parser.add_argument("--part", type=int, required=True)
    parser.add_argument("--of", type=int, default=4)
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    if args.command == "extract":
        manifest = extract(repo, args.base, args.part, args.of)
        total = sum(e["bytes"] for e in manifest["sections"])
        print(f"moved {len(manifest['sections'])} sections, {total} bytes")
        return 0
    problems = verify(repo, args.base, args.part)
    for problem in problems:
        print(problem, file=sys.stderr)
    print("lossless" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())

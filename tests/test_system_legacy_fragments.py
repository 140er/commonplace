"""The legacy ``SYSTEM.md`` decisions live one per file in ``docs/system/`` (card #461).

``scripts/system_legacy_split.py`` moves each ``### `` section out of ``SYSTEM.md`` into
``docs/system/<slug>.md`` and leaves a stub with the same heading. These tests cover the
extraction on synthetic text (lossless, byte for byte), the one-to-one check that fails when
a moved section is dropped or duplicated, the repository's own manifests, and that two
changes to two different moved decisions merge without a git conflict.
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from commonplace.orch import docs_check

REPO = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "system_legacy_split", REPO / "scripts" / "system_legacy_split.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


split = _load()

ORIGINAL = """# System

Intro.

## Decisions

### Alpha decision

- Alpha one.
- Alpha two.

#### Alpha detail

Detail text.

### Beta decision

Beta text, one line.


### Gamma decision

Gamma text.

## Numbers

- A number.

### Alpha decision

Second section with a repeated title.

## Waivers

- None.

### Last decision

Last text, no newline at the end of the file."""


def git(repo, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Example", "-c", "user.email=e@example.test"]
        + ["-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def commit(repo, files, message="change"):
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    assert git(repo, "add", "-A").returncode == 0
    assert git(repo, "commit", "-q", "-m", message).returncode == 0


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "develop")
    commit(root, {"SYSTEM.md": ORIGINAL, "docs/system/existing.md": "### Existing\n\nBody.\n"})
    return root


def extracted(repo, of=1, part=1):
    split.extract(repo, "develop", part, of)
    return repo


def test_sections_cover_every_heading_with_subsections_inside():
    found = split.sections_of(ORIGINAL)
    assert [s.title for s in found] == [
        "Alpha decision",
        "Beta decision",
        "Gamma decision",
        "Alpha decision",
        "Last decision",
    ]
    assert "#### Alpha detail" in found[0].text and found[0].text.endswith("Detail text.")
    assert not found[1].text.endswith("\n")


@pytest.mark.parametrize("of", [1, 2, 3, 5])
def test_every_part_rebuilds_the_original_byte_for_byte(of):
    covered = []
    for part in range(1, of + 1):
        index, moved = split.split_text(ORIGINAL, part, of, {"existing"})
        fragments = {slug: section.text + "\n" for section, slug in moved}
        assert split.rebuild(index, fragments).encode() == ORIGINAL.encode()
        covered += [slug for _section, slug in moved]
    assert sorted(covered) == sorted(set(covered))  # no section in two parts
    assert len(covered) == 5  # and none left out


def test_slugs_are_stable_unique_and_avoid_existing_files():
    taken = {"gamma-decision"}
    plan = split.plan(ORIGINAL, taken)
    assert list(plan.values()) == [
        "alpha-decision",
        "beta-decision",
        "gamma-decision-2",
        "alpha-decision-2",
        "last-decision",
    ]
    assert taken == {"gamma-decision"}


def test_stub_keeps_heading_and_anchor_in_the_index(repo):
    extracted(repo)
    index = (repo / "SYSTEM.md").read_text()
    assert "### Beta decision\n\nMoved to [`docs/system/beta-decision.md`]" in index
    assert "Beta text, one line." not in index
    assert (repo / "docs/system/beta-decision.md").read_text() == (
        "### Beta decision\n\nBeta text, one line.\n"
    )
    assert "## Numbers\n\n- A number." in index  # other ## sections stay where they were


def test_extraction_verifies_lossless_against_the_base(repo):
    extracted(repo, of=2, part=2)
    assert split.verify(repo, "develop", 2) == []
    assert split.tree_problems(repo) == []


def test_unchanged_extraction_is_one_to_one(repo):
    extracted(repo)
    assert split.tree_problems(repo) == []
    assert split.verify(repo, "develop", 1) == []


def mutate(repo, name, fn):
    path = repo / name
    path.write_text(fn(path.read_text()))


def test_dropped_line_fails_verify(repo):
    extracted(repo)
    mutate(repo, "docs/system/alpha-decision.md", lambda t: t.replace("- Alpha two.\n", ""))
    assert split.verify(repo, "develop", 1) == [
        "alpha-decision: text differs from the extracted section"
    ]
    assert split.tree_problems(repo) == []  # an edit is allowed; only verify judges extraction


def test_deleted_file_fails_both_checks(repo):
    extracted(repo)
    (repo / "docs/system/beta-decision.md").unlink()
    assert "beta-decision: file missing" in split.verify(repo, "develop", 1)
    assert "beta-decision: file missing" in split.tree_problems(repo)


def test_dropped_stub_fails_both_checks(repo):
    extracted(repo)
    mutate(
        repo, "SYSTEM.md", lambda t: t.replace("### Gamma decision\n\nMoved to ", "Moved to ", 1)
    )
    assert split.verify(repo, "develop", 1)
    assert any("gamma-decision" in p for p in split.tree_problems(repo))


def test_duplicated_stub_fails_both_checks(repo):
    extracted(repo)
    stub = split.stub_for("Beta decision", "beta-decision")
    mutate(repo, "SYSTEM.md", lambda t: t + "\n\n" + stub + "\n")
    assert split.verify(repo, "develop", 1)
    assert "beta-decision: expected one stub in SYSTEM.md, found 2" in split.tree_problems(repo)


def test_section_left_in_system_in_full_is_a_duplicate(repo):
    extracted(repo)
    mutate(repo, "SYSTEM.md", lambda t: t + "\n\n### Beta decision\n\nBeta text, one line.\n")
    assert "'Beta decision': still in SYSTEM.md in full" in split.tree_problems(repo)


def test_copied_file_is_a_duplicate(repo):
    extracted(repo)
    body = (repo / "docs/system/gamma-decision.md").read_text()
    (repo / "docs/system/gamma-copy.md").write_text(body)
    problems = split.tree_problems(repo)
    assert "'Gamma decision': 2 files carry the heading, expected 1" in problems
    assert "identical files: gamma-copy, gamma-decision" in problems


def test_empty_file_and_wrong_heading_fail(repo):
    extracted(repo)
    (repo / "docs/system/beta-decision.md").write_text("### Beta decision\n")
    (repo / "docs/system/gamma-decision.md").write_text("### Other\n\nText.\n")
    problems = split.tree_problems(repo)
    assert "beta-decision: has no text under its heading" in problems
    assert "gamma-decision: does not open with '### Gamma decision'" in problems


def test_stub_without_manifest_entry_fails(repo):
    extracted(repo)
    (repo / "tests/fixtures/system_legacy_part1.json").unlink()
    assert any("without a manifest entry" in p for p in split.tree_problems(repo))


# ------------------------------------------------------------------ the repository itself


def test_repository_moves_are_one_to_one():
    if not (REPO / "SYSTEM.md").exists():
        pytest.skip("not a source checkout")
    manifests = sorted((REPO / "tests/fixtures").glob("system_legacy_part*.json"))
    assert manifests, "no system_legacy manifest"
    assert split.tree_problems(REPO) == []


def test_repository_manifests_name_unique_slugs_and_sizes():
    seen = set()
    for path in sorted((REPO / "tests/fixtures").glob("system_legacy_part*.json")):
        manifest = json.loads(path.read_text())
        for entry in manifest["sections"]:
            assert entry["slug"] not in seen
            seen.add(entry["slug"])
            assert entry["bytes"] > 0 and len(entry["sha256"]) == 64
            assert (REPO / "docs/system" / f"{entry['slug']}.md").is_file()


def test_repository_docs_check_is_clean_against_the_development_branch():
    if not (REPO / ".git").exists():
        pytest.skip("not a git checkout")
    assert docs_check.check(REPO, "origin/develop", "HEAD") == []


# ------------------------------------------------------------------ parallel changes


def merge_both_orders(repo):
    results = {}
    for first, second in (("a", "b"), ("b", "a")):
        assert git(repo, "checkout", "-q", "-B", f"merge-{first}", "develop").returncode == 0
        assert git(repo, "merge", "-q", "--no-edit", first).returncode == 0
        results[f"{first} then {second}"] = git(repo, "merge", "--no-edit", second)
        if results[f"{first} then {second}"].returncode != 0:
            git(repo, "merge", "--abort")
    return results


def two_edits(repo, edit_a, edit_b):
    for name, edit in (("a", edit_a), ("b", edit_b)):
        assert git(repo, "checkout", "-q", "-b", name, "develop").returncode == 0
        for path, old, new in edit:
            text = (repo / path).read_text()
            assert old in text
            commit(repo, {path: text.replace(old, new, 1)}, f"branch {name}")
        assert git(repo, "checkout", "-q", "develop").returncode == 0


def test_two_changes_to_two_moved_decisions_merge_cleanly(repo):
    extracted(repo)
    assert git(repo, "add", "-A").returncode == 0
    assert git(repo, "commit", "-q", "-m", "split").returncode == 0
    two_edits(
        repo,
        [("docs/system/alpha-decision.md", "- Alpha two.", "- Alpha two, changed by a.")],
        [("docs/system/beta-decision.md", "one line.", "one line, changed by b.")],
    )
    results = merge_both_orders(repo)
    assert {k: v.returncode for k, v in results.items()} == {"a then b": 0, "b then a": 0}
    assert "changed by a." in (repo / "docs/system/alpha-decision.md").read_text()
    assert "changed by b." in (repo / "docs/system/beta-decision.md").read_text()


def test_the_same_two_changes_conflict_when_the_decisions_touch_in_one_file(repo):
    """Control: in the single file, edits at the touching lines of two decisions collide."""
    two_edits(
        repo,
        [
            (
                "SYSTEM.md",
                "Beta text, one line.\n\n\n### Gamma",
                "Beta text, changed by a.\n### Gamma",
            )
        ],
        [("SYSTEM.md", "### Gamma decision", "### Gamma decision (changed by b)")],
    )
    assert {k: v.returncode != 0 for k, v in merge_both_orders(repo).items()} == {
        "a then b": True,
        "b then a": True,
    }


def test_split_layout_passes_docs_check_after_the_move(repo):
    """The extraction output is a valid layout: every file is a section, SYSTEM.md changed."""
    commit(
        repo,
        {
            "MODULES.md": "# Modules\n",
            "docs/modules/system-docs.md": (
                "## system-docs\n\n- **Paths:** `SYSTEM.md`, `MODULES.md`, `docs/`\n"
                "- **Owns:**\n\n  - docs.\n\n- **Promises:**\n\n  - docs.\n\n"
                "- **How it fails:**\n\n  - docs.\n\n- **Tests:**\n\n  - docs.\n"
            ),
        },
        "modules",
    )
    extracted(repo)
    for entry in sorted((repo / "docs/system").glob("*.md")):
        assert (
            docs_check.system_fragment_problems(f"docs/system/{entry.name}", entry.read_text())
            == []
        )
    assert docs_check.is_system_path("docs/system/beta-decision.md")

"""Fragment batches preserve byte decoding, complete checks, and Git failures."""

import subprocess

import pytest

from commonplace.orch import docs_check
from tests.test_docs_fragments import block, commit, frag, fragment_base, git


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "develop")
    commit(repo, fragment_base(), "base")
    git(repo, "checkout", "-q", "-b", "work")
    return repo


def test_fragment_process_count_does_not_grow_per_file(repo, monkeypatch):
    commit(
        repo,
        {
            "core/a.py": "A = 2\n",
            "docs/modules/core.d/first.md": frag("Tests", "A changed."),
            "docs/system/new.md": "### New\n\n- A changed.\n",
        },
    )
    real_git = docs_check._git
    calls = []

    def record(*args, **kwargs):
        calls.append(args[1:])
        return real_git(*args, **kwargs)

    monkeypatch.setattr(docs_check, "_git", record)
    assert docs_check.check(repo, "develop", "HEAD") == []
    initial = len(calls)
    commit(repo, {f"docs/modules/core.d/{i}.md": frag("Tests", f"Case {i}.") for i in range(40)})
    calls.clear()
    assert docs_check.check(repo, "develop", "HEAD") == []
    assert len(calls) <= initial + 2, (initial, len(calls))


def test_fragment_bytes_names_duplicates_and_order_are_preserved(repo):
    contents = {
        "z.md": b"- **Tests:** tail without newline",
        "a with\nnewline.md": b"- **Owns:** utf8 \xc3\xa9 and invalid \xff\x00\n",
        "duplicate.md": b"- **Tests:** tail without newline",
        "empty.md": b"",
    }
    directory = repo / "docs/modules/core.d"
    directory.mkdir(parents=True)
    for name, data in contents.items():
        (directory / name).write_bytes(data)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "fragment bytes")
    assert docs_check._fragments(repo, "HEAD", "docs/modules/core.d/") == [
        (f"docs/modules/core.d/{name}", data.decode("utf-8", "replace"))
        for name, data in sorted(contents.items())
    ]
    assert any(
        "empty.md': holds no field" in item for item in docs_check.check(repo, "HEAD", "HEAD")
    )


def test_moving_head_reloads_fragments_and_deleted_paths_keep_the_base_owner(repo):
    commit(repo, {"docs/modules/core.d/current.md": frag("Tests", "First revision.")})
    first = docs_check.load_modules(repo, "HEAD")
    assert first is not None
    assert "First revision." in first.blocks["core"].text
    commit(repo, {"docs/modules/core.d/current.md": frag("Tests", "Second revision.")})
    second = docs_check.load_modules(repo, "HEAD")
    assert second is not None
    assert "Second revision." in second.blocks["core"].text
    assert "First revision." not in second.blocks["core"].text
    (repo / "core/a.py").unlink()
    commit(
        repo,
        {
            "docs/modules/core.md": block("core", "`removed/`"),
            "docs/system/new.md": "### Removed\n\n- Old ownership remains available.\n",
        },
    )
    assert docs_check.check(repo, "develop", "HEAD") == []


@pytest.mark.parametrize(
    "damage",
    [
        "truncated",
        "missing",
        "wrong-oid",
        "wrong-type",
        "bad-size",
        "negative-size",
        "huge-size",
        "trailing",
    ],
)
def test_bad_batch_replies_fail_closed(repo, monkeypatch, capsys, damage):
    real_git = docs_check._git

    def broken(*args, **kwargs):
        result = real_git(*args, **kwargs)
        if args[1:] == ("cat-file", "--batch"):
            header, body = result.stdout.split(b"\n", 1)
            oid, kind, size = header.split()
            result.stdout = {
                "truncated": result.stdout[:-1],
                "missing": oid + b" missing\n",
                "wrong-oid": b"0" * len(oid) + b" " + kind + b" " + size + b"\n" + body,
                "wrong-type": oid + b" tree " + size + b"\n" + body,
                "bad-size": oid + b" blob nope\n" + body,
                "negative-size": oid + b" blob -1\n" + body,
                "huge-size": oid + b" blob " + b"9" * 5000 + b"\n" + body,
                "trailing": result.stdout + b"unexpected\n",
            }[damage]
        return result

    monkeypatch.setattr(docs_check, "_git", broken)
    assert docs_check.main(["--repo", str(repo), "--base", "HEAD"]) == 2
    assert "git cat-file returned" in capsys.readouterr().err


def test_batch_git_error_is_an_error_exit(repo, monkeypatch, capsys):
    real_run = subprocess.run

    def fail_batch(argv, **kwargs):
        if argv[-2:] == ["cat-file", "--batch"]:
            return subprocess.CompletedProcess(argv, 1, stdout=b"", stderr=b"git failed")
        return real_run(argv, **kwargs)

    monkeypatch.setattr(docs_check.subprocess, "run", fail_batch)
    assert docs_check.main(["--repo", str(repo), "--base", "HEAD"]) == 2
    assert "git cat-file failed" in capsys.readouterr().err

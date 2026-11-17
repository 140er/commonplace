"""The verify command's evidence block and narrow check selection."""

import os
import subprocess

from commonplace import verify


def test_guard_files_are_the_four_repo_guards():
    assert verify.GUARD_TESTS == (
        "tests/test_eval_guards.py",
        "tests/test_commonplace_public_surface.py",
        "tests/test_docs_obligations.py",
        "tests/test_docs_fragments.py",
    )


def test_evidence_block_includes_the_full_sha():
    sha = "b" * 40
    text = verify.evidence_block(
        sha=sha, base="main", lint="pass", tests="pass", guards="pass", files=["t.py"]
    )
    assert text.startswith("Verify evidence\n")
    assert f"sha: {sha}\n" in text
    assert "result: pass\n" in text


def _git(repo, *args, name="Ada Lovelace", email="ada@lovelace.dev"):
    env = dict(
        os.environ,
        GIT_AUTHOR_NAME=name,
        GIT_AUTHOR_EMAIL=email,
        GIT_COMMITTER_NAME=name,
        GIT_COMMITTER_EMAIL=email,
    )
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env
    ).stdout.strip()


def _repo(tmp_path, test_body):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "pkg.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_pkg.py").write_text(test_body, encoding="utf-8")
    (repo / "tests" / "test_unrelated.py").write_text(
        "def test_unrelated():\n    assert False\n", encoding="utf-8"
    )
    for guard in verify.GUARD_TESTS:
        (repo / guard).write_text("def test_guard():\n    assert True\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "pkg.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "change")
    return repo


def test_passing_evidence_names_only_affected_tests_and_guards(tmp_path, capsys):
    repo = _repo(tmp_path, "def test_ok():\n    assert True\n")
    sha = _git(repo, "rev-parse", "HEAD")
    code = verify.main(["--repo", str(repo), "--base", "main"])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert f"sha: {sha}" in captured.out
    assert "result: pass" in captured.out
    assert "lint: pass" in captured.out
    assert "tests: pass" in captured.out
    assert "guards: pass" in captured.out
    assert "files: tests/test_pkg.py, " + ", ".join(verify.GUARD_TESTS) in captured.out
    assert "test_unrelated.py" not in captured.out


def test_a_failing_guard_does_not_print_a_passing_block(tmp_path, capsys):
    repo = _repo(tmp_path, "def test_ok():\n    assert True\n")
    (repo / verify.GUARD_TESTS[0]).write_text(
        "def test_guard():\n    assert False\n", encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "break guard")
    assert verify.main(["--repo", str(repo), "--base", "main"]) == 1
    captured = capsys.readouterr()
    assert "result: pass" not in captured.out
    assert verify.GUARD_TESTS[0] in captured.err


def test_a_failing_test_does_not_print_a_passing_block(tmp_path, capsys):
    repo = _repo(tmp_path, "def test_ok():\n    assert False\n")
    code = verify.main(["--repo", str(repo), "--base", "main"])
    captured = capsys.readouterr()
    assert code == 1
    assert "result: pass" not in captured.out
    assert "assert False" in captured.err


def test_a_missing_guard_does_not_print_a_passing_block(tmp_path, capsys):
    repo = _repo(tmp_path, "def test_ok():\n    assert True\n")
    (repo / verify.GUARD_TESTS[0]).unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "remove guard")
    assert verify.main(["--repo", str(repo), "--base", "main"]) == 1
    captured = capsys.readouterr()
    assert "result: pass" not in captured.out
    assert verify.GUARD_TESTS[0] in captured.err

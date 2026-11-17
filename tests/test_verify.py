"""The verify command's evidence block and the PR-body rule."""

import os
import subprocess
import sys

import pytest

from commonplace import verify


def test_body_accepts_the_evidence_block_or_an_explicit_skip():
    block = "Verify evidence\nsha: " + "a" * 40 + "\nresult: pass\n"
    assert verify.body_problem(block) is None
    assert verify.body_problem("notes\nCould not verify: base origin/develop is missing\n") is None


@pytest.mark.parametrize(
    "body",
    [
        "",
        None,
        "Verify evidence\nsha: abc\nresult: pass\n",
        "sha: " + "a" * 40 + "\nresult: pass\n",
        "Verify evidence\nsha: " + "a" * 40 + "\n",
        "Could not verify:",
        "Could not verify:   \n",
    ],
)
def test_body_rejects_a_missing_or_incomplete_block(body):
    problem = verify.body_problem(body)
    assert problem is not None
    assert "python -m commonplace.verify" in problem
    assert "Could not verify: <reason>" in problem
    assert "sha: <40-character git sha>" in problem


def test_guards_are_added_only_for_docs_routes_or_evals():
    assert verify.needs_guards(["docs/system/x.md"])
    assert verify.needs_guards(["README.md"])
    assert verify.needs_guards(["commonplace/web/routes.py"])
    assert verify.needs_guards(["tests/test_eval_guards.py"])
    assert not verify.needs_guards(["commonplace/verify.py", "tests/test_verify.py"])


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
        sha=sha, base="main", lint="pass", tests="pass", guards="not required", files=["t.py"]
    )
    assert text.startswith("Verify evidence\n")
    assert f"sha: {sha}\n" in text
    assert "result: pass\n" in text


def test_check_body_fails_with_the_fix(monkeypatch, capsys):
    monkeypatch.delenv("PR_BODY", raising=False)
    monkeypatch.setattr(sys, "stdin", type("S", (), {"read": staticmethod(lambda: "")})())
    assert verify.main(["--check-body"]) == 1
    err = capsys.readouterr().err
    assert "missing verify evidence" in err
    assert "python -m commonplace.verify" in err


def test_check_body_passes_on_a_block(monkeypatch):
    monkeypatch.setenv("PR_BODY", "Verify evidence\nsha: " + "c" * 40 + "\nresult: pass\n")
    assert verify.main(["--check-body"]) == 0


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
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "pkg.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "change")
    return repo


def test_a_passing_change_prints_the_head_sha(tmp_path, capsys):
    repo = _repo(tmp_path, "def test_ok():\n    assert True\n")
    sha = _git(repo, "rev-parse", "HEAD")
    code = verify.main(["--repo", str(repo), "--base", "main"])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert f"sha: {sha}" in captured.out
    assert "result: pass" in captured.out
    assert "lint: pass" in captured.out
    assert "tests: pass" in captured.out
    assert "guards: not required" in captured.out


def test_a_failing_test_does_not_print_a_passing_block(tmp_path, capsys):
    repo = _repo(tmp_path, "def test_ok():\n    assert False\n")
    code = verify.main(["--repo", str(repo), "--base", "main"])
    captured = capsys.readouterr()
    assert code == 1
    assert "result: pass" not in captured.out
    assert "assert False" in captured.err

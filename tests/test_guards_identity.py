"""Placeholder git author and committer identities."""

import io
import os
import subprocess

import pytest

from commonplace.guards import hooks, identity


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


def _repo(tmp_path, name="Ada Lovelace", email="ada@lovelace.dev"):
    top = tmp_path / "repo"
    top.mkdir()
    _git(top, "init", "-q", "-b", "main", name=name, email=email)
    (top / "f.txt").write_text("1\n", encoding="utf-8")
    _git(top, "add", "-A", name=name, email=email)
    _git(top, "commit", "-qm", "base", name=name, email=email)
    return top


@pytest.mark.parametrize(
    ("name", "email"),
    [
        ("Project contributor", "contributor@example.invalid"),
        ("Project Contributor", "ada@lovelace.dev"),
        ("  project   contributor  ", "ada@lovelace.dev"),
        ("Ada Lovelace", "ada@example.com"),
        ("Ada Lovelace", "ada@Example.INVALID"),
        ("Ada Lovelace", "ada@localhost"),
        ("", "ada@lovelace.dev"),
        ("   ", "ada@lovelace.dev"),
        ("Ada Lovelace", ""),
        ("Ada Lovelace", "not-an-email"),
    ],
)
def test_matcher_rejects_placeholder_identities(name, email):
    assert identity.identity_problem(name, email)


def test_matcher_accepts_a_real_identity():
    assert identity.identity_problem("Ada Lovelace", "ada@lovelace.dev") is None
    assert identity.identity_problem("Ada Lovelace", "ada@sub.example.com") is None


def test_range_rejects_a_placeholder_and_says_how_to_fix(tmp_path, capsys):
    repo = _repo(tmp_path, name="Project contributor", email="contributor@example.invalid")
    code = identity.main(["--range", "HEAD", "--repo", str(repo)])
    err = capsys.readouterr().err
    assert code == 1
    assert "Project contributor" in err
    assert "contributor@example.invalid" in err
    assert "placeholder email domain example.invalid" in err
    assert 'git config user.name "Your Name"' in err
    assert 'git config user.email "you@your-domain"' in err
    assert "git commit --amend --reset-author --no-edit" in err
    assert "git rebase <base> --exec" in err


def test_range_accepts_a_real_identity(tmp_path, capsys):
    repo = _repo(tmp_path)
    assert identity.main(["--range", "HEAD", "--repo", str(repo)]) == 0
    assert capsys.readouterr().err == ""


def test_pre_push_rejects_the_commit_being_pushed(tmp_path, capsys):
    repo = _repo(tmp_path, name="Project contributor", email="dev@localhost")
    head = _git(repo, "rev-parse", "HEAD")
    stdin = io.StringIO(f"refs/heads/main {head} refs/heads/main {'0' * 40}\n")
    code = identity.main(["--pre-push", "origin", "url", "--repo", str(repo)], stdin=stdin)
    err = capsys.readouterr().err
    assert code == 1
    assert "placeholder email domain localhost" in err
    assert "git commit --amend --reset-author" in err


def test_pre_push_script_runs_the_same_check():
    script = hooks.render("pre-push")
    assert "commonplace.guards.identity --pre-push" in script
    assert 'exec "$PY" -m commonplace.guards.prepush "$@"' in script
    assert "commonplace.guards.identity" not in hooks.render("pre-commit")

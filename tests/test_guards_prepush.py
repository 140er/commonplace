"""Pre-push checks: changed files, typechecking, elapsed alarms and worktree bytes."""

import io
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from commonplace.guards import prepush
from commonplace.guards.stacks import CommandResult, PlannedCommand


def _git(repo, *args):
    env = dict(
        os.environ,
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@example.invalid",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@example.invalid",
    )
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env
    ).stdout.strip()


def _write(path: Path, text: str = "", mode: int | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mode is not None:
        path.chmod(mode)
    return path


FAKE_TOOL = """#!/bin/sh
echo "$0 $*" >> "$TOOL_LOG"
[ -n "$TOOL_SLEEP" ] && exec sleep "$TOOL_SLEEP"
[ "${TOOL_EXIT:-0}" != 0 ] && echo "pkg/mod.py:3: E999 fake finding"
exit "${TOOL_EXIT:-0}"
"""


def _fake_ruff(root: Path) -> Path:
    _write(root / ".venv" / "bin" / "mypy", FAKE_TOOL, 0o755)
    return _write(root / ".venv" / "bin" / "ruff", FAKE_TOOL, 0o755)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repo on ``main`` with a ruff stack and a ``feature`` branch changing one module."""
    top = tmp_path / "repo"
    top.mkdir()
    _git(top, "init", "-q", "-b", "main")
    _write(top / "pyproject.toml", "[tool.ruff]\nline-length = 100\n")
    _write(top / "pkg" / "__init__.py")
    _write(top / "pkg" / "mod.py", "X = 1\n")
    _write(top / "tests" / "test_mod.py", "def test_ok():\n    assert True\n")
    _git(top, "add", "-A")
    _git(top, "commit", "-q", "-m", "base")
    _git(top, "checkout", "-q", "-b", "feature")
    _write(top / "pkg" / "mod.py", "X = 2\n")
    _git(top, "commit", "-qam", "change")
    monkeypatch.setenv("TOOL_LOG", str(tmp_path / "tool.log"))
    for name in ("TOOL_SLEEP", "TOOL_EXIT"):
        monkeypatch.delenv(name, raising=False)
    return top


# ── changed-file selection ─────────────────────────────────────────────


@pytest.fixture
def tree(tmp_path):
    for name in (
        "test_guards_stacks.py",
        "test_stacks_extra.py",
        "test_import_grant.py",
        "test_import_grant_more.py",
        "test_import_grants_other.py",
        "test_guards.py",
        "conftest.py",
    ):
        _write(tmp_path / "tests" / name)
    return tmp_path


def test_a_changed_test_file_selects_itself(tree):
    got = prepush.select_tests(tree, ["tests/test_guards_stacks.py"])
    assert got.files == ("tests/test_guards_stacks.py",)
    assert got.unmapped == ()


def test_a_package_module_maps_by_package_and_module_name(tree):
    got = prepush.select_tests(tree, ["commonplace/guards/stacks.py"])
    assert got.files == ("tests/test_guards_stacks.py",)


def test_the_bare_module_name_is_only_a_fallback(tree):
    assert prepush.select_tests(tree, ["commonplace/other/stacks.py"]).files == (
        "tests/test_stacks_extra.py",
    )


def test_a_top_level_module_maps_to_its_exact_and_suffixed_tests_only(tree):
    got = prepush.select_tests(tree, ["commonplace/import_grant.py"])
    assert got.files == ("tests/test_import_grant.py", "tests/test_import_grant_more.py")


def test_a_package_init_maps_to_every_test_of_the_package(tree):
    got = prepush.select_tests(tree, ["commonplace/guards/__init__.py"])
    assert got.files == ("tests/test_guards.py", "tests/test_guards_stacks.py")


def test_unmapped_changes_are_returned_not_dropped(tree):
    got = prepush.select_tests(
        tree, ["README.md", "commonplace/nothing_tests_this.py", "tests/conftest.py", "a.js"]
    )
    assert got.files == ()
    assert got.unmapped == (
        "README.md",
        "commonplace/nothing_tests_this.py",
        "tests/conftest.py",
        "a.js",
    )


def test_a_removed_test_file_is_not_selected(tree):
    got = prepush.select_tests(tree, ["tests/test_gone.py"])
    assert got.files == ()
    assert got.unmapped == ("tests/test_gone.py",)


def test_selection_is_sorted_and_deduplicated(tree):
    got = prepush.select_tests(
        tree, ["commonplace/guards/stacks.py", "tests/test_guards_stacks.py", "x/stacks.py"]
    )
    assert got.files == ("tests/test_guards_stacks.py", "tests/test_stacks_extra.py")


def test_gate_tests_appends_existing_guard_files_after_affected_tests(tree):
    for name in prepush.GUARD_TESTS:
        _write(tree / name)
    got = prepush.gate_tests(tree, ["commonplace/guards/stacks.py"], guards=True)
    assert got.files == ("tests/test_guards_stacks.py",) + prepush.GUARD_TESTS


def test_gate_tests_without_the_ci_flag_stays_on_affected_tests(tree):
    for name in prepush.GUARD_TESTS:
        _write(tree / name)
    got = prepush.gate_tests(tree, ["commonplace/guards/stacks.py"], guards=False)
    assert got.files == ("tests/test_guards_stacks.py",)


def test_gate_tests_does_not_repeat_a_guard_file_that_changed(tree):
    for name in prepush.GUARD_TESTS:
        _write(tree / name)
    got = prepush.gate_tests(tree, ["tests/test_eval_guards.py"], guards=True)
    assert got.files.count("tests/test_eval_guards.py") == 1
    assert got.files[1:] == prepush.GUARD_TESTS[1:]


def test_ci_workflow_calls_the_prepush_entrypoint_and_not_the_full_suite():
    workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    assert "python -m commonplace.guards.prepush --ci" in workflow
    assert "pytest tests/ -v" not in workflow
    assert "pytest tests/ --ignore" not in workflow


# ── budget ─────────────────────────────────────────────────────────────


def test_budget_defaults_and_overrides():
    warnings: list[str] = []
    assert prepush.parse_budget(None, warnings.append) == 9.0
    assert prepush.parse_budget("", warnings.append) == 9.0
    assert prepush.parse_budget("12.5", warnings.append) == 12.5
    assert warnings == []


@pytest.mark.parametrize("bad", ["abc", "0", "-3", "nan", "inf"])
def test_an_unusable_budget_warns_and_uses_the_default(bad):
    warnings: list[str] = []
    assert prepush.parse_budget(bad, warnings.append) == 9.0
    assert len(warnings) == 1 and repr(bad) in warnings[0]


def test_budget_counts_down_on_the_clock():
    now = [100.0]
    budget = prepush.Budget(10, clock=lambda: now[0])
    assert budget.remaining() == 10
    now[0] = 104.0
    assert (budget.elapsed(), budget.remaining()) == (4.0, 6.0)
    now[0] = 500.0
    assert budget.remaining() == 0.0


def _run(top, budget=30.0, **kwargs):
    out = io.StringIO()
    report = prepush.run_quick_checks(
        top,
        kwargs.pop("changed", ["pkg/mod.py", "tests/test_mod.py"]),
        base="main",
        heads=[_git(top, "rev-parse", "HEAD")],
        budget_seconds=budget,
        out=out,
        **kwargs,
    )
    return report, out.getvalue()


def test_a_green_run_says_it_is_a_quick_check_not_the_full_suite(repo):
    _fake_ruff(repo)
    report, text = _run(repo)
    names = {o.name: o.status for o in report.outcomes}
    assert names["lint ruff"] == names["format ruff"] == names["typecheck mypy"] == "passed"
    assert names["tests"] == "passed"
    assert report.exit_code == 0 and report.complete
    assert "QUICK CHECK" in text.splitlines()[0]
    assert "not the full test suite and not CI" in text
    assert "QUICK CHECK PASSED" in text


def test_a_failing_check_fails_the_run_and_prints_the_fix(repo, monkeypatch):
    _fake_ruff(repo)
    monkeypatch.setenv("TOOL_EXIT", "1")
    report, text = _run(repo)
    assert report.exit_code == 1
    assert "QUICK CHECK FAILED" in text
    assert "pkg/mod.py:3" in text
    assert "run: ruff check --fix" in text


def test_elapsed_alarm_never_cuts_off_a_check_or_omits_later_checks(repo, monkeypatch):
    _fake_ruff(repo)
    now = [0.0]
    original = prepush.run_command

    def run(command, top, timeout, **kwargs):
        assert timeout is None
        result = original(command, top, timeout, **kwargs)
        now[0] += 10.0
        return result

    monkeypatch.setattr(prepush, "run_command", run)
    report, text = _run(repo, budget=1.0, clock=lambda: now[0])
    status = {o.name: o.status for o in report.outcomes}
    assert status["lint ruff"] == status["format ruff"] == "passed"
    assert status["typecheck mypy"] == status["tests"] == "passed"
    assert report.exit_code == 0 and report.complete
    assert report.total_seconds == 40.0
    assert "TIME ALARM" in text and "QUICK CHECK PASSED" in text


def test_strict_mode_fails_when_a_step_is_cut_off(repo, monkeypatch):
    _fake_ruff(repo)
    monkeypatch.setattr(
        prepush,
        "_test_step",
        Mock(return_value=prepush.StepOutcome("tests", "incomplete", detail="test interrupted")),
    )
    report, text = _run(repo, budget=1.0, strict=True)
    assert report.exit_code == 1
    assert report.failed is False
    assert "CI GATE FAILED" in text
    assert "not a pass" in text


def test_a_test_failure_after_the_elapsed_alarm_still_blocks_the_push(repo, monkeypatch):
    _fake_ruff(repo)
    _write(repo / "tests/test_mod.py", "def test_bad():\n    assert False\n")
    ticks = iter((0.0, 40.0))
    report, text = _run(repo, budget=1.0, clock=lambda: next(ticks))
    tests = next(o for o in report.outcomes if o.name == "tests")
    assert tests.status == "failed"
    assert "python -m pytest" in (tests.rerun or "")
    assert report.exit_code == 1
    assert "TIME ALARM" in text and "QUICK CHECK FAILED" in text


def test_a_tool_missing_from_the_worktree_blocks_the_push(repo, monkeypatch):
    monkeypatch.setattr(sys, "executable", str(repo / "bin" / "python"))
    report, text = _run(repo, env={"PATH": str(repo / "empty")})
    lint = next(o for o in report.outcomes if o.name == "lint ruff")
    assert lint.status == "failed" and "ruff is not installed" in lint.detail
    assert report.exit_code == 1 and "QUICK CHECK FAILED" in text


def test_a_pushed_commit_that_is_not_checked_out_is_not_checked_as_head(repo):
    _fake_ruff(repo)
    report, text = _run(repo, files_current=False)
    status = {o.name: o.status for o in report.outcomes}
    assert status["lint/format/typecheck"] == status["tests"] == "skipped"
    assert report.exit_code == 1 and not report.complete
    assert "not the checked-out HEAD" in text
    assert Path(os.environ["TOOL_LOG"]).exists() is False


def test_docs_check_runs_against_the_base_when_the_repo_has_modules_md(repo):
    _fake_ruff(repo)
    _write(repo / "MODULES.md", "# Modules\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "modules")
    report, text = _run(repo)
    docs = next(o for o in report.outcomes if o.name == "docs-check")
    assert docs.status == "failed"
    assert report.exit_code == 1
    assert "python -m commonplace orch docs-check --base main --head HEAD" in text


def test_docs_check_with_an_unknown_base_blocks_the_push(repo):
    _write(repo / "MODULES.md", "# Modules\n")
    out = io.StringIO()
    report = prepush.run_quick_checks(
        repo,
        ["pkg/mod.py"],
        base="origin/nope",
        heads=[_git(repo, "rev-parse", "HEAD")],
        out=out,
        env={"PATH": ""},
    )
    docs = next(o for o in report.outcomes if o.name == "docs-check")
    assert docs.status == "failed" and "origin/nope" in docs.detail
    assert report.exit_code == 1


# ── worktree: each tree's own tools win ────────────────────────────────


def test_tool_path_puts_the_worktrees_own_bins_first(tmp_path):
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / "node_modules" / ".bin").mkdir(parents=True)
    path = prepush.tool_path(tmp_path, "/usr/bin").split(os.pathsep)
    assert path[:2] == [str(tmp_path / ".venv" / "bin"), str(tmp_path / "node_modules" / ".bin")]
    assert path[-1] == "/usr/bin"
    assert str(Path(sys.executable).parent) in path
    bare = prepush.tool_path(tmp_path / "bare", "/usr/bin").split(os.pathsep)
    assert str(tmp_path / "bare" / ".venv" / "bin") not in bare


def test_a_linked_worktree_runs_its_own_ruff_not_the_one_on_path(repo, tmp_path, monkeypatch):
    decoy = _write(
        tmp_path / "decoy" / "ruff", '#!/bin/sh\necho "decoy $*" >> "$TOOL_LOG"\nexit 1\n', 0o755
    )
    monkeypatch.setenv("PATH", f"{decoy.parent}{os.pathsep}{os.environ['PATH']}")
    _fake_ruff(repo)  # the main checkout also has one; the worktree gets its own below
    work = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", str(work), "-b", "wt-branch", "feature")
    own = _fake_ruff(work)
    monkeypatch.chdir(work)
    code = prepush.main(["--manual", "--base", "main", "--budget", "20"])
    log = Path(os.environ["TOOL_LOG"]).read_text()
    assert code == 0
    assert str(own) in log
    assert str(repo / ".venv") not in log and "decoy" not in log


def test_main_reads_pushed_refs_from_stdin_and_checks_only_those_files(
    repo, tmp_path, monkeypatch, capsys
):
    _fake_ruff(repo)
    head = _git(repo, "rev-parse", "HEAD")
    zero = "0" * 40
    monkeypatch.chdir(repo)
    stdin = io.StringIO(f"refs/heads/feature {head} refs/heads/feature {zero}\n")
    code = prepush.main(["origin", "url", "--budget", "20"], stdin=stdin)
    err = capsys.readouterr().err
    assert code == 0
    log = Path(os.environ["TOOL_LOG"]).read_text()
    assert "pkg/mod.py" in log and "tests/test_mod.py" not in log.split("format")[0]
    assert "1 changed file," in err


def test_main_with_only_a_delete_has_nothing_to_check(repo, monkeypatch, capsys):
    head = _git(repo, "rev-parse", "HEAD")
    monkeypatch.chdir(repo)
    stdin = io.StringIO(f"(delete) {'0' * 40} refs/heads/old {head}\n")
    assert prepush.main(["origin", "url"], stdin=stdin) == 0
    assert "nothing to check" in capsys.readouterr().err


def test_ci_flag_runs_the_hook_entrypoint(repo, monkeypatch, capsys):
    _fake_ruff(repo)
    for name in prepush.GUARD_TESTS:
        _write(repo / name, "def test_guard():\n    assert True\n")
    monkeypatch.chdir(repo)
    code = prepush.main(["--ci", "--base", "main", "--budget", "20"])
    err = capsys.readouterr().err
    assert code == 0, err
    assert "commonplace CI gate" in err
    assert "CI GATE PASSED" in err
    assert "5 test files" in err
    assert "not the full test suite and not CI" not in err


@pytest.mark.parametrize("guard", prepush.GUARD_TESTS)
@pytest.mark.parametrize("invalid_kind", ["missing", "directory"])
def test_ci_fails_before_running_tests_when_a_mandatory_guard_is_not_a_file(
    repo, monkeypatch, guard, invalid_kind
):
    for name in prepush.GUARD_TESTS:
        _write(repo / name)
    path = repo / guard
    path.unlink()
    if invalid_kind == "directory":
        path.mkdir()
    command = PlannedCommand(name="pytest", argv=(sys.executable, "-m", "pytest"))
    run = Mock(return_value=CommandResult(command, "passed", 0.0, "", []))
    monkeypatch.setattr(prepush, "run_command", run)

    report, text = _run(repo, changed=["README.md"], guards=True, strict=True)

    assert report.exit_code == 1
    tests = next(outcome for outcome in report.outcomes if outcome.name == "tests")
    assert tests.status == "failed"
    assert guard in tests.detail
    assert "mandatory guard" in tests.detail
    assert "CI GATE FAILED" in text
    assert "CI GATE PASSED" not in text
    run.assert_not_called()


def test_a_bad_budget_value_does_not_block_the_push(repo, monkeypatch, capsys):
    _fake_ruff(repo)
    monkeypatch.chdir(repo)
    monkeypatch.setenv(prepush.BUDGET_ENV, "soon")
    assert prepush.main(["--manual", "--base", "main"]) == 0
    assert "ignoring budget 'soon'" in capsys.readouterr().err


def test_the_module_runs_as_a_script_from_the_worktree(repo):
    _fake_ruff(repo)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "commonplace.guards.prepush",
            "--manual",
            "--base",
            "main",
            "--budget",
            "20",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1])),
    )
    assert proc.returncode == 0, proc.stderr
    assert "QUICK CHECK" in proc.stderr


def test_a_tool_beside_the_interpreter_in_use_is_found(repo, monkeypatch):
    """A hook pointed at another environment (COMMONPLACE_PYTHON) still finds that env's ruff."""
    other = repo.parent / "other-env" / "bin"
    _fake_ruff(other.parent.parent / "other-env-root")  # unrelated, must not be used
    _write(other / "ruff", FAKE_TOOL, 0o755)
    _write(other / "mypy", FAKE_TOOL, 0o755)
    monkeypatch.setattr(sys, "executable", str(other / "python"))
    report, _ = _run(repo, env={"PATH": "", "TOOL_LOG": os.environ["TOOL_LOG"]})
    lint = next(o for o in report.outcomes if o.name == "lint ruff")
    assert lint.status == "passed"


def test_dirty_tracked_files_cannot_mask_a_pushed_error(repo, monkeypatch, capsys):
    _fake_ruff(repo)
    _write(repo / "pkg/mod.py", "X = 99\n")
    monkeypatch.chdir(repo)
    assert prepush.main(["--manual", "--base", "main"]) == 1
    assert "tracked files differ from HEAD" in capsys.readouterr().err
    assert not Path(os.environ["TOOL_LOG"]).exists()


def test_bad_hook_input_fails_closed_without_echoing_it(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    private_input = "private-ref private-value malformed-input\n"
    assert prepush.main(["origin", "url"], stdin=io.StringIO(private_input)) == 1
    output = capsys.readouterr().err
    assert "push is blocked" in output and "private-" not in output


def test_real_typechecker_rejects_a_stub_with_a_space_in_its_path(repo):
    _fake_ruff(repo)
    (repo / ".venv/bin/mypy").unlink()  # use the real development typechecker
    _write(repo / "stub dir/a b.pyi", 'VALUE: int = "wrong type"\n')
    report, text = _run(repo, changed=["notes.md", "stub dir/a b.pyi"])
    mypy = next(outcome for outcome in report.outcomes if outcome.name == "typecheck mypy")
    assert mypy.status == "failed" and report.exit_code == 1
    assert "stub dir/a b.pyi:1" in text
    assert "Incompatible types in assignment" in text and "[assignment]" in text


@pytest.mark.parametrize("mistake", ["none", "helper", "consumer", "untyped_body"])
def test_real_typechecker_checks_namespace_import_roots(repo, mistake):
    """Changed namespace modules keep one identity and errors in both explicit roots."""
    helper = 'VALUE: int = "wrong"\n' if mistake == "helper" else "VALUE: int = 1\n"
    consumer = "from tests.namespace_helper import VALUE\n\n"
    if mistake == "consumer":
        consumer += "RESULT: str = VALUE\n"
    elif mistake == "untyped_body":
        consumer += "def value():\n    result: str = VALUE\n    return result\n"
    else:
        consumer += "def value() -> int:\n    return VALUE\n"
    roots = ["tests/namespace_helper.py", "tests/namespace_consumer.py"]
    _write(repo / roots[0], helper)
    _write(repo / roots[1], consumer)
    assert not (repo / "tests/__init__.py").exists()
    commands = [
        command for name, command in prepush._file_steps(repo, roots) if name == "typecheck mypy"
    ]
    assert len(commands) == 1
    result = subprocess.run(
        commands[0].argv,
        cwd=repo,
        env=dict(os.environ, PATH=prepush.tool_path(repo)),
        capture_output=True,
        text=True,
    )
    if mistake == "none":
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode == 1, result.stdout + result.stderr
        assert "[assignment]" in result.stdout

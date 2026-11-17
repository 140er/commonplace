"""Narrow verification. Prints an evidence block that includes the git SHA.

    python -m commonplace.verify

The block is printed only after changed-file lint, affected tests, and the four
repository guard files pass. Paste it into the pull request description.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

EVIDENCE_HEADER = "Verify evidence"
GUARD_TESTS = (
    "tests/test_eval_guards.py",
    "tests/test_commonplace_public_surface.py",
    "tests/test_docs_obligations.py",
    "tests/test_docs_fragments.py",
)


def evidence_block(
    *,
    sha: str,
    base: str,
    lint: str,
    tests: str,
    guards: str,
    files: list[str],
) -> str:
    shown = ", ".join(files[:20])
    if len(files) > 20:
        shown += f" (+{len(files) - 20} more)"
    lines = [
        EVIDENCE_HEADER,
        f"sha: {sha}",
        "result: pass",
        f"base: {base}",
        f"lint: {lint}",
        f"tests: {tests}",
        f"guards: {guards}",
        f"files: {shown or '(none)'}",
    ]
    return "\n".join(lines) + "\n"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )


def _run(repo: Path, argv: list[str]) -> tuple[int, str]:
    from commonplace.guards.prepush import tool_path

    env = os.environ.copy()
    env["PATH"] = tool_path(repo, env.get("PATH", ""))
    try:
        proc = subprocess.run(argv, cwd=repo, env=env, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return 127, f"{argv[0]} is not installed"
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _could_not(reason: str) -> int:
    print(f"Could not verify: {reason}", file=sys.stderr)
    return 2


def _planned_tests(repo: Path, changed: list[str]) -> list[str]:
    from commonplace.guards.prepush import select_tests

    files = list(select_tests(repo, changed).files)
    for name in GUARD_TESTS:
        if name not in files:
            files.append(name)
    return files


def run(repo: Path, base: str) -> int:
    """Lint changed Python, run the affected tests, and print the evidence block."""
    top = _git(repo, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        return _could_not("not inside a git work tree")
    root = Path(top.stdout.strip())
    head = _git(root, "rev-parse", "HEAD")
    if head.returncode != 0 or len(head.stdout.strip()) != 40:
        return _could_not("could not read the git SHA of HEAD")
    sha = head.stdout.strip()
    merged = _git(root, "merge-base", base, "HEAD")
    if merged.returncode != 0 or not merged.stdout.strip():
        return _could_not(f"base {base} is not available (git fetch it)")
    diff = _git(root, "diff", "--name-only", "--diff-filter=d", merged.stdout.strip(), "HEAD")
    if diff.returncode != 0:
        return _could_not("could not list the changed files")
    changed = [line for line in diff.stdout.splitlines() if line.strip()]
    python_files = [path for path in changed if path.endswith(".py") and (root / path).is_file()]
    lint = "not required"
    if python_files:
        for argv in (
            ["ruff", "check", *python_files],
            ["ruff", "format", "--check", *python_files],
        ):
            code, output = _run(root, argv)
            if code == 127:
                return _could_not(output)
            if code != 0:
                print(output, file=sys.stderr)
                return 1
        lint = "pass"
    files = _planned_tests(root, changed)
    code, output = _run(
        root, [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *files]
    )
    if code == 127:
        return _could_not(output)
    if code != 0:
        print(output, file=sys.stderr)
        return 1
    print(
        evidence_block(sha=sha, base=base, lint=lint, tests="pass", guards="pass", files=files),
        end="",
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m commonplace.verify",
        description="Run the narrow checks and print a verify evidence block.",
    )
    parser.add_argument(
        "--base", default="origin/develop", help="base ref (default origin/develop)"
    )
    parser.add_argument("--repo", default=".", help="repository directory (default .)")
    args = parser.parse_args(argv)
    return run(Path(args.repo), args.base)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

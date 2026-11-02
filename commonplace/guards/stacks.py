"""Lint and format stacks of a repo: detection, per-file command plans, budgeted runs.

Pure library: nothing here installs a hook or reads a commonplace config file.
A pre-push guard calls ``detect_stacks``, ``plan_commands`` and ``run_checks``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

JS_EXTENSIONS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
TS_EXTENSIONS = (".ts", ".tsx", ".mts", ".cts")
JS_TOOLS = frozenset({"oxlint", "eslint", "biome", "prettier", "tsc"})

_ESLINT_FILES = (
    tuple(f"eslint.config.{ext}" for ext in ("js", "mjs", "cjs", "ts", "mts", "cts"))
    + (".eslintrc", ".eslintrc.js", ".eslintrc.cjs", ".eslintrc.json", ".eslintrc.yml")
    + (".eslintrc.yaml",)
)
_PRETTIER_FILES = (
    ".prettierrc",
    ".prettierrc.json",
    ".prettierrc.yml",
    ".prettierrc.yaml",
    ".prettierrc.js",
    ".prettierrc.cjs",
    ".prettierrc.mjs",
    ".prettierrc.toml",
    "prettier.config.js",
    "prettier.config.cjs",
    "prettier.config.mjs",
    "prettier.config.ts",
)
_RUFF_TABLE = re.compile(r"^\s*\[tool\.ruff[.\]]", re.MULTILINE)


@dataclass(frozen=True)
class Stack:
    """A lint or format tool detected in a repo.

    ``marker`` is the repo-relative file that triggered detection.
    """

    name: str
    extensions: tuple[str, ...]
    marker: str


def _read_package_json(repo_root: Path) -> dict:
    """Parse the root package.json; anything unreadable or malformed counts as empty."""
    try:
        data = json.loads((repo_root / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return {}
    return data if isinstance(data, dict) else {}


def _declares(package: dict, dependency: str) -> bool:
    """True when the package.json lists the dependency in dependencies or devDependencies."""
    for section in ("dependencies", "devDependencies"):
        deps = package.get(section)
        if isinstance(deps, dict) and dependency in deps:
            return True
    return False


def _first_file(repo_root: Path, names: tuple[str, ...]) -> str | None:
    """Return the first name that exists as a file at the repo root."""
    for name in names:
        if (repo_root / name).is_file():
            return name
    return None


def _pyproject_has_ruff(repo_root: Path) -> bool:
    try:
        text = (repo_root / "pyproject.toml").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return _RUFF_TABLE.search(text) is not None


def detect_stacks(repo_root: Path | str) -> list[Stack]:
    """Detect the lint and format stacks at the repo root, in a stable order."""
    root = Path(repo_root)
    package = _read_package_json(root)
    stacks: list[Stack] = []

    ruff_marker = _first_file(root, ("ruff.toml", ".ruff.toml"))
    if ruff_marker is None and _pyproject_has_ruff(root):
        ruff_marker = "pyproject.toml"
    if ruff_marker:
        stacks.append(Stack("ruff", (".py", ".pyi"), ruff_marker))

    if (root / ".oxlintrc.json").is_file():
        stacks.append(Stack("oxlint", JS_EXTENSIONS, ".oxlintrc.json"))
    elif _declares(package, "oxlint"):
        stacks.append(Stack("oxlint", JS_EXTENSIONS, "package.json"))

    eslint_marker = _first_file(root, _ESLINT_FILES)
    if eslint_marker is None and "eslintConfig" in package:
        eslint_marker = "package.json"
    if eslint_marker:
        stacks.append(Stack("eslint", JS_EXTENSIONS, eslint_marker))

    biome_marker = _first_file(root, ("biome.json", "biome.jsonc"))
    if biome_marker:
        stacks.append(Stack("biome", JS_EXTENSIONS + (".json", ".jsonc", ".css"), biome_marker))

    prettier_marker = _first_file(root, _PRETTIER_FILES)
    if prettier_marker is None and "prettier" in package:
        prettier_marker = "package.json"
    if prettier_marker:
        stacks.append(
            Stack(
                "prettier",
                JS_EXTENSIONS + (".json", ".css", ".scss", ".md", ".yaml", ".yml", ".html"),
                prettier_marker,
            )
        )

    if (root / "tsconfig.json").is_file():
        stacks.append(Stack("tsc", TS_EXTENSIONS, "tsconfig.json"))
    if (root / "Cargo.toml").is_file():
        stacks.append(Stack("cargo-fmt", (".rs",), "Cargo.toml"))
    if (root / "go.mod").is_file():
        stacks.append(Stack("gofmt", (".go",), "go.mod"))
    return stacks

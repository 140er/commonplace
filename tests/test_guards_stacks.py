"""Tests for commonplace.guards.stacks: detection, planning, budgeted runs, real tools."""

import json
from pathlib import Path

import pytest

from commonplace.guards.stacks import (
    PlannedCommand,
    Stack,
    detect_stacks,
    plan_commands,
    resolve_executable,
)


def _names(stacks):
    return [stack.name for stack in stacks]


def _write(root: Path, name: str, text: str = "") -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ── detection ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("marker", ["ruff.toml", ".ruff.toml"])
def test_ruff_detected_from_its_own_config_file(tmp_path, marker):
    _write(tmp_path, marker, "line-length = 100\n")
    stacks = detect_stacks(tmp_path)
    assert stacks == [Stack("ruff", (".py", ".pyi"), marker)]


def test_ruff_detected_from_pyproject_tool_table(tmp_path):
    _write(tmp_path, "pyproject.toml", "[project]\nname = 'x'\n\n[tool.ruff.lint]\nselect = []\n")
    assert _names(detect_stacks(tmp_path)) == ["ruff"]
    assert detect_stacks(tmp_path)[0].marker == "pyproject.toml"


def test_pyproject_without_ruff_table_is_not_ruff(tmp_path):
    _write(tmp_path, "pyproject.toml", "[project]\nname = 'x'\n# [tool.ruff] is only a comment\n")
    assert detect_stacks(tmp_path) == []


def test_oxlint_from_config_or_package_dependency(tmp_path):
    _write(tmp_path / "config", ".oxlintrc.json", "{}")
    stacks = detect_stacks(tmp_path / "config")
    assert stacks == [Stack("oxlint", stacks[0].extensions, ".oxlintrc.json")]
    assert ".tsx" in stacks[0].extensions

    _write(tmp_path / "dep", "package.json", json.dumps({"devDependencies": {"oxlint": "^1"}}))
    stacks = detect_stacks(tmp_path / "dep")
    assert _names(stacks) == ["oxlint"]
    assert stacks[0].marker == "package.json"


def test_eslint_from_flat_config_legacy_config_or_package_key(tmp_path):
    _write(tmp_path / "flat", "eslint.config.mjs", "export default [];\n")
    assert detect_stacks(tmp_path / "flat")[0].marker == "eslint.config.mjs"

    _write(tmp_path / "legacy", ".eslintrc.yml", "root: true\n")
    assert detect_stacks(tmp_path / "legacy")[0].marker == ".eslintrc.yml"

    _write(tmp_path / "pkg", "package.json", json.dumps({"eslintConfig": {"root": True}}))
    assert detect_stacks(tmp_path / "pkg")[0].marker == "package.json"


def test_biome_and_prettier_detection_and_extensions(tmp_path):
    _write(tmp_path, "biome.jsonc", "{}")
    _write(tmp_path, ".prettierrc.toml", "semi = false\n")
    stacks = detect_stacks(tmp_path)
    assert _names(stacks) == ["biome", "prettier"]
    biome, prettier = stacks
    assert biome.marker == "biome.jsonc"
    assert ".jsonc" in biome.extensions and ".css" in biome.extensions
    assert prettier.marker == ".prettierrc.toml"
    assert ".md" in prettier.extensions and ".scss" in prettier.extensions


def test_prettier_from_package_key(tmp_path):
    _write(tmp_path, "package.json", json.dumps({"prettier": {"semi": False}}))
    assert detect_stacks(tmp_path)[0].marker == "package.json"


def test_malformed_package_json_is_ignored(tmp_path):
    _write(tmp_path, "package.json", '{"devDependencies": {"oxlint": ')
    _write(tmp_path, "tsconfig.json", "{}")
    assert _names(detect_stacks(tmp_path)) == ["tsc"]


def test_non_object_package_json_is_ignored(tmp_path):
    _write(tmp_path, "package.json", "[1, 2]")
    assert detect_stacks(tmp_path) == []


def test_tsc_cargo_and_gofmt_detection(tmp_path):
    _write(tmp_path, "tsconfig.json", "{}")
    _write(tmp_path, "Cargo.toml", "[package]\n")
    _write(tmp_path, "go.mod", "module x\n")
    stacks = detect_stacks(tmp_path)
    assert _names(stacks) == ["tsc", "cargo-fmt", "gofmt"]
    assert [s.marker for s in stacks] == ["tsconfig.json", "Cargo.toml", "go.mod"]


def test_stack_order_is_stable_as_listed(tmp_path):
    _write(tmp_path, "go.mod", "module x\n")
    _write(tmp_path, "ruff.toml", "")
    _write(tmp_path, "biome.json", "{}")
    _write(tmp_path, "package.json", json.dumps({"devDependencies": {"oxlint": "^1"}}))
    assert _names(detect_stacks(tmp_path)) == ["ruff", "oxlint", "biome", "gofmt"]


def test_empty_repo_has_no_stacks(tmp_path):
    assert detect_stacks(tmp_path) == []


# ── planning ───────────────────────────────────────────────────────────


def test_plan_limits_each_stack_to_its_changed_files(tmp_path):
    _write(tmp_path, "ruff.toml", "")
    _write(tmp_path, "biome.json", "{}")
    stacks = detect_stacks(tmp_path)
    changed = ["a.py", "sub/d.pyi", "web/x.jsx", "README.md", "notes.txt"]
    commands = plan_commands(tmp_path, stacks, changed)
    assert [c.name for c in commands] == ["ruff", "biome"]
    ruff, biome = commands
    assert ruff.argv == ("ruff", "check", "--force-exclude", "a.py", "sub/d.pyi")
    assert ruff.files == ("a.py", "sub/d.pyi")
    assert ruff.fix == "ruff check --fix a.py sub/d.pyi"
    assert biome.argv == ("biome", "check", "--no-errors-on-unmatched", "web/x.jsx")
    assert biome.fix == "biome check --write web/x.jsx"


def test_no_command_when_no_changed_file_matches(tmp_path):
    _write(tmp_path, "ruff.toml", "")
    assert plan_commands(tmp_path, detect_stacks(tmp_path), ["README.md"]) == []


def test_tsc_is_one_project_command_without_fix(tmp_path):
    _write(tmp_path, "tsconfig.json", "{}")
    commands = plan_commands(tmp_path, detect_stacks(tmp_path), ["a.ts", "b/c.tsx", "d.ts"])
    assert commands == [
        PlannedCommand(
            name="tsc",
            argv=("tsc", "--noEmit", "-p", "tsconfig.json"),
            files=("a.ts", "b/c.tsx", "d.ts"),
            fix=None,
        )
    ]


def test_fix_strings_quote_files_with_spaces(tmp_path):
    _write(tmp_path, "go.mod", "module x\n")
    commands = plan_commands(tmp_path, detect_stacks(tmp_path), ["my dir/it's.go"])
    assert commands[0].argv == ("gofmt", "-l", "my dir/it's.go")
    assert commands[0].fail_on_output is True
    assert commands[0].fix == "gofmt -w 'my dir/it'\"'\"'s.go'"


def test_cargo_fmt_and_gofmt_argv(tmp_path):
    _write(tmp_path, "Cargo.toml", "[package]\n")
    _write(tmp_path, "go.mod", "module x\n")
    cargo, gofmt = plan_commands(tmp_path, detect_stacks(tmp_path), ["src/lib.rs", "m.go"])
    assert cargo.argv == ("cargo", "fmt", "--check", "--", "src/lib.rs")
    assert cargo.fix == "cargo fmt -- src/lib.rs"
    assert gofmt.argv == ("gofmt", "-l", "m.go")
    assert gofmt.fix == "gofmt -w m.go"


def test_changed_paths_are_repo_relative(tmp_path):
    _write(tmp_path, "ruff.toml", "")
    changed = [str(tmp_path / "a.py"), "./b.py", "./b.py"]
    commands = plan_commands(tmp_path, detect_stacks(tmp_path), changed)
    assert commands[0].files == ("a.py", "b.py")


def test_resolve_prefers_node_modules_bin_for_js_tools(tmp_path):
    local = tmp_path / "node_modules" / ".bin" / "prettier"
    _write(tmp_path, "node_modules/.bin/prettier", "#!/bin/sh\n")
    assert resolve_executable(tmp_path, "prettier") == str(local)
    assert resolve_executable(tmp_path, "definitely-not-a-tool-xyz") is None

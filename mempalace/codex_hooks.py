"""Read-only diagnostics for Codex project hook trust."""

import os
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python 3.10
    import tomli as tomllib


def _read_toml(path: Path) -> dict:
    if not path.is_file():
        return {}
    with path.open("rb") as stream:
        return tomllib.load(stream)


def codex_hook_notice(project_dir: Path) -> str | None:
    """Warn about inactive project hooks without changing Codex configuration.

    Inspect project layers up to the nearest repository root. No hook commands
    are executed and no trust state is written. Hook-definition review is a
    separate Codex gate; a trusted project alone does not establish hook trust.
    """
    project_dir = project_dir.expanduser().resolve()
    directories = []
    has_hooks = False
    for directory in (project_dir, *project_dir.parents):
        directories.append(directory)
        local = directory / ".codex"
        try:
            has_hooks |= (local / "hooks.json").is_file() or bool(
                _read_toml(local / "config.toml").get("hooks")
            )
        except (OSError, ValueError) as exc:
            return f"Cannot check Codex project hooks in {local}: {exc}"
        if (directory / ".git").exists():
            break
    if not has_hooks:
        return None

    codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()
    config_path = codex_home / "config.toml"
    try:
        projects = _read_toml(config_path).get("projects", {})
    except (OSError, ValueError) as exc:
        return f"Cannot check Codex project trust in {config_path}: {exc}"
    if isinstance(projects, dict):
        for directory in directories:
            entry = projects.get(str(directory))
            if isinstance(entry, dict) and entry.get("trust_level") in ("trusted", "untrusted"):
                if entry["trust_level"] == "trusted":
                    return None
                break
    return (
        f"Codex project is not trusted in {config_path}; project-local hooks will be skipped. "
        "Review project trust in Codex, then review hook definitions with /hooks."
    )

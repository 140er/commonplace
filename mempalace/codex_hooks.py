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


def _project_layers(project_dir: Path) -> tuple[Path, ...]:
    """Bound project config discovery to a repository, or just the cwd."""
    candidates = (project_dir, *project_dir.parents)
    for index, directory in enumerate(candidates):
        if (directory / ".git").exists():
            return candidates[: index + 1]
    return (project_dir,)


def _worktree_repo_root(project_root: Path) -> Path | None:
    """Find the main checkout from a linked worktree's gitdir pointer."""
    marker = project_root / ".git"
    if not marker.is_file():
        return None
    try:
        contents = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not contents.startswith("gitdir:"):
        return None
    gitdir = Path(contents.removeprefix("gitdir:").strip())
    if not gitdir.is_absolute():
        gitdir = project_root / gitdir
    if gitdir.parent.name == "worktrees" and gitdir.parent.parent.name == ".git":
        return gitdir.parent.parent.parent
    return None


def _trust_level(projects: dict, paths: list[Path]) -> str | None:
    """Match Codex's canonical/original spellings and Windows case folding."""
    if os.name == "nt":
        projects = {key.lower(): value for key, value in projects.items()}
    for path in paths:
        key = str(path).lower() if os.name == "nt" else str(path)
        entry = projects.get(key)
        if isinstance(entry, dict) and entry.get("trust_level") in ("trusted", "untrusted"):
            return entry["trust_level"]
    return None


def codex_hook_notice(project_dir: Path) -> str | None:
    """Warn about inactive project hooks without changing Codex configuration.

    Inspect project layers up to the nearest repository root. No hook commands
    are executed and no trust state is written. Hook-definition review is a
    separate Codex gate; a trusted project alone does not establish hook trust.
    """
    original = project_dir.expanduser().absolute()
    project_dir = original.resolve()
    directories = _project_layers(project_dir)
    codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()
    has_hooks = False
    for directory in directories:
        local = directory / ".codex"
        # User-level hooks always run, independently of project trust.
        if local.resolve() == codex_home.resolve() or directory == codex_home.resolve():
            continue
        try:
            has_hooks |= (local / "hooks.json").is_file() or bool(
                _read_toml(local / "config.toml").get("hooks")
            )
        except (OSError, ValueError) as exc:
            return f"Cannot check Codex project hooks in {local}: {exc}"
    if not has_hooks:
        return None

    config_path = codex_home / "config.toml"
    try:
        projects = _read_toml(config_path).get("projects", {})
    except (OSError, ValueError) as exc:
        return f"Cannot check Codex project trust in {config_path}: {exc}"
    paths = []
    original_parents = (original, *original.parents)
    for index, directory in enumerate(directories):
        paths.append(directory)
        if index < len(original_parents):
            paths.append(original_parents[index])
    repo_root = _worktree_repo_root(directories[-1])
    if repo_root is not None:
        paths.extend((repo_root.resolve(), repo_root))
    if isinstance(projects, dict) and _trust_level(projects, paths) == "trusted":
        return None
    return (
        f"Codex project is not trusted in {config_path}; project-local hooks will be skipped. "
        "Review project trust in Codex, then review hook definitions with /hooks."
    )

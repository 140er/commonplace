"""Resolve and change the handoff setting. Owner: part T05.

Resolution, most specific wins per field: task row > agent row > workspace
row > defaults (off, ``DEFAULT_THRESHOLD``, window from the model,
``client`` launcher). ``source`` names the level that decided ``enabled``.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import PurePosixPath, PureWindowsPath
from string import Formatter
from typing import Optional
from urllib.parse import urlsplit

from .models import (
    EVENT_SETTINGS,
    LAUNCHER_KINDS,
    SETTING_SCOPES,
    HandoffSettings,
    LauncherConfig,
    SettingRow,
)

THRESHOLD_MIN = 0.50
THRESHOLD_MAX = 0.95

_AGENT_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,119}")
_TASK_REF = re.compile(r"#?\w[\w.-]{0,63}")
_EXECUTABLE_NAME = re.compile(r"[A-Za-z0-9_.+-]+")
_PLACEHOLDERS = frozenset(("prompt", "prompt_file", "handoff_id", "thread_id", "cwd"))
_OWNER_ONLY = "only workspace owners can change this"


class SettingsError(ValueError):
    pass


def resolve(rows: list[SettingRow]) -> HandoffSettings:
    """``rows`` in setting_rows order (workspace, agent, task)."""
    values = {}
    source = "default"
    for row in rows:
        for name in ("enabled", "threshold", "context_window", "launcher"):
            value = getattr(row, name)
            if value is not None:
                values[name] = value
        if row.enabled is not None:
            source = row.scope
    return HandoffSettings(**values, source=source)


def _normal_ref(scope: str, ref: str) -> str:
    if scope not in SETTING_SCOPES:
        raise SettingsError("scope must be workspace, agent or task")
    if not isinstance(ref, str):
        raise SettingsError("ref must be a string")
    if scope == "workspace":
        if ref:
            raise SettingsError("workspace ref must be empty")
        return ref
    if scope == "agent":
        if not _AGENT_REF.fullmatch(ref):
            raise SettingsError("agent ref must be 1-120 letters, digits or . _ : @ -")
        return ref
    if not _TASK_REF.fullmatch(ref):
        raise SettingsError("task ref must be 1-64 word characters or . - with an optional #")
    number = ref.removeprefix("#")
    return f"#{int(number)}" if number.isdecimal() else ref


def _validate_command(argv: tuple) -> None:
    if not argv:
        raise SettingsError("command launcher needs a non-empty argv")
    executable = argv[0]
    if not executable or any(ord(char) < 32 or ord(char) == 127 for char in executable):
        raise SettingsError("command must start with an executable name or absolute path")
    is_name = _EXECUTABLE_NAME.fullmatch(executable) and executable not in (".", "..")
    if not (
        is_name
        or PurePosixPath(executable).is_absolute()
        or PureWindowsPath(executable).is_absolute()
    ):
        raise SettingsError("command must start with an executable name or absolute path")
    for arg in argv:
        try:
            fields = list(Formatter().parse(arg))
        except ValueError:
            raise SettingsError("command argv has invalid placeholders") from None
        for _, name, spec, conversion in fields:
            if name is not None and (name not in _PLACEHOLDERS or spec or conversion is not None):
                raise SettingsError("command argv has invalid placeholders")


def _validate_webhook(url: str, secret: str) -> None:
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url):
        raise SettingsError("webhook launcher needs an http(s) URL with a host")
    try:
        parsed = urlsplit(url)
        valid = parsed.scheme in ("http", "https") and bool(parsed.hostname)
        _ = parsed.port  # Validate a supplied port as well as the host.
    except ValueError:
        valid = False
    if not valid:
        raise SettingsError("webhook launcher needs an http(s) URL with a host")
    if len(secret) < 16:
        raise SettingsError("webhook launcher secret must be at least 16 characters")


def _validate_launcher(config: LauncherConfig) -> LauncherConfig:
    if not isinstance(config, LauncherConfig):
        raise SettingsError("launcher must be a launcher configuration")
    if config.kind not in LAUNCHER_KINDS:
        raise SettingsError("launcher kind must be client, command or webhook")
    if not isinstance(config.argv, (list, tuple)) or any(
        not isinstance(arg, str) or "\0" in arg for arg in config.argv
    ):
        raise SettingsError("launcher argv must be an array of strings without null characters")
    if not isinstance(config.url, str) or not isinstance(config.secret, str):
        raise SettingsError("launcher URL and secret must be strings")
    argv = tuple(config.argv)
    if config.kind == "command":
        _validate_command(argv)
    elif config.kind == "webhook":
        _validate_webhook(config.url, config.secret)
    return replace(config, argv=argv)


def validate(row: SettingRow, *, is_owner: bool) -> SettingRow:
    """Check ranges and the launcher. Only owners may set ``command`` or
    ``webhook`` launchers or touch workspace/agent rows; a member may set a
    ``task`` row's ``enabled`` only. Raises ``SettingsError``."""
    if not is_owner and (
        row.scope != "task"
        or row.enabled is None
        or any(value is not None for value in (row.threshold, row.context_window, row.launcher))
    ):
        raise SettingsError(_OWNER_ONLY)
    ref = _normal_ref(row.scope, row.ref)
    if row.enabled is not None and not isinstance(row.enabled, bool):
        raise SettingsError("enabled must be true or false")
    if row.threshold is not None and (
        isinstance(row.threshold, bool)
        or not isinstance(row.threshold, (int, float))
        or not THRESHOLD_MIN <= row.threshold <= THRESHOLD_MAX
    ):
        raise SettingsError(f"threshold must be between {THRESHOLD_MIN} and {THRESHOLD_MAX}")
    if row.context_window is not None and (
        isinstance(row.context_window, bool)
        or not isinstance(row.context_window, int)
        or not 8_000 <= row.context_window <= 10_000_000
    ):
        raise SettingsError("context window must be an integer between 8000 and 10000000")
    launcher = _validate_launcher(row.launcher) if row.launcher is not None else None
    return replace(row, ref=ref, launcher=launcher)


def parse_launcher(data: Optional[dict]) -> Optional[LauncherConfig]:
    """Parse and validate JSON fields; ``None`` means inherit the launcher."""
    if data is None:
        return None
    if not isinstance(data, dict):
        raise SettingsError("launcher must be an object")
    if data.keys() - {"kind", "argv", "url", "secret"}:
        raise SettingsError("unknown launcher fields")
    return _validate_launcher(LauncherConfig(**data))


class SettingsService:
    def __init__(self, handoff_db, events=None):
        self.db = handoff_db
        self.events = events

    def effective(self, workspace_id: str, agent: str = "", task_ref: str = "") -> HandoffSettings:
        return resolve(self.db.setting_rows(workspace_id, agent, task_ref))

    def set(self, row: SettingRow, *, actor: str, is_owner: bool) -> SettingRow:
        """Validate, store, emit ``EVENT_SETTINGS``."""
        saved = self.db.put_setting(validate(row, is_owner=is_owner), actor=actor)
        if self.events is not None:
            self.events.emit(
                EVENT_SETTINGS,
                workspace_id=saved.workspace_id,
                reason=f"set {saved.scope} {saved.ref}".rstrip(),
            )
        return saved

    def clear(self, workspace_id: str, scope: str, ref: str, *, actor: str, is_owner: bool) -> bool:
        """Remove an override. Only owners may delete rows, including task rows."""
        if not is_owner:
            raise SettingsError(_OWNER_ONLY)
        ref = _normal_ref(scope, ref)
        changed = self.db.delete_setting(workspace_id, scope, ref, actor=actor)
        if changed and self.events is not None:
            self.events.emit(
                EVENT_SETTINGS, workspace_id=workspace_id, reason=f"clear {scope} {ref}".rstrip()
            )
        return changed

"""Resolve and change the handoff setting. Owner: part T05.

Resolution, most specific wins per field: task row > agent row > workspace
row > defaults (off, ``DEFAULT_THRESHOLD``, window from the model,
``client`` launcher). ``source`` names the level that decided ``enabled``.
"""

from __future__ import annotations

from typing import Optional

from .models import HandoffSettings, LauncherConfig, SettingRow

THRESHOLD_MIN = 0.50
THRESHOLD_MAX = 0.95


class SettingsError(ValueError):
    pass


def resolve(rows: list[SettingRow]) -> HandoffSettings:
    """``rows`` in setting_rows order (workspace, agent, task)."""
    raise NotImplementedError


def validate(row: SettingRow, *, is_owner: bool) -> SettingRow:
    """Check ranges and the launcher. Only owners may set ``command`` or
    ``webhook`` launchers or touch workspace/agent rows; a member may set a
    ``task`` row's ``enabled`` only. Raises ``SettingsError``."""
    raise NotImplementedError


def parse_launcher(data: Optional[dict]) -> Optional[LauncherConfig]:
    raise NotImplementedError


class SettingsService:
    def __init__(self, handoff_db, events=None):
        raise NotImplementedError

    def effective(self, workspace_id: str, agent: str = "", task_ref: str = "") -> HandoffSettings:
        raise NotImplementedError

    def set(self, row: SettingRow, *, actor: str, is_owner: bool) -> SettingRow:
        """Validate, store, emit ``EVENT_SETTINGS``."""
        raise NotImplementedError

    def clear(self, workspace_id: str, scope: str, ref: str, *, actor: str, is_owner: bool) -> bool:
        raise NotImplementedError

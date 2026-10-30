"""Start the successor agent. Owner: part T08.

``client``: returns ``None``; the calling hook starts the successor and
registers it (``POST /api/handoffs/<id>/successor``). ``command``: run
``argv`` detached, no shell, placeholders filled per argument, env gets
``COMMONPLACE_HANDOFF_ID`` and ``COMMONPLACE_THREAD_ID``. ``webhook``: POST
JSON with an ``X-Commonplace-Signature: sha256=<hmac>`` header, 10 s
timeout, expects ``{"agent_ref", "url"?, "thread_id"?}``.
"""

from __future__ import annotations

from typing import Optional

from .models import Handoff, LauncherConfig, SuccessorRef


class LaunchError(RuntimeError):
    pass


def launch(
    config: LauncherConfig, handoff: Handoff, prompt: str, *, prompt_dir: str
) -> Optional[SuccessorRef]:
    """Raises ``LaunchError`` on failure. ``prompt_dir``: where
    ``{prompt_file}`` is written (0600)."""
    raise NotImplementedError

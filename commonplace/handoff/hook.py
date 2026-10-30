"""Client hook: hand off instead of compacting. Owner: part T12.

Run by Claude Code (PostToolUse and PreCompact) and Codex hooks as
``python -m commonplace.handoff.hook --client claude-code``; reads the hook
JSON on stdin. Config: ``COMMONPLACE_URL``, ``COMMONPLACE_TOKEN`` (cmmn_…),
``COMMONPLACE_HANDOFF`` (``off`` forces off locally), and
``~/.commonplace/handoff-client.json`` for the successor command
(``{"successor_argv": ["claude", "-p", "{prompt}"]}``).

Below the threshold it exits 0 with no output, fast (one tail read, no
network). Above it: POST /api/handoff; on ``action: handoff`` with
``launch: true`` start the successor (detached), register it, and print
``{"continue": false, "stopReason": "Handed off to …"}``. Any error, or
``action: compact``: exit 0 silently, so normal compaction happens.
"""

from __future__ import annotations


def main(argv=None) -> int:
    raise NotImplementedError


if __name__ == "__main__":
    raise SystemExit(main())

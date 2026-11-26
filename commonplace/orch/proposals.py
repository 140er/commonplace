"""Pending config proposals as owner decisions in Waiting on you (card #662).

``pending`` lists every proposed config version the caller may decide: the board, version,
digest to accept, the proposer by display name, a one-line plain-words summary and the diff
against what is accepted now. Accepting and rejecting stay on the orchestrator routes
(``orch_config_accept`` / ``orch_config_reject``); nothing here writes or loosens a check.
"""

from __future__ import annotations

import logging
from pathlib import Path

from commonplace.http_util import log_failure
from commonplace.projects.types import DEFAULT_SEAT_PREFIX

from . import schema, store

logger = logging.getLogger("commonplace.orch")

MAX_ITEMS = 20
SUMMARY_LINES = 3
LIMIT = "limits.max_agents"


def _limit(value) -> str:
    return "no limit" if value is None else str(value)


def _line(change: schema.Change) -> str:
    parts = change.path.split(".")
    if change.path == LIMIT:
        if change.op == "remove":
            return "Agent limit removed"
        if change.op == "add":
            return f"Agent limit set to {_limit(change.new)}"
        return f"Agent limit: {_limit(change.old)} to {_limit(change.new)}"
    if parts[0] == "roles" and len(parts) == 3:
        role, field = parts[1], parts[2]
        word = {
            "prompt": "instructions",
            "model_role": "model",
            "workflow": "workflow",
            "topic": "topic",
            "skills": "skills",
        }.get(field, field.replace("_", " "))
        return f"Changes the {role} {word}"
    if parts[0] == "roles" and len(parts) == 2:
        return f"Adds the {parts[1]} role" if change.op == "add" else f"Removes the {parts[1]} role"
    top = parts[0].replace("_", " ")
    return {
        "add": f"Adds {top}",
        "remove": f"Removes {top}",
    }.get(change.op, f"Changes {top}")


def summarize(changes) -> str:
    """The changed values in plain words, at most three, then "and N more changes"."""
    lines = []
    for change in changes:
        line = _line(change)
        if line not in lines:
            lines.append(line)
    if not lines:
        return "No changes to the settings"
    shown, rest = lines[:SUMMARY_LINES], len(lines) - SUMMARY_LINES
    text = "; ".join(shown)
    if rest > 0:
        text += f" and {rest} more change{'s' if rest != 1 else ''}"
    return text


def first_summary(config) -> str:
    """No accepted config to compare with: what the proposal sets up."""
    parts = []
    if config.max_agents is not None:
        parts.append(f"Agent limit {config.max_agents}")
    roles = len(config.roles)
    parts.append(f"{roles} role{'s' if roles != 1 else ''}")
    return "First config: " + ", ".join(parts)


def proposer_name(proposed_by: str, board_name: str, people=None) -> str:
    """A person by name, the project's Coworker seat as "<project> bot", another app by its name."""
    if proposed_by.startswith("conn:"):
        name = proposed_by[5:]
        if name.startswith(DEFAULT_SEAT_PREFIX) and board_name:
            return f"{board_name} bot"
        return name
    if people is not None:
        return people(proposed_by)
    return proposed_by


def _changes(row, baseline):
    """``(summary, diff text)``; a body that no longer parses is said so, never raised."""
    try:
        config = schema.parse(row["body"], expected_sha256=row["sha256"])
        if baseline is None:
            changes = [
                schema.Change(path=key, op="add", new=value)
                for key, value in sorted(config.to_dict().items())
            ]
            return first_summary(config), schema.format_diff(changes)
        changes = schema.diff(schema.parse(baseline.body), config)
    except schema.ConfigError:
        return "The changes could not be read", None
    return summarize(changes), schema.format_diff(changes)


def pending(gateway, workspace_id: str, *, boards, people=None) -> list[dict]:
    """Proposed versions on ``boards`` (slug -> display name), newest first, capped.

    Empty when the orchestrator store does not exist yet; read-only (no statement runs on
    open). The caller decides who may see the list: it is for the signed-in owner only."""
    directory = gateway.workspace_dir(workspace_id)
    if not boards or not (Path(directory) / "runner.sqlite3").exists():
        return []
    if store.marker_pending(directory):
        return []
    try:
        st = store.open_existing(gateway, workspace_id)
        with st.connection() as conn:
            if not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='orch_configs'"
            ).fetchone():
                return []
            marks = ",".join("?" * len(boards))
            rows = conn.execute(
                f"SELECT * FROM orch_configs WHERE status='proposed' AND board IN ({marks}) "
                "ORDER BY created_at DESC, version DESC LIMIT ?",
                (*boards, MAX_ITEMS),
            ).fetchall()
            items = []
            for row in rows:
                baseline = store.accepted_in(conn, row["board"], row["channel"])
                if baseline is None and row["channel"] != store.LIVE:
                    baseline = store.accepted_in(conn, row["board"], store.LIVE)
                summary, diff = _changes(row, baseline)
                items.append(
                    {
                        "board": row["board"],
                        "board_name": boards[row["board"]],
                        "version": row["version"],
                        "ref": f"v{row['version']}",
                        "channel": row["channel"],
                        "sha256": row["sha256"],
                        "proposed_by": row["proposed_by"],
                        "proposer_name": proposer_name(
                            row["proposed_by"], boards[row["board"]], people
                        ),
                        "proposed_at": row["created_at"],
                        "first": baseline is None,
                        "summary": summary,
                        "diff": diff,
                    }
                )
            return items
    except Exception as exc:
        log_failure(logger, "config proposals unavailable", exc)
        return []

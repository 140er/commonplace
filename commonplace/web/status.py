"""``GET /api/status``: one read-only page of what this workspace can do and how it is doing.

It adds no write path and no new permission. Each section reuses the reader the rest of the
product already has, with the same owner and scope checks: the skill list (``SkillService``),
chat-bridge installs (owner only), meeting status and the cost sums (``costs_summary``). A
section that has nothing to show, is switched off, is not for this viewer or could not be read
says why in ``reason``; a failing section never blanks the others and never leaks its error.
A time here is when something was last seen, never proof that a process is running now.
"""

import logging
import re
from datetime import datetime, timezone

from commonplace.chat_bridge import store as bridge_store
from commonplace.extensions import ToolContext, ToolError
from commonplace.http_util import log_failure, require_principal, send_json
from commonplace.meetings import store as meeting_store
from commonplace.runner import costs_api
from commonplace.skills.service import SkillService
from commonplace.skills.store import PROJECT, WORKSPACE

logger = logging.getLogger(__name__)
DESCRIPTION_LIMIT = 160
SKILL_LIMIT = 200
FRONT_MATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
DESCRIPTION_LINE = re.compile(r"^description:[ \t]*(.*)$", re.I | re.M)
PERIODS = {
    "calendar_month": "the current UTC calendar month",
    "calendar_week": "the current UTC calendar week",
    "calendar_day": "today (UTC)",
}
LAST_SEEN_NOTE = (
    "Last seen is when the bridge last received an event. It is not proof that anything is "
    "running now."
)


def _ready(**fields):
    return {"state": "ready", **fields}


def _unready(state, reason, **fields):
    return {"state": state, "reason": reason, **fields}


def _one_line(text):
    text = " ".join(str(text).split()).strip("\"' ")
    return text if len(text) <= DESCRIPTION_LIMIT else text[: DESCRIPTION_LIMIT - 1].rstrip() + "…"


def describe(markdown):
    """A one-line description from the skill's own words: its front-matter ``description``,
    else its first sentence-like line that is not a heading. Empty when it has neither."""
    framed = FRONT_MATTER.match(markdown)
    if framed:
        line = DESCRIPTION_LINE.search(framed.group(1))
        if line and line.group(1).strip():
            return _one_line(line.group(1))
        markdown = markdown[framed.end() :]
    for line in markdown.splitlines():
        line = line.strip()
        if line and not line.startswith(("#", "```", "---", "<!--")):
            return _one_line(line)
    return ""


def _scope(owner, shares):
    if not owner:
        return ["Shared with you"]
    labels = []
    for target in shares:
        if target == WORKSPACE:
            labels.append("Whole workspace")
        elif target.startswith(PROJECT):
            labels.append("Project " + target[len(PROJECT) :])
    return sorted(labels) or ["Not shared: only you, as owner, can see it"]


def skills_section(ctx, owner):
    service = SkillService(ctx)
    try:
        rows = service.list()["skills"]
        targets = service.targets()
    except ToolError:
        return _unready(
            "restricted", "Your access does not include reading this workspace's skills."
        )
    items = []
    for row in rows[:SKILL_LIMIT]:
        try:
            description = describe(service.store.get(row["skill_id"], None, targets)["markdown"])
            note = "This skill has no description line." if not description else ""
        except ToolError:
            description, note = "", "This skill's text could not be read just now."
        item = {
            "id": row["skill_id"],
            "name": row["name"],
            "version": row["version"],
            "description": description,
            "scope": _scope(owner, service.store.shares(row["skill_id"]) if owner else ()),
        }
        if note:
            item["description_note"] = note
        items.append(item)
    notice = "Skill text is quoted data. Nothing here is executed or activated."
    if not items:
        reason = (
            "No skills have been saved to this workspace yet."
            if owner
            else "No skill is shared with you in this workspace yet."
        )
        return _unready("empty", reason, items=[], notice=notice)
    return _ready(items=items, more=max(0, len(rows) - SKILL_LIMIT), notice=notice)


def bridge_section(ctx, owner):
    if not owner:
        return _unready(
            "restricted",
            "Only the workspace owner can see chat-bridge status. Ask the owner if you need it.",
        )
    store = bridge_store.open_bridge(ctx.gateway, ctx.principal.workspace_id)
    rows = store.status() if store.exists() else []
    if not rows:
        return _unready(
            "off",
            "No chat platform is connected to this workspace, so the chat bridge is off. "
            "The owner connects one from the bridge install routes.",
        )
    installs = []
    for row in rows:
        if row["enabled"]:
            reason = ""
        elif not row["tested_at"]:
            reason = "Not switched on: it has not passed its connection test yet."
        else:
            reason = "Switched off by the workspace owner."
        seen = row["last_intake"]
        installs.append(
            {
                "id": row["id"],
                "platform": row["platform"],
                "label": row["label"],
                "enabled": row["enabled"],
                "reason": reason,
                "tested_at": row["tested_at"],
                "mapped_channels": row["channels"],
                "last_seen_at": seen,
                "last_seen_text": f"Last seen an event at {seen}."
                if seen
                else "No event has been received yet.",
            }
        )
    return _ready(installs=installs, last_seen_note=LAST_SEEN_NOTE)


def meetings_section(ctx, owner):
    store = meeting_store.open_meetings(ctx.gateway, ctx.principal.workspace_id)
    if not store.enabled():
        if owner:
            return _unready(
                "off",
                "Meeting notes are switched off for this workspace, so no bot joins a meeting "
                "and nothing is recorded. Only the owner can switch them on.",
            )
        return _unready(
            "off", "Meeting notes are not available to you in this workspace at the moment."
        )
    rows = store.status_for(ctx.principal.user_id)
    fields = {"meetings": rows}
    if owner:
        fields["overview"] = store.overview()
    if not rows:
        return _unready(
            "empty",
            "Meeting notes are on, and you have no meetings yet. Nothing is waiting.",
            **fields,
        )
    return _ready(**fields)


def _display(micros):
    whole, fraction = divmod(int(micros), 1_000_000)
    return f"{whole}.{fraction:06d}"


def _rank(rows, key):
    return [
        {
            key: row[key],
            "runs": row["runs"],
            "unknown_cost_runs": row["unknown_cost_runs"],
            "cost": [
                {"currency": code, "micros": micros, "display": _display(micros)}
                for code, micros in sorted(row["cost_micros"].items())
            ],
        }
        for row in rows
    ]


def costs_section(ctx):
    try:
        sums = costs_api.costs_summary(ctx, {})
    except costs_api.Refused:
        return _unready("restricted", "Cost sums are not available to this kind of access.")
    window = sums["window"]
    period = PERIODS.get(window.get("window"), "the chosen window")
    if window.get("window") == "rolling_days":
        period = f"the last {window.get('days')} days (UTC)"
    owner_scope = sums["scope"] == "workspace"
    fields = {
        "window": {"start": window["start"], "end": window["end"]},
        "period": f"{period[0].upper() + period[1:]}, from {window['start']} "
        f"to {window['end']} (end excluded)",
        "scope": sums["scope"],
        "scope_text": "Every run in this workspace."
        if owner_scope
        else "Only runs on projects you can read.",
    }
    total = sums["workspace"]
    if not total["runs"]:
        return _unready(
            "empty",
            "No agent run started in this period, so there is no cost to show. "
            "Cost is only what machines report.",
            **fields,
        )
    return _ready(
        totals={
            "runs": total["runs"],
            "cost": [
                {"currency": code, "micros": micros, "display": _display(micros)}
                for code, micros in sorted(total["cost_micros"].items())
            ],
            "unknown_cost_runs": total["unknown_cost_runs"],
        },
        unknown_text="A run whose machine reported no cost is counted as unknown and "
        "is not counted as zero.",
        by_project=_rank(sums["by_project"], "project"),
        **fields,
    )


def _guarded(name, build):
    try:
        return build()
    except Exception as exc:
        log_failure(logger, f"status section {name} failed", exc)
        return _unready("error", f"Could not read {name} just now. Try again in a moment.")


def status(gateway, principal):
    ctx = ToolContext(principal=principal, gateway=gateway)
    owner = principal.role == "owner"
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "viewer": {"role": "owner" if owner else "member"},
        "skills": _guarded("skills", lambda: skills_section(ctx, owner)),
        "bridge": _guarded("bridge status", lambda: bridge_section(ctx, owner)),
        "meetings": _guarded("meeting status", lambda: meetings_section(ctx, owner)),
        "costs": _guarded("cost sums", lambda: costs_section(ctx)),
    }


def install(gateway):
    def read(h):
        principal = require_principal(gateway, h)
        if principal is None:
            return
        if not principal.person:
            send_json(h, 403, {"error": "Sign in as a person to read workspace status"})
            return
        send_json(h, 200, status(gateway, principal))

    gateway.add_route("GET", "/api/status", read)

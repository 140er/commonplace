"""``/api/handoff/start``: the Deck's read of, and request for, a handoff from a stored thread.

It adds no storage and no new permission. A person opens a stored thread; the live thread that
continues it is the successor its own handoff registered. ``GET`` says whether a handoff can be
requested and, when it cannot, why. ``POST`` without ``confirm`` is a dry run that names the
destination and what would be carried over; ``POST`` with ``confirm`` runs the same
``ManualHandoff.start`` the ``handoff_start`` tool runs, with the same launch authority, the
same checkout binding to this workspace and the same settings. Verbatim history is never
summarized here: the response only counts and names it.
"""

from __future__ import annotations

from .authority import can_launch
from .manual import ManualHandoff, ManualHandoffError
from .recall import ThreadNotFound
from .settings import SettingsError
from .t3 import T3Error, Target

SETTINGS_PAGE = "/app/settings/memory"
NOT_ALLOWED = (
    "Starting a handoff needs a signed-in workspace member, or an agent connection with "
    "handoff:write and trusted trust. You can still read this history."
)


class StartRefusal(Exception):
    """A plain refusal: ``status``, what happened and a working next step."""

    def __init__(self, status, message, next_step="", **extra):
        super().__init__(message)
        self.status, self.message, self.next_step, self.extra = status, message, next_step, extra

    def payload(self):
        body = {"error": self.message, **self.extra}
        if self.next_step:
            body["next_step"] = self.next_step
        return body


def _unavailable(thread_id, reason, next_step="", **extra):
    return {
        "thread": thread_id,
        "state": "unavailable",
        "reason": reason,
        "next_step": next_step,
        **extra,
    }


def _setting(ctx, principal, record):
    return ctx.service.settings.effective(principal.workspace_id, record.agent, record.task_ref)


def inspect(ctx, principal, thread_id, *, readable):
    """What the Deck may offer for stored thread ``thread_id``. ``readable`` is the API's own
    thread check; a thread the caller cannot read is not found, with no hint that it exists."""
    stored = readable(principal, thread_id)
    if not can_launch(principal):
        return _unavailable(stored.id, NOT_ALLOWED, can_start=False)
    record = ctx.handoff_db.by_stored_thread(stored.id)
    if record is None or record.workspace_id != principal.workspace_id:
        return _unavailable(
            stored.id,
            "This thread was saved on its own, so no later thread was started from it and "
            "there is nothing to continue from here.",
            "Open a thread that has a handoff, or start a handoff from the agent that wrote it.",
        )
    if record.successor is None:
        if record.state in ("launch_failed", "failed"):
            return _unavailable(
                stored.id,
                "The handoff saved this thread but the new thread did not start.",
                "Retry the launch: it reuses the saved thread and does not save it again.",
                handoff_id=record.id,
                retry=record.state == "launch_failed",
            )
        return _unavailable(
            stored.id,
            "The new thread for this handoff has not been registered yet.",
            "Wait a moment and reload; it appears once the new thread has started.",
            handoff_id=record.id,
        )
    live = record.successor.thread_id
    if not live:
        return _unavailable(
            stored.id,
            f"The new thread ({record.successor.agent_ref}) was registered without a thread id, "
            "so the app cannot hand it off.",
            "Hand it off from the tool that started it.",
            handoff_id=record.id,
        )
    later = ctx.handoff_db.by_key(principal.workspace_id, f"thread:{live}")
    if later is not None and later.successor is not None and later.stored_thread_id:
        try:
            readable(principal, later.stored_thread_id)
            link = later.stored_thread_id
        except ThreadNotFound:
            link = ""
        return _unavailable(
            stored.id,
            "A later thread in this chain already continues from here.",
            "Open the later thread and hand off from there."
            if link
            else "Ask whoever owns the later thread to continue from there.",
            handoff_id=record.id,
            later_thread=link,
        )
    setting = _setting(ctx, principal, record)
    if not setting.enabled:
        owner = principal.role == "owner"
        return _unavailable(
            stored.id,
            f"Handoff is switched off for this work (set at {setting.source} level).",
            "Turn Handoff on in Memory settings, then reload this page."
            if owner
            else "Ask a workspace owner to turn Handoff on in Memory settings.",
            handoff_id=record.id,
            settings_link=SETTINGS_PAGE,
        )
    return {
        "thread": stored.id,
        "state": "ready",
        "handoff_id": record.id,
        "continues": {
            "thread_id": live,
            "agent": record.agent,
            "task": record.task_ref,
            "branch": record.branch,
        },
        "setting_source": setting.source,
    }


def _explain(error):
    text = str(error)
    if text.startswith("handoff is off"):
        return 409, "Turn Handoff on in Memory settings, then try again."
    if "not bound" in text:
        return 409, (
            "On the machine that hosts this thread, map its checkout to this workspace "
            "(see 'Start a handoff by hand' in the handoff guide), then try again."
        )
    if text == "in progress":
        return 409, "Another handoff of this thread is running. Wait a moment, then try again."
    if "registration failed" in text:
        return 409, "Do not start another one: run this action again to finish registering it."
    if "source is still running" in text or "not settled" in text:
        return 409, (
            "The old thread is still running and nothing was lost. Check that T3 is running on "
            "the machine that hosts this workspace, then try again."
        )
    return (
        409,
        "Try again in a moment. If it keeps failing, check T3 on the machine that hosts this workspace.",
    )


def _destination(value, effort):
    try:
        return Target.parse(value, effort)
    except T3Error:
        raise StartRefusal(
            400,
            "The destination must look like provider/model.",
            "Type it as provider/model, for example codex/gpt-6.1-sol.",
        ) from None


def run(ctx, principal, data, *, readable):
    """Preview (``confirm`` false) or start (``confirm`` true) a handoff for ``data["thread"]``."""
    thread_id = data["thread"]
    offer = inspect(ctx, principal, thread_id, readable=readable)
    if offer["state"] != "ready":
        raise StartRefusal(409, offer["reason"], offer["next_step"])
    target = _destination(data["to"], data.get("effort", ""))
    record = ctx.handoff_db.get(offer["handoff_id"])
    live = offer["continues"]["thread_id"]
    manual = ManualHandoff(ctx.service)
    options = {
        "thread_id": live,
        "to": f"{target.provider}/{target.model}",
        "effort": target.effort,
        "note": data.get("note", ""),
        "agent": record.agent,
        "task": record.task_ref,
    }
    confirm = data.get("confirm", False)
    try:
        if not confirm:
            result = manual.start(principal, require_binding=True, dry_run=True, **options)
            chain = ctx.recall.chain(principal, thread_id)
            return {
                "preview": True,
                "thread": thread_id,
                "continues": offer["continues"],
                "destination": {
                    "provider": target.provider,
                    "model": target.model,
                    "effort": target.effort,
                },
                "transcript_format": result["transcript_format"],
                "settings_source": result["settings_source"],
                "note": options["note"],
                "carried": {
                    "earlier_threads": len(chain),
                    "earlier_messages": sum(row.message_count for row in chain),
                    "history": [row.id for row in chain],
                },
            }
        result = manual.start(principal, require_binding=True, **options)
        return {"preview": False, "thread": thread_id, **result}
    except (ManualHandoffError, T3Error, SettingsError) as error:
        status, next_step = _explain(error)
        raise StartRefusal(status, str(error), next_step) from None
    except OSError:
        raise StartRefusal(
            409,
            "Local handoff data is unavailable, so nothing was started and the old thread is untouched.",
            "Check disk space and permissions on the machine that hosts this workspace, then try again.",
        ) from None

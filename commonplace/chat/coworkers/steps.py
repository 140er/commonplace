"""Small, factual step trees from authorized conversation jobs and run receipts."""

MAX_JOBS = 20


def state(value):
    if value in {"working", "running", "leased", "starting", "started", "doing"}:
        return "working"
    if value in {"done", "completed"}:
        return "done"
    if value in {"uncertain", "needs_me", "review", "waiting"}:
        return "waiting"
    return "idle"


def action(value):
    if value in {"failed", "errored", "interrupted"}:
        return "Stopped after an error"
    if value in {"cancelled", "stopped"}:
        return "Stopped"
    if value in {"pending", "queued", "todo"}:
        return "Waiting to start"
    return {
        "working": "Working on the task",
        "done": "Finished the task",
        "waiting": "Waiting for a decision",
        "idle": "Ready for work",
    }[state(value)]


def tree(bots, jobs):
    """Flat parent links describe the tree; only recorded runs get child nodes."""
    from .livelog import clean

    result = []
    for bot in bots:
        own = [job for job in jobs if job["bot_id"] == bot["id"]]
        active = [job for job in own if state(job["step_state"]) in {"waiting", "working"}]
        latest = active[-1] if active else own[-1] if own else None
        value = latest["step_state"] if latest else "idle"
        result.append(
            {
                "id": bot["id"],
                "parent_id": None,
                "display_name": clean(bot["name"], limit=80),
                "action_summary": clean(latest.get("summary") or action(value), limit=160)
                if latest
                else action(value),
                "state": state(value),
                "at": latest["at"] if latest else bot["created_at"],
            }
        )
        for job in own:
            if job.get("run") is None:
                continue
            run = job["run"]
            result.append(
                {
                    "id": "run:" + str(job["id"]),
                    "parent_id": bot["id"],
                    "display_name": clean(run["display_name"], limit=80),
                    "action_summary": action(run["state"]),
                    "state": state(run["state"]),
                    "at": run["at"],
                }
            )
    return result

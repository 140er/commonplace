"""Local runner interface, awaiting implementation."""

WORKSPACE_STRATEGY = {"type": "root"}  # UNVERIFIED until D2
SPAWN_MESSAGE = (
    "You were started for Commonplace card #{n} only "
    "(job marker commonplace-job:{job_id}). "
    "First claim it with task_take_next(task={n}). "
    "If that claim is refused, stop and do nothing else. "
    "Touch no other card: do not claim, update, comment on or hand off "
    "any card except #{n}."
)


def validate_job(job) -> None:
    raise NotImplementedError("agent")


def run(config_path, once) -> int:
    raise NotImplementedError("agent")


def check(config_path) -> int:
    raise NotImplementedError("agent")

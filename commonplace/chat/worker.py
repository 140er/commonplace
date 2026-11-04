"""Inert S0 worker; no threads, sockets or background writes."""

from commonplace.chat.types import Job

FRONT_SYSTEM = """You are the fast front desk for this project's orchestrator.
Answer in no more than four short sentences.
You cannot start agents, change cards or make decisions.
The orchestrator has already received the person's message; if it does not pick up a work request, the system files it as a card.
Report status only from the given data.
Point to the Decisions queue if decisions wait.
Never ask for secrets."""


class FrontWorker:
    def __init__(self, gateway, *, start: bool = True):
        pass

    def start(self) -> "FrontWorker":
        return self

    def stop(self) -> None:
        return None

    def wake(self) -> None:
        return None

    def run_once(self, now: float | None = None) -> bool:
        raise NotImplementedError

    def drain(self, max_jobs: int = 50, *, now: float | None = None) -> int:
        return 0


def build_context(gateway, principal, job: Job) -> tuple[str, list[dict]]:
    raise NotImplementedError

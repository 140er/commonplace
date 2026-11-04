"""Phone notification dispatch lifecycle."""


class SeedRace(Exception):
    """The event snapshot changed while seeding a subscription."""


class PushDispatcher:
    def __init__(self, db, *, gateway, schedule, max_attempts, sweep_seconds=300):
        raise NotImplementedError("card20 stub")

    @property
    def work(self):
        raise NotImplementedError("card20 stub")

    def start(self):
        raise NotImplementedError("card20 stub")

    def stop(self):
        raise NotImplementedError("card20 stub")

    def wake(self):
        raise NotImplementedError("card20 stub")

    def on_event(self, e):
        raise NotImplementedError("card20 stub")

    def reconcile(self) -> bool:
        raise NotImplementedError("card20 stub")

    def sweep(self):
        raise NotImplementedError("card20 stub")

    def drain(self) -> int:
        raise NotImplementedError("card20 stub")

    def seed(self, owner, topic) -> str:
        raise NotImplementedError("card20 stub")

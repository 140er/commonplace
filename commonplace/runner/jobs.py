"""Job store interface, awaiting implementation."""

QUEUE_LIMIT = 8
STARTED_HOLD_SECONDS = 3600


class Conflict(Exception):
    """A requested job transition conflicts with its current state."""


def _now() -> str:
    raise NotImplementedError("jobs")


def open_store(gateway, ws):
    raise NotImplementedError("jobs")


def create(store, *args, **kwargs):
    raise NotImplementedError("jobs")


def sweep(store, db, ws):
    raise NotImplementedError("jobs")


def lease(*args, **kwargs):
    raise NotImplementedError("jobs")


def renew(*args, **kwargs):
    raise NotImplementedError("jobs")


def starting(*args, **kwargs):
    raise NotImplementedError("jobs")


def result(*args, **kwargs):
    raise NotImplementedError("jobs")


def cancel(*args, **kwargs):
    raise NotImplementedError("jobs")


def resolve(*args, **kwargs):
    raise NotImplementedError("jobs")


def status(*args, **kwargs):
    raise NotImplementedError("jobs")

"""Start predicate interface, awaiting implementation."""


class Refused(Exception):
    """A card cannot be started for the given reason."""

    def __init__(self, reason):
        raise NotImplementedError("predicate")


def startable(gateway, machine, row, board) -> None:
    raise NotImplementedError("predicate")


def requester_ok(gateway, principal, machine) -> bool:
    raise NotImplementedError("predicate")

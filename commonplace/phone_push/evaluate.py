"""Phone notification category evaluation."""


def current(gateway, owner) -> dict[int, set[str]]:
    raise NotImplementedError("card20 stub")


def applies(gateway, owner, number, category, *, cur) -> bool:
    raise NotImplementedError("card20 stub")


def enters(events, number, category, *, after_seq) -> list:
    raise NotImplementedError("card20 stub")

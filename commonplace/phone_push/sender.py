"""Phone notification delivery helpers."""


def drain(dispatcher) -> int:
    raise NotImplementedError("card20 stub")


def recover(db):
    raise NotImplementedError("card20 stub")


def enqueue_test(db, subscription_id):
    raise NotImplementedError("card20 stub")


def post(config, topic, body, *, priority, click) -> tuple:
    raise NotImplementedError("card20 stub")

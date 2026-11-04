"""Machine registry interface, awaiting implementation."""


def ensure(db):
    return None


def enroll(db, owner, grant):
    raise NotImplementedError("registry")


def patch(*args, **kwargs):
    raise NotImplementedError("registry")


def attest(*args, **kwargs):
    raise NotImplementedError("registry")


def rotate(*args, **kwargs):
    raise NotImplementedError("registry")


def revoke(*args, **kwargs):
    raise NotImplementedError("registry")


def get_machine(db, ws, ref):
    raise NotImplementedError("registry")


def machines(db, ws):
    raise NotImplementedError("registry")


def worker_principal(db, machine):
    raise NotImplementedError("registry")

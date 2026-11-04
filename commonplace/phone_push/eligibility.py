"""Owner eligibility and sign-in policy."""

from commonplace.control_db import Principal

PAUSE_SIGNED_OUT: bool = True


def owner_principal(db, user_id, ws) -> Principal | None:
    raise NotImplementedError("card20 stub")


def eligible(db, principal) -> bool:
    raise NotImplementedError("card20 stub")


def signed_in(db, user_id, ws) -> bool:
    raise NotImplementedError("card20 stub")


def paused(db, user_id, ws) -> bool:
    raise NotImplementedError("card20 stub")

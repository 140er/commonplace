"""Frozen console interface; implemented by the owning builder unit."""

skipped: int = 0


def ensure_schema(db) -> None:
    return None


def recording(db, cid) -> bool:
    raise NotImplementedError


def set_recording(db, principal, cid, record: bool) -> None:
    raise NotImplementedError


def recording_ids(db, ws) -> set[str]:
    raise NotImplementedError


def forget(db, connection_id) -> None:
    raise NotImplementedError

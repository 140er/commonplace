"""Decision provenance, distinct from the identity that transported a write."""

from urllib.parse import quote, unquote, urlsplit

from . import provenance

COLUMNS = {
    "origin_kind": "TEXT NOT NULL DEFAULT 'unknown'",
    "origin_id": "TEXT NOT NULL DEFAULT 'unknown'",
    "origin_link": "TEXT",
    "approval_status": "TEXT NOT NULL DEFAULT 'unknown'",
    "approved_by": "TEXT",
    "approval_link": "TEXT",
}


def source_link(thread=None, url=None):
    """A navigable chat pointer; never infer a speaker from a transcript role."""
    if url is not None:
        if not isinstance(url, str) or not url or len(url) > 2048:
            raise ValueError("source_url must be a non-empty URL of at most 2048 characters")
        if any(ord(c) < 33 for c in url) or any(c in url for c in "<>`\\"):
            raise ValueError("source_url contains invalid characters")
        parts = urlsplit(url)
        if not parts.scheme and ".." in unquote(parts.path).split("/"):
            raise ValueError("source_url path must not contain '..' segments")
        if not (
            (parts.scheme in ("http", "https") and parts.netloc and not parts.username)
            or (parts.scheme == "t3-thread" and parts.netloc == "v1" and parts.path)
            or (not parts.scheme and not parts.netloc and parts.path.startswith("/app/threads/"))
        ):
            raise ValueError("source_url must link to a chat or thread")
        return url
    if not thread:
        return None
    if thread.startswith("thr_"):
        return "/app/threads/" + quote(thread, safe="")
    if thread.startswith("mcp:"):
        return "t3-thread://v1/" + quote(thread, safe=":-")
    return None


def evidence(thread=None, url=None):
    """Only an authenticated person can establish user origin or approval."""
    identity = provenance.current() or {}
    link = source_link(thread, url)
    person = identity.get("person") is True
    approved = person and link is not None
    kind = "user" if person else "agent" if identity else "thread" if thread else "unknown"
    decider = identity.get("user") if person else identity.get("connection") or thread or "unknown"
    if not person and identity.get("auth") == "session" and thread:
        # A person session transporting automatic extraction is not a deciding agent.
        kind, decider = "thread", thread
    return {
        "origin_kind": kind,
        "origin_id": decider,
        "origin_link": link,
        "approval_status": "approved" if approved else "proposal",
        "approved_by": identity["user"] if approved else None,
        "approval_link": link if approved else None,
    }


def record(conn, triple_id, thread=None, url=None, *, new=False):
    """Keep original authorship when merging; retain separate user approval evidence."""
    fields = evidence(thread, url)
    if new:
        conn.execute(
            "UPDATE triples SET " + ", ".join(f"{key}=?" for key in COLUMNS) + " WHERE id=?",
            (*fields.values(), triple_id),
        )
    elif fields["approval_status"] == "approved":
        conn.execute(
            "UPDATE triples SET approval_status='approved', approved_by=?, approval_link=? WHERE id=?",
            (fields["approved_by"], fields["approval_link"], triple_id),
        )
    return fields


def fields(row):
    """Approval and lifecycle remain separate in storage and explicit on recall."""
    result = {key: row[key] for key in COLUMNS}
    lifecycle = row["status"] or "active"
    result["fact_status"] = lifecycle if lifecycle != "active" else row["approval_status"]
    return result


def may_replace_approved(thread=None, url=None):
    return evidence(thread, url)["approval_status"] == "approved"


def imported_fields(row):
    """Read stored evidence without borrowing the importing caller's authority.

    Approval can have an agent as its original author. It still needs the
    separate person and navigable approval link minted by a person save.
    Legacy caller stamps and an approval label alone establish nothing.
    """
    result = {key: row.get(key) for key in COLUMNS}
    for key in ("origin_kind", "origin_id", "approval_status"):
        result[key] = result[key] or "unknown"
    approved_by = result["approved_by"]
    try:
        link = source_link(url=result["approval_link"])
    except ValueError:
        link = None
    approved = (
        result["approval_status"] == "approved"
        and isinstance(approved_by, str)
        and bool(approved_by.strip())
        and link is not None
    )
    if not approved:
        if result["approval_status"] == "approved":
            result["approval_status"] = "unknown"
        result["approved_by"] = result["approval_link"] = None
    return result


def merge_imported_approval(conn, triple_id, row):
    """Add valid person approval without replacing any existing approval proof."""
    fields = imported_fields(row)
    if fields["approval_status"] == "approved":
        conn.execute(
            "UPDATE triples SET approval_status='approved', approved_by=?, approval_link=? "
            "WHERE id=? AND approval_status != 'approved'",
            (fields["approved_by"], fields["approval_link"], triple_id),
        )

"""Publish, list, stop and reopen previews, and the workspace stop rule.

The caller is always a ``Principal``; the workspace comes from it, never from the request.
A preview is reachable by the people who can open its card, so every call starts from the
card: an unknown id, another workspace's id and a card the caller cannot see all raise the
same ``NotFound``.
"""

from __future__ import annotations

import logging
import shutil

from commonplace.boards.service import BoardService
from commonplace.boards.store import require_visible, task_row
from commonplace.extensions import ToolContext, ToolError
from commonplace.http_util import log_failure

from . import bundle, store
from .bundle import Refused
from .store import Forbidden, NotFound

log = logging.getLogger("commonplace.previews")


def _remove_tree(path) -> None:
    """The one place files are removed; callers look at the result instead of trusting it."""
    shutil.rmtree(path, ignore_errors=True)


def _service(gateway, principal):
    return BoardService(ToolContext(principal=principal, gateway=gateway))


def _card_row(conn, principal, number):
    try:
        row = task_row(conn, number)
        require_visible(row, principal)
    except ToolError:
        raise NotFound(str(number)) from None
    return row


def card_info(gateway, principal, number, *, write=False) -> dict:
    """The card a caller may act on. A caller that cannot see it gets ``NotFound``; an agent
    token that may read but not write the card's wing gets ``Forbidden`` when ``write``."""
    service = _service(gateway, principal)
    with service.store.connection() as conn:
        row = _card_row(conn, principal, number)
        if (
            write
            and not principal.person
            and not principal.policy.can_write_wing(row["board_wing"])
        ):
            raise Forbidden("card")
        return {
            "number": row["id"],
            "pr_url": row["pr_url"] or "",
            "pr_head": row["pr_head"] or "",
        }


def _visible_cards(gateway, principal, numbers) -> set:
    service = _service(gateway, principal)
    seen = set()
    with service.store.connection() as conn:
        for number in numbers:
            try:
                _card_row(conn, principal, number)
            except NotFound:
                continue
            seen.add(number)
    return seen


def _pr_cards(gateway, principal, pr_url) -> list:
    service = _service(gateway, principal)
    with service.store.connection() as conn:
        numbers = [r[0] for r in conn.execute("SELECT id FROM tasks WHERE pr_url=?", (pr_url,))]
    return sorted(_visible_cards(gateway, principal, numbers))


def _name(raw) -> str:
    if not isinstance(raw, str):
        raise ValueError("name must be text of 1 to 80 characters")
    name = raw.strip()
    if not 1 <= len(name) <= store.MAX_NAME or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ValueError("name must be text of 1 to 80 characters")
    return name


def _who(principal) -> tuple[str, int]:
    return ("a person" if principal.person else principal.connection), int(principal.person)


def _history(gateway, principal, card, text) -> None:
    """One line on the card, so the owner sees every publish. Best effort: the preview's own
    event record is the one that must not be lost."""
    try:
        service = _service(gateway, principal)
        with service.transaction() as conn:
            row = _card_row(conn, principal, card)
            service._log(conn, row["id"], "comment", text=text)
    except Exception as exc:  # noqa: BLE001 - a card that cannot take a note must not undo a publish
        log_failure(log, "preview card note failed", exc)


def _failure(gateway, principal, info, name, exc) -> dict:
    """Record a refused publish: a new name gets a failed row the card shows, an existing
    preview keeps serving what it had. Returns the preview's view; refused attempts that never
    published are kept newest first (``store.MAX_FAILED``) and do not count toward the cap."""
    db = gateway.db
    workspace = principal.workspace_id
    who, by_person = _who(principal)
    now = store.stamp(store.clock())
    with db._lock, db._tx() as conn:
        row = store.row_by_name(conn, workspace, info["number"], name)
        if row is None:
            identity = store.new_id()
            conn.execute(
                "INSERT INTO previews(id,workspace_id,card,name,state,reason,pr_url,pr_head,actor,"
                "published_by,by_person,created_at,updated_at) VALUES (?,?,?,?,'failed',?,?,?,?,?,?,?,?)",
                (
                    identity,
                    workspace,
                    info["number"],
                    name,
                    exc.reason,
                    info["pr_url"],
                    info["pr_head"],
                    principal.actor,
                    who,
                    by_person,
                    now,
                    now,
                ),
            )
            row = store.row_by_id(conn, workspace, identity)
        elif row["current_revision"] == 0 and row["state"] == "failed":
            # Only a refused attempt is refreshed; a publish that is still building is left alone.
            conn.execute(
                "UPDATE previews SET state='failed',reason=?,updated_at=? WHERE id=?",
                (exc.reason, now, row["id"]),
            )
            row = store.row_by_id(conn, workspace, row["id"])
        store.event(
            conn,
            workspace,
            row["id"],
            info["number"],
            "publish_failed",
            principal.actor,
            exc.reason,
        )
        shown = store.view(conn, row)
        store.prune_failed(conn, workspace)
        return shown


def publish(gateway, principal, card, name, files) -> tuple[dict, bool]:
    """Publish ``files`` as the next revision of preview ``name`` on ``card``.

    Returns ``(preview, created)``; ``created`` is False when the bundle is the one already
    served. Raises ``ValueError`` (malformed request), ``NotFound``, ``Forbidden`` or
    ``Refused``; a ``Refused`` for a bundle problem carries the stored failed preview."""
    name = _name(name)
    info = card_info(gateway, principal, card, write=True)
    db, workspace = gateway.db, principal.workspace_id
    who, by_person = _who(principal)
    # A malformed request is an input error and uses no attempt; a bundle that breaks a limit does.
    try:
        items, broken = bundle.parse(files), None
    except Refused as exc:
        items, broken = None, exc
    with db._lock, db._tx() as conn:
        # Check and reserve in one transaction, so racing publishes cannot both slip under it.
        if store.attempts_last_hour(conn, workspace, info["number"]) >= store.MAX_PER_CARD_HOUR:
            raise Refused("rate_limited")
        store.event(conn, workspace, "", info["number"], "publish_started", principal.actor)
    if broken is not None:
        broken.detail["preview"] = _failure(gateway, principal, info, name, broken)
        raise broken
    size = sum(len(data) for _path, data in items)
    digest = bundle.digest(items)
    now = store.stamp(store.clock())
    with db._lock, db._tx() as conn:
        row = store.row_by_name(conn, workspace, info["number"], name)
        if row is not None and row["state"] == "ready":
            held = conn.execute(
                "SELECT digest FROM preview_revisions WHERE preview_id=? AND revision=?",
                (row["id"], row["current_revision"]),
            ).fetchone()
            if held is not None and held["digest"] == digest:
                return store.view(conn, row), False
        refusal = None
        try:
            store.check_room(
                conn, workspace, size, needs_ready_slot=row is None or row["state"] != "ready"
            )
            if (row is None or not store.counts_live(row)) and store.live_rows(
                conn, workspace
            ) >= store.MAX_ROWS:
                raise Refused("too_many_previews")
        except Refused as exc:
            refusal = exc
        before = None
        keep = row is not None and row["state"] == "ready"
        if refusal is not None:
            pass
        elif row is None:
            identity = store.new_id()
            conn.execute(
                "INSERT INTO previews(id,workspace_id,card,name,state,reason,pr_url,pr_head,actor,"
                "published_by,by_person,created_at,updated_at) VALUES (?,?,?,?,'building','storing',?,?,?,?,?,?,?)",
                (
                    identity,
                    workspace,
                    info["number"],
                    name,
                    info["pr_url"],
                    info["pr_head"],
                    principal.actor,
                    who,
                    by_person,
                    now,
                    now,
                ),
            )
        else:
            identity = row["id"]
            if row["state"] != "ready":
                before = (row["state"], row["reason"], row["current_revision"])
                conn.execute(
                    "UPDATE previews SET state='building',reason='storing',updated_at=? WHERE id=?",
                    (now, identity),
                )

    if refusal is not None:
        refusal.detail["preview"] = _failure(gateway, principal, info, name, refusal)
        raise refusal

    def settle(exc):
        """Put the row back (or fail it) after the files could not be stored."""
        with db._lock, db._tx() as conn:
            now = store.stamp(store.clock())
            if keep:
                pass  # it was ready and still serves its current revision
            elif before is not None and before[2] > 0:
                # A stale building row is put back as what it reads as: an unfinished publish.
                state, why = (
                    ("failed", "publish_interrupted") if before[0] == "building" else before[:2]
                )
                conn.execute(
                    "UPDATE previews SET state=?,reason=?,updated_at=? WHERE id=?",
                    (state, why, now, identity),
                )
            else:
                conn.execute(
                    "UPDATE previews SET state='failed',reason=?,updated_at=? WHERE id=?",
                    (exc.reason, now, identity),
                )
            store.event(
                conn,
                workspace,
                identity,
                info["number"],
                "publish_failed",
                principal.actor,
                exc.reason,
            )
            exc.detail["preview"] = store.view(conn, store.row_by_id(conn, workspace, identity))

    try:
        tmp = store.write_files(gateway, workspace, identity, items)
    except Refused as exc:
        settle(exc)
        raise
    failed = changed = final = aside = None
    try:
        with db._lock, db._tx() as conn:
            row = store.row_by_id(conn, workspace, identity)
            # Someone may have stopped this preview while the files were written. A
            # preview that was ready must still be ready; one this publish started must still be
            # building (or already ready through a racing publish of the same name).
            if row["state"] not in (("ready",) if keep else ("building", "ready")):
                changed = Refused("changed_meanwhile")
                store.event(
                    conn,
                    workspace,
                    identity,
                    info["number"],
                    "publish_failed",
                    principal.actor,
                    "changed_meanwhile",
                )
                changed.detail["preview"] = store.view(conn, row)
            else:
                try:
                    # The workspace may have filled while the files were written; look again.
                    store.check_room(
                        conn, workspace, size, needs_ready_slot=row["state"] != "ready"
                    )
                    revision = conn.execute(
                        "SELECT coalesce(max(revision),0)+1 FROM preview_revisions "
                        "WHERE preview_id=?",
                        (identity,),
                    ).fetchone()[0]
                    target = store.revision_dir(gateway, workspace, identity, revision)
                    aside = store.place(tmp, target)
                    final = target
                except Refused as exc:
                    failed = exc
            if failed is None and changed is None:
                now = store.stamp(store.clock())
                conn.execute(
                    "INSERT INTO preview_revisions(preview_id,revision,digest,size_bytes,file_count,"
                    "published_by,by_person,published_at) VALUES (?,?,?,?,?,?,?,?)",
                    (identity, revision, digest, size, len(items), who, by_person, now),
                )
                conn.execute(
                    "UPDATE previews SET state='ready',reason='published',current_revision=?,"
                    "pr_url=?,pr_head=?,actor=?,published_by=?,by_person=?,updated_at=? WHERE id=?",
                    (
                        revision,
                        info["pr_url"],
                        info["pr_head"],
                        principal.actor,
                        who,
                        by_person,
                        now,
                        identity,
                    ),
                )
                store.event(conn, workspace, identity, info["number"], "published", principal.actor)
                shown = store.view(conn, store.row_by_id(conn, workspace, identity))
    except Exception:
        # Nothing was committed: the placed files, and the ones still waiting, must not outlive it.
        if final is not None:
            _remove_tree(final)
        if aside is not None:
            _remove_tree(aside)
        _remove_tree(tmp)
        raise
    if aside is not None:
        _remove_tree(aside)  # a leftover from a crash, removed with the lock released
    if changed is not None:
        _remove_tree(tmp)
        raise changed
    if failed is not None:
        _remove_tree(tmp)
        settle(failed)
        raise failed
    _history(
        gateway,
        principal,
        info["number"],
        f"Published preview {name} (revision {shown['revision']}): {shown['url']}",
    )
    return shown, True


def _authorize(gateway, principal, preview_id) -> None:
    """``NotFound`` unless the preview is in the caller's workspace and the caller can open its
    card; ``Forbidden`` when an agent token no longer holds write access to the card's wing. The
    board is read before any control-database lock is taken, never inside one."""
    db = gateway.db
    with db._lock:
        row = store.row_by_id(db._conn, principal.workspace_id, preview_id)
    if row is None:
        raise NotFound(str(preview_id))
    card_info(gateway, principal, row["card"], write=True)


def _held(conn, principal, preview_id):
    row = store.row_by_id(conn, principal.workspace_id, preview_id)
    if row is None:
        raise NotFound(str(preview_id))
    return row


def get(gateway, principal, preview_id) -> dict:
    db = gateway.db
    with db._lock:
        row = store.row_by_id(db._conn, principal.workspace_id, preview_id)
    if row is None:
        raise NotFound(str(preview_id))
    card_info(gateway, principal, row["card"])
    with db._lock:
        current, revisions = store.snapshot(db._conn, row, revisions=True)
    return store.shape(row, current, revisions)


def listing(gateway, principal, *, card=None, pr=None) -> dict:
    """Previews the caller may see, newest first, with the workspace's usage and limits."""
    db, workspace = gateway.db, principal.workspace_id
    if card is not None:
        numbers = [card_info(gateway, principal, card)["number"]]
    elif pr is not None:
        numbers = _pr_cards(gateway, principal, pr)
    else:
        numbers = None
    with db._lock:
        rows = db._conn.execute(
            "SELECT * FROM previews WHERE workspace_id=? ORDER BY updated_at DESC, id DESC",
            (workspace,),
        ).fetchall()
    if numbers is None:
        allowed = _visible_cards(gateway, principal, sorted({r["card"] for r in rows}))
    else:
        allowed = set(numbers)
    # One query for every current revision, then the shaping happens outside the lock.
    with db._lock:
        current = {
            r["preview_id"]: r
            for r in db._conn.execute(
                "SELECT r.* FROM preview_revisions r JOIN previews p ON p.id=r.preview_id "
                "AND r.revision=p.current_revision WHERE p.workspace_id=?",
                (workspace,),
            )
        }
        held = store.usage(db._conn, workspace)
        rule = rules(db._conn, workspace)
    shown = [store.shape(r, current.get(r["id"])) for r in rows if r["card"] in allowed]
    return {"previews": shown, "usage": held, "limits": store.LIMITS, "rule": rule}


def _change(gateway, principal, preview_id, *, kind, to_state, reason_for, allowed_from):
    _authorize(gateway, principal, preview_id)
    db, workspace = gateway.db, principal.workspace_id
    with db._lock, db._tx() as conn:
        row = _held(conn, principal, preview_id)
        if not principal.person and principal.actor != row["actor"]:
            raise Forbidden("not the publisher")
        if row["state"] not in allowed_from:
            return store.view(conn, row)
        reason = reason_for(row, principal)
        if to_state == "ready":
            held = store.usage(conn, workspace)
            if held["ready"] >= store.MAX_READY:
                raise Refused("workspace_quota_full")
        conn.execute(
            "UPDATE previews SET state=?,reason=?,updated_at=? WHERE id=?",
            (to_state, reason, store.stamp(store.clock()), row["id"]),
        )
        store.event(conn, workspace, row["id"], row["card"], kind, principal.actor, reason)
        return store.view(conn, store.row_by_id(conn, workspace, row["id"]))


def stop(gateway, principal, preview_id) -> dict:
    """Stop serving a ready preview. Its files are kept. A person may stop any preview they
    can see; a Coworker only the ones it published."""
    return _change(
        gateway,
        principal,
        preview_id,
        kind="stopped",
        to_state="stopped",
        reason_for=lambda row, p: "stopped_by_person" if p.person else "stopped_by_coworker",
        allowed_from=("ready",),
    )


def reopen(gateway, principal, preview_id) -> dict:
    """Serve a stopped preview again from its kept files."""
    return _change(
        gateway,
        principal,
        preview_id,
        kind="reopened",
        to_state="ready",
        reason_for=lambda row, p: "published",
        allowed_from=("stopped",),
    )


RULE_BOUNDS = {"stop_days_after_pr": (1, 365), "max_age_days": (1, 3650)}


def rules(conn, workspace_id) -> dict:
    row = conn.execute(
        "SELECT stop_days_after_pr,max_age_days,updated_at FROM preview_rules WHERE workspace_id=?",
        (workspace_id,),
    ).fetchone()
    held = {"stop_days_after_pr": None, "max_age_days": None, "updated_at": None}
    if row is not None:
        held = {k: row[k] for k in held}
    held["text"] = rule_text(held)
    return held


def rule_text(held) -> str:
    parts = []
    if held["stop_days_after_pr"]:
        parts.append(
            f"{held['stop_days_after_pr']} days after its pull request is merged or closed"
        )
    if held["max_age_days"]:
        parts.append(f"{held['max_age_days']} days after it was published")
    if not parts:
        return "Previews are served until a person or the Coworker that published them stops them."
    return "A preview stops being served " + " or ".join(parts) + ". Its files are kept."


def get_rules(gateway, principal) -> dict:
    with gateway.db._lock:
        return rules(gateway.db._conn, principal.workspace_id)


def set_rules(gateway, principal, body) -> dict:
    """Owner only. ``None`` switches a part of the rule off. The rule is stored and shown; the
    serving slice applies it."""
    if not principal.person or principal.role != "owner":
        raise Forbidden("owner")
    if not isinstance(body, dict) or set(body) - set(RULE_BOUNDS):
        raise ValueError("use stop_days_after_pr and max_age_days only")
    db, workspace = gateway.db, principal.workspace_id
    with db._lock, db._tx() as conn:
        held = rules(conn, workspace)
        for key, (low, high) in RULE_BOUNDS.items():
            if key in body:
                value = body[key]
                if value is not None and (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or not low <= value <= high
                ):
                    raise ValueError(
                        f"{key} must be null or a whole number of days from {low} to {high}"
                    )
                held[key] = value
        conn.execute(
            "INSERT INTO preview_rules(workspace_id,stop_days_after_pr,max_age_days,updated_by,"
            "updated_at) VALUES (?,?,?,?,?) ON CONFLICT(workspace_id) DO UPDATE SET "
            "stop_days_after_pr=excluded.stop_days_after_pr,max_age_days=excluded.max_age_days,"
            "updated_by=excluded.updated_by,updated_at=excluded.updated_at",
            (
                workspace,
                held["stop_days_after_pr"],
                held["max_age_days"],
                principal.actor,
                store.stamp(store.clock()),
            ),
        )
        store.event(conn, workspace, "", 0, "rule_set", principal.actor, "")
        return rules(conn, workspace)


REFUSAL_STATUS = {
    "rate_limited": 429,
    "too_many_previews": 409,
    "changed_meanwhile": 409,
    "workspace_quota_full": 409,
    "validating": 409,
    "storage_unavailable": 503,
}


def refusal_payload(exc: Refused) -> dict:
    """What a caller is told when a bundle or request is refused: fixed text, the reason code
    and the next step, plus the stored failed preview when there is one. Never file content."""
    text, nxt = store.REASONS[exc.reason]
    out = {"error": text, "reason": exc.reason, "next": nxt}
    out.update({k: v for k, v in exc.detail.items() if v is not None})
    return out

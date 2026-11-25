"""Publish, list, stop and reopen previews, and the workspace stop rule.

The caller is always a ``Principal``; the workspace comes from it, never from the request.
A preview is reachable by the people who can open its card, so every call starts from the
card: an unknown id, another workspace's id and a card the caller cannot see all raise the
same ``NotFound``.
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
from datetime import timedelta

from commonplace.boards.service import BoardService
from commonplace.boards.store import require_visible, task_row
from commonplace.extensions import ToolContext, ToolError
from commonplace.http_util import log_failure

from . import bundle, store
from .bundle import Refused
from .store import Forbidden, NotFound

log = logging.getLogger("commonplace.previews")


def _remove_tree(path) -> None:
    """The one place files are removed; callers look at the result instead of trusting it, so
    no failure to remove escapes from here."""
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception as exc:
        log_failure(log, "preview files could not be removed", exc)


def _exists(path) -> bool:
    """Whether a path is still there; a path that cannot be looked at counts as there."""
    try:
        return path.exists()
    except OSError:
        return True


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
        return _facts(row)


def _facts(row) -> dict:
    """What previews need to know about a card: its number and its pull request as reported."""
    return {
        "number": row["id"],
        "pr_url": row["pr_url"] or "",
        "pr_head": row["pr_head"] or "",
        "pr_state": row["pr_state"] or "",
        "pr_reported_at": row["pr_reported_at"] or "",
    }


def _visible_cards(gateway, principal, numbers) -> dict:
    """``{number: facts}`` for the cards of ``numbers`` the caller can see."""
    service = _service(gateway, principal)
    seen = {}
    with service.store.connection() as conn:
        for number in numbers:
            try:
                row = _card_row(conn, principal, number)
            except NotFound:
                continue
            seen[number] = _facts(row)
    return seen


def _pr_cards(gateway, principal, pr_url) -> dict:
    service = _service(gateway, principal)
    with service.store.connection() as conn:
        numbers = [r[0] for r in conn.execute("SELECT id FROM tasks WHERE pr_url=?", (pr_url,))]
    return _visible_cards(gateway, principal, numbers)


def _name(raw) -> str:
    if not isinstance(raw, str):
        raise ValueError("name must be text of 1 to 80 characters")
    name = raw.strip()
    if not 1 <= len(name) <= store.MAX_NAME or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ValueError("name must be text of 1 to 80 characters")
    # The name is shown to everyone who can open the card: it must not carry a credential.
    if bundle.secret_kind(name):
        raise ValueError("name looks like it holds a credential; choose a plain name")
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
    # The file a refusal is about and the kind of credential, never the content.
    failure_file = str(exc.detail.get("file") or "")[:80]
    failure_kind = str(exc.detail.get("kind") or "")[:40]
    with db._lock, db._tx() as conn:
        row = store.row_by_name(conn, workspace, info["number"], name)
        if row is None:
            identity = store.new_id()
            conn.execute(
                "INSERT INTO previews(id,workspace_id,card,name,state,reason,pr_url,pr_head,actor,"
                "published_by,by_person,created_at,updated_at,failure_file,failure_kind) "
                "VALUES (?,?,?,?,'failed',?,?,?,?,?,?,?,?,?,?)",
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
                    failure_file,
                    failure_kind,
                ),
            )
            row = store.row_by_id(conn, workspace, identity)
        elif row["current_revision"] == 0 and row["state"] == "failed":
            # Only a refused attempt is refreshed; a publish that is still building is left alone.
            conn.execute(
                "UPDATE previews SET state='failed',reason=?,updated_at=?,failure_file=?,"
                "failure_kind=?,actor=?,published_by=?,by_person=? WHERE id=?",
                (
                    exc.reason,
                    now,
                    failure_file,
                    failure_kind,
                    principal.actor,
                    who,
                    by_person,
                    row["id"],
                ),
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
    text, _next = store.person_words(exc.reason)
    _history(gateway, principal, info["number"], f"Preview {name} was not published. {text}")
    return shown


def _same_or_refused(conn, principal, workspace, info, row, digest, now) -> tuple:
    """Before a publish takes a revision: ``(refusal, None)`` when a Coworker would bring back a
    preview a person stopped, or ``(None, view)`` when the files are the ones already kept (a
    ready preview is unchanged; a stopped one is served again without a copy)."""
    if (
        row is not None
        and not principal.person
        and row["state"] == "stopped"
        and row["reason"] == "stopped_by_person"
    ):
        # A person took it down: a Coworker does not bring it back by publishing again.
        return Refused("person_stopped"), None
    held = (
        conn.execute(
            "SELECT digest,deleted_at FROM preview_revisions WHERE preview_id=? AND revision=?",
            (row["id"], row["current_revision"]),
        ).fetchone()
        if row is not None and row["current_revision"]
        else None
    )
    # Files a person deleted are not "kept": the same files are a new revision, never a preview
    # served again over a folder that is gone.
    if (
        held is None
        or held["deleted_at"] is not None
        or held["digest"] != digest
        or row["state"] not in ("ready", "stopped")
    ):
        return None, None
    if row["state"] == "ready":
        return None, store.view(conn, row)
    if store.usage(conn, workspace)["ready"] >= store.MAX_READY:
        return Refused("workspace_quota_full"), None
    conn.execute(
        "UPDATE previews SET state='ready',reason='published',updated_at=? WHERE id=?",
        (now, row["id"]),
    )
    store.event(conn, workspace, row["id"], info["number"], "reopened", principal.actor)
    return None, store.view(conn, store.row_by_id(conn, workspace, row["id"]))


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
        refusal, same = _same_or_refused(conn, principal, workspace, info, row, digest, now)
        if same is not None:
            return same, False
        try:
            store.check_room(
                conn, workspace, size, needs_ready_slot=row is None or row["state"] != "ready"
            )
            if (row is None or not store.counts_live(row)) and store.live_rows(
                conn, workspace
            ) >= store.MAX_ROWS:
                raise Refused("too_many_previews")
        except Refused as exc:
            refusal = refusal or exc
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
                    "UPDATE previews SET state=?,reason=?,updated_at=?,failure_file='',"
                    "failure_kind='' WHERE id=?",
                    (state, why, now, identity),
                )
            else:
                # A storage or room failure is not about one file: no stale file or kind stays.
                conn.execute(
                    "UPDATE previews SET state='failed',reason=?,updated_at=?,failure_file='',"
                    "failure_kind='' WHERE id=?",
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
                    "pr_url=?,pr_head=?,actor=?,published_by=?,by_person=?,updated_at=?,"
                    "failure_file='',failure_kind='' WHERE id=?",
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
        f"Published preview {name} (version {shown['revision']}). Open it from this card, "
        "under Previews.",
    )
    return shown, True


def _leftovers(gateway, workspace, preview_id) -> list:
    """Folders a delete renamed aside and could not remove yet (read with no lock held)."""
    return sorted(store.root(gateway, workspace).glob(f".deleting-{preview_id}-*"))


def _leftover_ids(gateway, workspace) -> set:
    """The previews with such folders, from one look at the folder (``.deleting-<id>-<random>``)."""
    found = set()
    for path in store.root(gateway, workspace).glob(".deleting-*"):
        parts = path.name.split("-")
        if len(parts) == 3:
            found.add(parts[1])
    return found


def _mark_leftovers(shown, leftover_ids) -> dict:
    """A deleted preview whose removal left files says so, so the owner can delete again."""
    if shown["reason"] == "files_deleted":
        shown["files_remaining"] = shown["id"] in leftover_ids
        if shown["files_remaining"]:
            shown["reason_text"] = "The preview is stopped, but some files could not be removed."
            shown["next"] = "The workspace owner can use Finish deleting on this card to try again."
    return shown


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


def stop_due(row, card, rule) -> tuple:
    """``(stops_at, reason)`` for a ready preview under the workspace's stop rule, or
    ``(None, None)``. A preview counts from when it last became ready (published or opened again),
    so opening an expired preview again gives it the rule's full time again. The pull request
    part counts from when the card last reported it merged or closed."""
    if row["state"] != "ready" or rule is None:
        return None, None
    since = store.parse_stamp(row["updated_at"])
    due = []
    if rule.get("max_age_days"):
        due.append((since + timedelta(days=rule["max_age_days"]), "expired"))
    state = (card or {}).get("pr_state")
    reported = (card or {}).get("pr_reported_at")
    if rule.get("stop_days_after_pr") and state in ("merged", "closed") and reported:
        try:
            closed = store.parse_stamp(reported)
        except ValueError:
            closed = None
        if closed is not None:
            anchor = max(closed, since)
            due.append((anchor + timedelta(days=rule["stop_days_after_pr"]), f"pr_{state}"))
    if not due:
        return None, None
    at, why = min(due)
    return store.stamp(at), why


def _settle_expired(gateway, workspace, rows, cards, rule) -> set:
    """Stop the ready previews whose time under the rule has passed, once, when they are read
    or requested; nothing runs on a timer and the files stay. Returns the ids it stopped."""
    now = store.stamp(store.clock())
    due = []
    for row in rows:
        at, why = stop_due(row, cards.get(row["card"]), rule)
        if at is not None and at <= now:
            due.append((row, why))
    if not due:
        return set()
    db = gateway.db
    stopped = set()
    with db._lock, db._tx() as conn:
        for row, why in due:
            # Only the row as it was read: a preview opened again meanwhile keeps its new time.
            changed = conn.execute(
                "UPDATE previews SET state='stopped',reason=?,updated_at=? "
                "WHERE id=? AND state='ready' AND updated_at=?",
                (why, now, row["id"], row["updated_at"]),
            ).rowcount
            if changed:
                store.event(conn, workspace, row["id"], row["card"], "expired", "rule", why)
                stopped.add(row["id"])
    return stopped


def _rule_now(gateway, workspace):
    with gateway.db._lock:
        return rules(gateway.db._conn, workspace)


def get(gateway, principal, preview_id) -> dict:
    shown, _folder = open_preview(gateway, principal, preview_id, revisions=True)
    return shown


def open_preview(gateway, principal, preview_id, *, revisions=False) -> tuple:
    """``(view, folder of the current revision or None)`` for a preview the caller can open,
    with the stop rule applied first; ``NotFound`` otherwise."""
    db, workspace = gateway.db, principal.workspace_id
    with db._lock:
        row = store.row_by_id(db._conn, workspace, preview_id)
    if row is None:
        raise NotFound(str(preview_id))
    card = card_info(gateway, principal, row["card"])
    rule = _rule_now(gateway, workspace)
    if _settle_expired(gateway, workspace, [row], {row["card"]: card}, rule):
        with db._lock:
            row = store.row_by_id(db._conn, workspace, preview_id)
    with db._lock:
        current, history = store.snapshot(db._conn, row, revisions=revisions)
    stops_at, _why = stop_due(row, card, rule)
    shown = store.shape(row, current, history, stops_at)
    if shown["reason"] == "files_deleted":
        shown = _mark_leftovers(
            shown, {row["id"]} if _leftovers(gateway, workspace, row["id"]) else set()
        )
    folder = (
        store.revision_dir(gateway, workspace, row["id"], row["current_revision"])
        if current is not None
        else None
    )
    return shown, folder


def listing(gateway, principal, *, card=None, pr=None) -> dict:
    """Previews the caller may see, newest first, with the workspace's usage and limits."""
    db, workspace = gateway.db, principal.workspace_id
    if card is not None:
        info = card_info(gateway, principal, card)
        cards = {info["number"]: info}
    elif pr is not None:
        cards = _pr_cards(gateway, principal, pr)
    else:
        cards = None
    with db._lock:
        rows = db._conn.execute(
            "SELECT * FROM previews WHERE workspace_id=? ORDER BY updated_at DESC, id DESC",
            (workspace,),
        ).fetchall()
    if cards is None:
        cards = _visible_cards(gateway, principal, sorted({r["card"] for r in rows}))
    rows = [r for r in rows if r["card"] in cards]
    rule = _rule_now(gateway, workspace)
    if _settle_expired(gateway, workspace, rows, cards, rule):
        with db._lock:
            rows = [store.row_by_id(db._conn, workspace, r["id"]) for r in rows]
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
    shapes = [
        store.shape(r, current.get(r["id"]), None, stop_due(r, cards.get(r["card"]), rule)[0])
        for r in rows
    ]
    # One look at the folder for the whole list, and only when a deleted preview is in it.
    leftover_ids = (
        _leftover_ids(gateway, workspace)
        if any(p["reason"] == "files_deleted" for p in shapes)
        else set()
    )
    shown = [_mark_leftovers(p, leftover_ids) for p in shapes]
    return {
        "previews": shown,
        "usage": held,
        "limits": store.LIMITS,
        "rule": rule,
        # Whether this caller may change the rule; the PUT route checks it again.
        "can_set_rule": bool(principal.person and principal.role == "owner"),
        # Whether this caller may delete files; the DELETE route checks it again.
        "can_delete": bool(principal.person and principal.role == "owner"),
    }


def _change(gateway, principal, preview_id, *, kind, to_state, reason_for, allowed_from):
    _authorize(gateway, principal, preview_id)
    db, workspace = gateway.db, principal.workspace_id
    with db._lock, db._tx() as conn:
        row = _held(conn, principal, preview_id)
        if not principal.person and principal.actor != row["actor"]:
            raise Forbidden("not the publisher")
        if row["state"] not in allowed_from:
            return store.view(conn, row)
        if kind == "reopened" and not principal.person and row["reason"] == "stopped_by_person":
            # A person took it down: only a person serves it again, whatever the caller's routes.
            raise Refused("person_stopped")
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
        shown = store.view(conn, store.row_by_id(conn, workspace, row["id"]))
    words = {"stopped": "Stopped", "reopened": "Served again"}.get(kind, kind)
    _history(gateway, principal, row["card"], f"{words}: preview {row['name']}.")
    return shown


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
    """Serve a stopped preview again from its kept files; ``Refused`` when they were deleted."""
    _authorize(gateway, principal, preview_id)
    db = gateway.db
    with db._lock:
        row = _held(db._conn, principal, preview_id)
    if row["state"] == "stopped" and row["reason"] == "files_deleted":
        raise Refused("files_deleted")
    return _change(
        gateway,
        principal,
        preview_id,
        kind="reopened",
        to_state="ready",
        reason_for=lambda row, p: "published",
        allowed_from=("stopped",),
    )


def _mark_deleted(conn, workspace, row, actor) -> None:
    """The rows of a delete: every revision's files gone, the preview stopped, one event."""
    now = store.stamp(store.clock())
    conn.execute(
        "UPDATE preview_revisions SET deleted_at=? WHERE preview_id=? AND deleted_at IS NULL",
        (now, row["id"]),
    )
    conn.execute(
        "UPDATE previews SET state='stopped',reason='files_deleted',updated_at=? WHERE id=?",
        (now, row["id"]),
    )
    store.event(conn, workspace, row["id"], row["card"], "files_deleted", actor)


def _finish_unrestorable(db, workspace, row, actor) -> None:
    """The folder could not be renamed back after a failed delete, so the rows must not read
    ready over a folder that is no longer there. Record the delete instead: the preview reads
    deleted with files remaining, and the owner's Finish deleting removes the aside folder."""
    try:
        with db._lock, db._tx() as conn:
            _mark_deleted(conn, workspace, row, actor)
            store.event(conn, workspace, row["id"], row["card"], "delete_incomplete", actor)
    except Exception as exc:
        log_failure(log, "preview delete could not be recorded after a failed restore", exc)


def delete_files(gateway, principal, preview_id, confirm) -> dict:
    """Delete every revision's files of a preview. A signed-in workspace owner only, with the
    preview's own id repeated as confirmation; never called by a timer or a Coworker.

    The order (see docs/system/previews.md, "Deleting files"): under the control-database lock
    the rows are marked deleted and the preview's folder is renamed aside in one step, which is
    cheap and atomic; the lock is then released and the renamed folder is removed. A publish
    racing the delete finds its preview stopped in its final transaction and refuses itself
    (``changed_meanwhile``), and its half-written files went aside with the folder, so a deleted
    preview never reads ready. A removal that leaves files behind is recorded in a short
    follow-up transaction and said in the answer; asking again removes what is left."""
    if not principal.person or principal.role != "owner":
        raise Forbidden("owner")
    _authorize(gateway, principal, preview_id)
    db, workspace = gateway.db, principal.workspace_id
    base = store.root(gateway, workspace)
    aside = folder = None
    with db._lock:
        try:
            with db._tx() as conn:
                row = _held(conn, principal, preview_id)
                if confirm != row["id"]:
                    raise ValueError("confirm must repeat the preview id")
                state, _reason = store.effective(row)
                if state == "building":
                    raise Refused("validating")
                _mark_deleted(conn, workspace, row, principal.actor)
                folder = base / row["id"]
                if folder.exists():
                    target = base / f".deleting-{row['id']}-{secrets.token_hex(4)}"
                    # A rename only: if the disk refuses it, the transaction rolls back.
                    try:
                        os.replace(folder, target)
                    except OSError:
                        raise Refused("storage_unavailable") from None
                    aside = target
                shown = store.view(
                    conn, store.row_by_id(conn, workspace, row["id"]), revisions=True
                )
        except BaseException:
            # The rows rolled back (a refused or failed commit included), so the files must be
            # where the rows say they are: rename the folder back, still under the lock, so no
            # publish can make a new folder in between.
            if aside is not None:
                try:
                    os.replace(aside, folder)
                except OSError as exc:
                    log_failure(
                        log, "preview folder could not be put back after a failed delete", exc
                    )
                    _finish_unrestorable(db, workspace, row, principal.actor)
            raise
    # The lock is released: the removal itself may take as long as the files need.
    leftovers = _leftovers(gateway, workspace, row["id"])
    for path in leftovers:
        _remove_tree(path)
    if any(_exists(path) for path in leftovers):
        log.warning("preview files could not be fully removed for %s", row["id"])
        try:
            with db._lock, db._tx() as conn:
                store.event(
                    conn, workspace, row["id"], row["card"], "delete_incomplete", principal.actor
                )
        except Exception as exc:
            log_failure(log, "a delete_incomplete event could not be recorded", exc)
        shown["files_remaining"] = True
    else:
        shown["files_remaining"] = False
    return _mark_leftovers(shown, {shown["id"]} if shown["files_remaining"] else set())


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
            f"{held['stop_days_after_pr']} days after the card records its pull request as "
            "merged or closed"
        )
    if held["max_age_days"]:
        parts.append(f"{held['max_age_days']} days after it was last made live")
    if not parts:
        return "Previews are served until a person or the Coworker that published them stops them."
    when = " and ".join(parts) + (", whichever comes first" if len(parts) == 2 else "")
    return f"A preview stops being served {when}. Its files are kept."


def get_rules(gateway, principal) -> dict:
    with gateway.db._lock:
        return rules(gateway.db._conn, principal.workspace_id)


def set_rules(gateway, principal, body) -> dict:
    """Owner only. ``None`` switches a part of the rule off. The rule is applied when a
    preview is next read or requested (``stop_due``); nothing runs on a timer."""
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
    "workspace_space_full": 409,
    "validating": 409,
    "storage_unavailable": 503,
    "files_deleted": 409,
    "person_stopped": 409,
}


def refusal_payload(exc: Refused) -> dict:
    """What a caller is told when a bundle or request is refused: fixed text, the reason code
    and the next step, plus the stored failed preview when there is one. Never file content."""
    text, nxt = store.REASONS[exc.reason]
    out = {"error": text, "reason": exc.reason, "next": nxt}
    out.update({k: v for k, v in exc.detail.items() if v is not None})
    return out

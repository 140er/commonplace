"""Topology proposals: agents write facts, the owner approves changes in the app.

A caller hands in node, link and route items (and subjects to take off the map) with
evidence. What happens next depends on who it is and what would change:

- a runner's additions about itself apply at once (and a repeat refreshes them);
- the signed-in owner's own write applies at once, as the authoritative value;
- everything else that would add, change or remove a fact becomes one proposal. It
  opens one decision for the owner through the ordinary ask path, with the map before
  and after side by side. Approve applies it, Reject keeps the old facts.

Every applied change is kept with its before and after values, so the owner can undo a
wrong discovery. The rows live in ``topology.sqlite3`` next to the facts
(``topology_proposals``). The contract is in ``docs/topology.md``.
"""

from __future__ import annotations

import json
import secrets
import threading
from contextlib import contextmanager
from datetime import timedelta

from commonplace import sessions
from commonplace.extensions import ToolContext, ToolError

from . import model, store
from .model import TOPOLOGY_WING, Observer, TopologyError

APPROVE, REJECT = "Approve", "Reject"
PERSON_REASON = "Only the workspace owner approves a change to the map."
STATES = ("pending", "applying", "applied", "rejected", "void", "undone")
ORIGINS = ("proposal", "self_report", "owner")
IDENTITY = {"node": "kind", "link": "method", "route": "hops"}
MAX_ITEMS = 32
LIST_LIMIT = 100
# A proposal row whose decision card never opened (a crash in between) is void after this.
OPENING_SECONDS = 600

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS topology_proposals (
        id TEXT PRIMARY KEY,
        state TEXT NOT NULL CHECK(state IN
            ('pending','applying','applied','rejected','void','undone')),
        origin TEXT NOT NULL CHECK(origin IN ('proposal','self_report','owner')),
        observer_kind TEXT NOT NULL, observer_id TEXT NOT NULL,
        proposed_by TEXT NOT NULL,
        evidence TEXT NOT NULL, changes TEXT NOT NULL, ttl_seconds INTEGER,
        card INTEGER, card_event INTEGER,
        decided_by TEXT, decided_at TEXT, note TEXT,
        undone_by TEXT, undone_at TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS topology_proposals_state ON topology_proposals(state)",
    "CREATE INDEX IF NOT EXISTS topology_proposals_card ON topology_proposals(card)",
)
_settling = threading.Lock()


@contextmanager
def _writing(topo):
    with topo._write() as conn:
        for statement in _SCHEMA:
            conn.execute(statement)
        yield conn


@contextmanager
def _reading(topo):
    """Yields ``None`` for a workspace that never held a proposal; nothing is created."""
    with topo._read() as conn:
        known = (
            conn is not None
            and conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='topology_proposals'"
            ).fetchone()
        )
        yield conn if known else None


def _now() -> str:
    return model.stamp(model.clock(None))


def _same(left, right) -> bool:
    if left is None or right is None:
        return left is right
    return model.encode(left) == model.encode(right)


# ── Who is asking ─────────────────────────────────────────────────────────
def _machine(ctx):
    """The active runner machine this connection belongs to, or ``None``."""
    from commonplace.runner import registry

    p = ctx.principal
    return next(
        (
            m
            for m in registry.machines(ctx.db, p.workspace_id)
            if p.connection_id in (m["worker_connection_id"], m["machine_connection_id"])
            and m["enabled"]
            and not m["revoked_at"]
        ),
        None,
    )


def _caller(ctx):
    """``(observer, runner machine id or None)`` for a caller that may write the wing."""
    p = ctx.principal
    if not p.policy.can_write_wing(TOPOLOGY_WING):
        raise ToolError("no: this connection cannot write the topology wing")
    if p.person:
        if p.role != "owner":
            raise ToolError(
                "no: only the workspace owner or an agent connection writes topology facts"
            )
        return Observer("owner", p.user_id), None
    if p.runner is not None:
        machine = _machine(ctx)
        if machine is None:
            raise ToolError("no: this runner is not active")
        return Observer("runner", machine["id"]), machine["id"]
    return Observer("agent", p.connection_id), None


# ── The map now, and what a request would change ──────────────────────────
def _current(topo) -> dict:
    """Every current value by subject and field, stale ones included."""
    graph = store.read_graph(topo)
    return {
        record["subject"]: {name: field["value"] for name, field in record["fields"].items()}
        for kind in ("nodes", "links", "routes")
        for record in graph[kind]
    }


def _subject(value) -> str:
    if isinstance(value, str):
        kind, _, rest = value.partition(":")
        if kind == "node":
            return model.node_subject(rest)
        if kind == "route":
            return model.route_subject(rest)
        if kind == "link":
            return model.link_subject(*model.parse_link_id(rest))
    raise TopologyError("no: subject must be node:<key>, link:<id> or route:<site>")


def _kind(subject) -> str:
    return subject.partition(":")[0]


def _removals(remove) -> list:
    """``[(subject, field or None)]`` from subjects or ``{subject, field}`` objects."""
    if remove is None:
        return []
    if not isinstance(remove, (list, tuple)) or not 1 <= len(remove) <= MAX_ITEMS:
        raise TopologyError(f"no: remove must list 1 to {MAX_ITEMS} subjects")
    found = []
    for entry in remove:
        if isinstance(entry, str):
            entry = {"subject": entry}
        if (
            not isinstance(entry, dict)
            or "subject" not in entry
            or set(entry)
            - {
                "subject",
                "field",
            }
        ):
            raise TopologyError("no: a removal is a subject or {subject, field}")
        subject, field = _subject(entry["subject"]), entry.get("field")
        if field is not None:
            allowed = {
                "node": tuple(model.NODE_FIELDS),
                "link": tuple(model.LINK_FIELDS),
                "route": model.ROUTE_FIELDS,
            }[_kind(subject)]
            model.check_field_name(field, allowed)
            if field == IDENTITY[_kind(subject)]:
                raise TopologyError("no: remove the whole subject, not its identity field")
        found.append((subject, field))
    return found


def _own_nodes(facts, current, machine_id) -> set:
    """Node subjects that are this runner: bound to it, or new and declared as it."""
    own = {subject for subject, fields in current.items() if fields.get("runner_id") == machine_id}
    own |= {
        subject
        for subject, predicate, value in facts
        if predicate == "runner_id" and value == machine_id and subject not in current
    }
    return own


def _about_itself(subject, own) -> bool:
    if _kind(subject) == "node":
        return subject in own
    if _kind(subject) == "link":
        source, target, _ = model.parse_link_id(subject.partition(":")[2])
        return "node:" + source in own or "node:" + target in own
    return False


def _plan(items, remove, current, machine_id, owner):
    """Split a request into ``(direct changes, refreshed facts, proposed changes, unchanged)``.

    A change is ``{subject, predicate, before, after}``; ``None`` means no value.
    """
    if items is not None and (not isinstance(items, (list, tuple)) or len(items) > MAX_ITEMS):
        raise TopologyError(f"no: items must list 1 to {MAX_ITEMS} entries")
    facts = model.facts_for(items) if items is not None else []
    removals = _removals(remove)
    if not facts and not removals:
        raise TopologyError("no: name items to write or subjects to remove")
    written = {(subject, predicate) for subject, predicate, _ in facts}
    if len(written) != len(facts):
        raise TopologyError("no: the same field is written twice")
    for subject in {subject for subject, _ in written if _kind(subject) == "node"}:
        if "kind" not in current.get(subject, {}) and (subject, "kind") not in written:
            raise TopologyError("no: the first write for a node needs its kind")
    own = _own_nodes(facts, current, machine_id) if machine_id else set()
    direct, refreshed, proposed, unchanged = [], [], [], 0
    for subject, predicate, value in facts:
        before = current.get(subject, {}).get(predicate)
        change = {"subject": subject, "predicate": predicate, "before": before, "after": value}
        if _same(before, value):
            if machine_id and _about_itself(subject, own):
                refreshed.append((subject, predicate, value))
            unchanged += 1
        elif owner or (before is None and machine_id and _about_itself(subject, own)):
            direct.append(change)
        else:
            proposed.append(change)
    removed = set()
    for subject, field in removals:
        fields = current.get(subject, {})
        names = [field] if field else sorted(fields)
        if not fields or (field and field not in fields):
            raise TopologyError("no: nothing to remove: that subject or field is not on the map")
        if subject in removed or any(name == subject for name, _ in written):
            raise TopologyError("no: a subject is written and removed, or removed twice")
        removed.add(subject)
        for name in names:
            change = {
                "subject": subject,
                "predicate": name,
                "before": fields[name],
                "after": None,
            }
            (direct if owner else proposed).append(change)
    return _ordered(direct), refreshed, _ordered(proposed), unchanged


def _ordered(changes) -> list:
    """Subjects in the order they were named, each with its identity field first."""
    first = {}
    for change in changes:
        first.setdefault(change["subject"], len(first))
    return sorted(
        changes,
        key=lambda c: (
            first[c["subject"]],
            c["predicate"] != IDENTITY[_kind(c["subject"])],
            c["predicate"],
        ),
    )


def _items(facts) -> list:
    """Write items for ``store.upsert`` from ``(subject, predicate, value)`` facts."""
    grouped = {}
    for subject, predicate, value in facts:
        grouped.setdefault(subject, {})[predicate] = value
    items = []
    for subject, fields in grouped.items():
        kind, _, rest = subject.partition(":")
        if kind == "node":
            items.append({"node": rest, "fields": fields})
        elif kind == "link":
            items.append({"link": list(model.parse_link_id(rest)), "fields": fields})
        else:
            items.append({"route": rest, "hops": fields["hops"]})
    return items


def _retractions(pairs) -> list:
    """``[(subject, field or None)]``: a subject losing its identity goes as a whole."""
    grouped = {}
    for subject, predicate in pairs:
        grouped.setdefault(subject, []).append(predicate)
    found = []
    for subject, predicates in grouped.items():
        if IDENTITY[_kind(subject)] in predicates:
            found.append((subject, None))
        else:
            found.extend((subject, predicate) for predicate in predicates)
    return found


# ── The decision the owner reads ──────────────────────────────────────────
def _shown(value, missing) -> str:
    if value is None:
        return missing
    if isinstance(value, list):
        text = ", ".join(str(entry) for entry in value) or "(empty)"
    else:
        text = value if isinstance(value, str) else model.encode(value)
    return "".join(char if char.isprintable() else "?" for char in text)


def _preview(changes, side) -> str:
    lines, subject = ["Before:" if side == "before" else "After:"], None
    for change in changes:
        if change["subject"] != subject:
            subject = change["subject"]
            lines.append(subject.replace(":", " ", 1))
        missing = "(not set)" if side == "before" else "(removed)"
        lines.append(f"  {change['predicate']}: {_shown(change[side], missing)}")
    return "\n".join(lines)


def _summary(changes) -> str:
    verbs = {}
    for change in changes:
        verb = (
            "add" if change["before"] is None else "remove" if change["after"] is None else "change"
        )
        verbs.setdefault(change["subject"], set()).add(verb)
    text = "; ".join(
        f"{next(iter(found)) if len(found) == 1 else 'change'} {subject.replace(':', ' ', 1)}"
        for subject, found in verbs.items()
    )
    return text if len(text) <= 240 else text[:239] + "…"


def _decision(changes) -> list:
    """The two structured options, or a refusal when the change does not fit one decision."""
    from commonplace.boards import decision_blocks

    before, after = _preview(changes, "before"), _preview(changes, "after")
    for text in (before, after):
        if (
            len(text) > decision_blocks.PREVIEW_LIMIT
            or len(text.splitlines()) > decision_blocks.PREVIEW_LINES
        ):
            raise ToolError(
                "no: this is too much for the owner to read as one decision; "
                "propose it in smaller parts"
            )
    count = f"{len(changes)} fact" + ("" if len(changes) == 1 else "s")
    return [
        {
            "label": APPROVE,
            "detail": f"Change {count} on the map. The old values stay in history.",
            "preview": after,
        },
        {"label": REJECT, "detail": "Keep the map as it is.", "preview": before},
    ]


# ── Rows ──────────────────────────────────────────────────────────────────
def _insert(topo, *, state, origin, observer, actor, evidence, changes, ttl_seconds) -> str:
    proposal_id, stamp = "tp_" + secrets.token_hex(8), _now()
    with _writing(topo) as conn:
        conn.execute(
            "INSERT INTO topology_proposals (id,state,origin,observer_kind,observer_id,"
            "proposed_by,evidence,changes,ttl_seconds,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                proposal_id,
                state,
                origin,
                observer.kind,
                observer.id,
                actor,
                evidence,
                model.encode(changes),
                ttl_seconds,
                stamp,
                stamp,
            ),
        )
    return proposal_id


def _update(topo, proposal_id, *, expect=None, **values) -> bool:
    """Set columns; with ``expect`` only while the row is still in that state."""
    values["updated_at"] = _now()
    with _writing(topo) as conn:
        cursor = conn.execute(
            f"UPDATE topology_proposals SET {', '.join(key + '=?' for key in values)} WHERE id=?"
            + (" AND state=?" if expect else ""),
            (*values.values(), proposal_id, *((expect,) if expect else ())),
        )
        return cursor.rowcount == 1


def _row(topo, proposal_id):
    with _reading(topo) as conn:
        if conn is None or not isinstance(proposal_id, str):
            return None
        return conn.execute(
            "SELECT * FROM topology_proposals WHERE id=?", (proposal_id,)
        ).fetchone()


def _public(row) -> dict:
    return {
        "id": row["id"],
        "state": row["state"],
        "origin": row["origin"],
        "wing": TOPOLOGY_WING,
        "observer": {"kind": row["observer_kind"], "id": row["observer_id"]},
        "proposed_by": row["proposed_by"],
        "evidence": row["evidence"],
        "changes": json.loads(row["changes"]),
        "card": row["card"],
        "created_at": row["created_at"],
        "decided_by": row["decided_by"],
        "decided_at": row["decided_at"],
        "note": row["note"],
        "undone_by": row["undone_by"],
        "undone_at": row["undone_at"],
    }


def _waiting(topo, observer, changes):
    """This observer's open proposal of exactly these changes, so a rerun asks only once."""
    with _reading(topo) as conn:
        if conn is None:
            return None
        return conn.execute(
            "SELECT * FROM topology_proposals WHERE state='pending' AND card IS NOT NULL "
            "AND observer_kind=? AND observer_id=? AND changes=? ORDER BY created_at LIMIT 1",
            (observer.kind, observer.id, model.encode(changes)),
        ).fetchone()


def _apply(topo, row) -> None:
    """Write a row's changes to the store. Safe to repeat after a crash in the middle."""
    changes = json.loads(row["changes"])
    write = {
        "observer": Observer(row["observer_kind"], row["observer_id"]),
        "evidence": row["evidence"],
    }
    facts = [(c["subject"], c["predicate"], c["after"]) for c in changes if c["after"] is not None]
    if facts:
        store.upsert(
            topo,
            _items(facts),
            ttl_seconds=row["ttl_seconds"],
            # An owner's decision is authoritative; a self-report is one more observation.
            replace=row["origin"] != "self_report",
            **write,
        )
    removed = [(c["subject"], c["predicate"]) for c in changes if c["after"] is None]
    for subject, predicate in _retractions(removed):
        store.retract(topo, subject, predicate=predicate, **write)


def _still_true(changes, current) -> bool:
    """Is the map still what the proposal showed as "before"?"""
    for change in changes:
        if not _same(current.get(change["subject"], {}).get(change["predicate"]), change["before"]):
            return False
    removed = {c["subject"] for c in changes if c["after"] is None}
    for subject in removed:
        named = {c["predicate"] for c in changes if c["subject"] == subject}
        # Taking a whole subject off the map must not sweep up fields nobody was shown.
        if IDENTITY[_kind(subject)] in named and set(current.get(subject, {})) != named:
            return False
    return True


# ── Propose ───────────────────────────────────────────────────────────────
def propose(ctx, items=None, remove=None, evidence=None, ttl_seconds=None, board=None) -> dict:
    """Write what may be written at once and open one owner decision for the rest.

    Returns ``{"applied", "proposal", "unchanged"}``: the applied change and the pending
    proposal (each ``None`` or its public row) and how many facts already had the value.
    Refusals raise ``TopologyError`` or ``ToolError`` before anything is stored.
    """
    observer, machine_id = _caller(ctx)
    principal = ctx.principal
    evidence = model.evidence_text(evidence)
    if ttl_seconds is not None:
        model.expiry("kind", model.clock(None), ttl_seconds)
    settle(ctx.gateway, principal.workspace_id)
    topo = store.open_topology(ctx.gateway, principal.workspace_id)
    owner = observer.kind == "owner"
    direct, refreshed, proposed, unchanged = _plan(items, remove, _current(topo), machine_id, owner)
    options = _decision(proposed) if proposed else None
    result = {"applied": None, "proposal": None, "unchanged": unchanged}
    common = {
        "observer": observer,
        "actor": principal.actor,
        "evidence": evidence,
        "ttl_seconds": ttl_seconds,
    }
    if proposed:
        # Asked first: when no decision can be opened, nothing at all is written.
        waiting = _waiting(topo, observer, proposed)
        if waiting is None:
            pending = _insert(topo, state="pending", origin="proposal", changes=proposed, **common)
            try:
                card, last_event = _open_decision(
                    ctx, pending, observer, evidence, proposed, options, board
                )
            except BaseException:
                _update(topo, pending, state="void", note="the decision could not be opened")
                raise
            _update(topo, pending, card=card, card_event=last_event)
            waiting = _row(topo, pending)
        result["proposal"] = _public(waiting)
    if direct:
        applied = _insert(
            topo,
            state="applying",
            origin="owner" if owner else "self_report",
            changes=direct,
            **common,
        )
        _apply(topo, _row(topo, applied))
        _update(topo, applied, state="applied", decided_by=principal.actor, decided_at=_now())
        result["applied"] = _public(_row(topo, applied))
    if refreshed:
        store.upsert(
            topo, _items(refreshed), observer=observer, evidence=evidence, ttl_seconds=ttl_seconds
        )
    return result


def _open_decision(ctx, proposal_id, observer, evidence, changes, options, board):
    """Open the owner's decision card; returns its number and its last event id."""
    from commonplace.boards.service import BoardService

    service = BoardService(ctx)
    card = service.ask(
        "Apply this change to the map? " + _summary(changes),
        options=options,
        board=board,
        context=f"Topology proposal {proposal_id} from {observer.kind} {observer.id}.\n\n"
        f"Evidence:\n{evidence}",
        person_reason=PERSON_REASON,
    )
    with service.store.connection() as conn:
        row = conn.execute(
            "SELECT version, (SELECT max(id) FROM task_events WHERE task_id=tasks.id) AS last "
            "FROM tasks WHERE id=?",
            (card["number"],),
        ).fetchone()
    if row is None or row["version"] != card["version"]:
        # Someone touched the card before it was bound: the owner may not see this ask.
        raise ToolError("no: the decision changed while it was opened; propose again")
    return card["number"], row["last"] or 0


# ── Settle: bring proposals in line with their decisions ──────────────────
def _owner_service(gateway, workspace_id):
    from commonplace.boards.service import BoardService

    for member in gateway.db.members(workspace_id):
        if member["role"] == "owner":
            person = sessions.person_principal(gateway.db, member["id"], workspace_id)
            if person is not None:
                return BoardService(ToolContext(principal=person, gateway=gateway))
    return None


def _answer(gateway, workspace_id, row):
    """``(verdict, decided_by, note)`` from the card's history, or ``None`` while it is open.

    Only the owner's answer to the ask exactly as it was opened counts: any other event
    on the card first (an edit, a withdrawal, a new ask) voids the proposal.
    """
    service = _owner_service(gateway, workspace_id)
    if service is None:
        return None
    with service.store.connection() as conn:
        events = conn.execute(
            "SELECT kind, choice, stamp_user FROM task_events "
            "WHERE task_id=? AND id>? AND kind!='comment' ORDER BY id LIMIT 1",
            (row["card"], row["card_event"]),
        ).fetchall()
    if not events:
        return None
    event = events[0]
    user = (event["stamp_user"] or "").removeprefix("user:")
    if event["kind"] != "decide" or gateway.db.role(workspace_id, user) != "owner":
        return "void", None, "the decision was changed before the owner answered it"
    if event["choice"] == APPROVE:
        return "approve", f"user:{user}", None
    note = None if event["choice"] == REJECT else "answered without choosing Approve"
    return "reject", f"user:{user}", note


def _settle(gateway, workspace_id, topo, row) -> None:
    if row["state"] == "applying":
        _apply(topo, row)
        _update(topo, row["id"], expect="applying", state="applied")
        return
    if row["card"] is None:
        opened = model.instant(row["created_at"]) + timedelta(seconds=OPENING_SECONDS)
        if opened <= model.clock(None):
            _update(topo, row["id"], expect="pending", state="void", note="no decision was opened")
        return
    answer = _answer(gateway, workspace_id, row)
    if answer is None:
        return
    verdict, decided_by, note = answer
    decided = {"decided_by": decided_by, "decided_at": _now(), "note": note}
    if verdict == "approve" and not _still_true(json.loads(row["changes"]), _current(topo)):
        verdict = "void"
        decided["note"] = "the map changed after this was proposed; propose it again"
    if verdict != "approve":
        state = "rejected" if verdict == "reject" else "void"
        _update(topo, row["id"], expect="pending", state=state, **decided)
        return
    if _update(topo, row["id"], expect="pending", state="applying", **decided):
        _apply(topo, row)
        _update(topo, row["id"], expect="applying", state="applied")


def settle(gateway, workspace_id, *, card=None) -> None:
    """Apply or close every proposal whose decision is answered. Safe to call any time."""
    topo = store.open_topology(gateway, workspace_id)
    with _settling:
        with _reading(topo) as conn:
            if conn is None:
                return
            rows = conn.execute(
                "SELECT * FROM topology_proposals WHERE state IN ('pending','applying')"
                + (" AND card=?" if card is not None else "")
                + " ORDER BY created_at, id",
                ((card,) if card is not None else ()),
            ).fetchall()
        for row in rows:
            _settle(gateway, workspace_id, topo, row)


# ── Read and undo ─────────────────────────────────────────────────────────
def _readable(ctx):
    if not ctx.principal.policy.can_read_wing(TOPOLOGY_WING):
        raise ToolError("no: this connection cannot read the topology wing")
    workspace_id = ctx.principal.workspace_id
    settle(ctx.gateway, workspace_id)
    return store.open_topology(ctx.gateway, workspace_id)


def get(ctx, proposal_id) -> dict:
    row = _row(_readable(ctx), proposal_id)
    if row is None:
        raise ToolError("no: proposal not found")
    return _public(row)


def listing(ctx, state=None) -> list:
    """Proposals and applied changes, newest first."""
    if state is not None and state not in STATES:
        raise ToolError(f"no: state must be one of {', '.join(STATES)}")
    with _reading(_readable(ctx)) as conn:
        if conn is None:
            return []
        rows = conn.execute(
            "SELECT * FROM topology_proposals"
            + (" WHERE state=?" if state else "")
            + " ORDER BY created_at DESC, rowid DESC LIMIT ?",
            ((state,) if state else ()) + (LIST_LIMIT,),
        ).fetchall()
    return [_public(row) for row in rows]


def undo(ctx, proposal_id) -> dict:
    """Put back what an applied change replaced. The signed-in workspace owner only.

    A fact that changed again since is left alone and listed under ``skipped``. Both the
    undone values and the restored ones stay in the store's history.
    """
    principal = ctx.principal
    if not principal.person or principal.role != "owner":
        raise ToolError("no: only the signed-in workspace owner can undo a topology change")
    topo = _readable(ctx)
    row = _row(topo, proposal_id)
    if row is None:
        raise ToolError("no: proposal not found")
    if row["state"] != "applied":
        raise ToolError(f"no: this change is {row['state']}, not applied")
    current = _current(topo)
    changes = json.loads(row["changes"])
    live = [
        c for c in changes if _same(current.get(c["subject"], {}).get(c["predicate"]), c["after"])
    ]
    if not live:
        raise ToolError("no: nothing left to undo; the map changed since")
    write = {
        "observer": Observer("owner", principal.user_id),
        "evidence": f"Undo of topology change {row['id']}",
    }
    restored = [
        (c["subject"], c["predicate"], c["before"]) for c in live if c["before"] is not None
    ]
    if restored:
        store.upsert(topo, _items(restored), replace=True, **write)
    added = [(c["subject"], c["predicate"]) for c in live if c["before"] is None]
    for subject, predicate in _retractions(added):
        store.retract(topo, subject, predicate=predicate, **write)
    _update(topo, row["id"], state="undone", undone_by=principal.actor, undone_at=_now())
    ctx.db.audit(
        action="topology.undo",
        decision="ok",
        workspace_id=principal.workspace_id,
        user_id=principal.user_id,
        connection_id=principal.connection_id,
        auth=principal.auth,
        wing=TOPOLOGY_WING,
        target_ids=[row["id"]],
    )
    skipped = [
        {"subject": c["subject"], "predicate": c["predicate"]} for c in changes if c not in live
    ]
    return {**_public(_row(topo, row["id"])), "skipped": skipped}

"""Verified identity, scope visibility and the rule lifecycle for MCP and REST."""

import hashlib
import logging
import re
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from commonplace import events
from commonplace.boards.service import normalize_repo, validate_text
from commonplace.extensions import ToolError
from commonplace.http_util import log_failure

from .store import RuleStore, now, sequence

KINDS = ("permission", "limit", "instruction", "style")
SCOPES = ("global", "board", "repo")
RULE_FIELDS = (
    "id agent key text agent_text kind scope scope_ref status source pending version seq "
    "retired_by updated_at updated_by"
).split()
WAITING = "(status='proposed' OR pending=1) AND (status!='retired' OR retired_by='owner')"
logger = logging.getLogger(__name__)


def open_store(workspace_dir):
    return RuleStore(workspace_dir)


class VersionClash(ToolError):
    def __init__(self, current):
        super().__init__("no: rule changed")
        self.current = current


def rule_json(row):
    return {key: row[key] for key in RULE_FIELDS}


def rule_text(value):
    return validate_text(value, "text", required=True, limit=4000)


def rule_key(text):
    return "r-" + hashlib.sha1(" ".join(text.split()).encode()).hexdigest()[:10]


def fields(text, kind, scope="global", scope_ref=None):
    text = rule_text(text)
    if kind not in KINDS or scope not in SCOPES:
        raise ToolError("no: invalid kind or scope")
    if scope == "global":
        scope_ref = None
    else:
        scope_ref = validate_text(scope_ref, "scope_ref", required=True, limit=1000)
        if scope == "repo":
            scope_ref = normalize_repo(scope_ref)
            if len(scope_ref.split("/")) != 3 or not all(scope_ref.split("/")):
                raise ToolError("no: repo must be host/owner/name")
    return dict(text=text, kind=kind, scope=scope, scope_ref=scope_ref)


def row_for(conn, rule_id):
    if type(rule_id) not in (str, int) or not str(rule_id).isdigit():
        raise ToolError("no: rule not found")
    row = conn.execute("SELECT * FROM rules WHERE id=?", (rule_id,)).fetchone()
    if row is None:
        raise ToolError("no: rule not found")
    return row


class RuleService:
    def __init__(self, ctx):
        self.ctx = ctx
        self.principal = ctx.principal
        self.store = open_store(ctx.workspace_dir())
        self._board_visibility = {}

    def _emit(self, kind, data):
        try:
            events.emit(
                self.ctx.db,
                self.principal.workspace_id,
                kind,
                actor=self.principal.actor,
                data=data,
            )
        except Exception as exc:
            log_failure(logger, "committed rule event emission failed", exc)

    @contextmanager
    def transaction(self):
        with self.store.transaction() as conn:
            before = sequence(conn)
            yield conn
            changed = conn.execute(
                "SELECT agent,id,key FROM rules WHERE seq>? ORDER BY seq", (before,)
            ).fetchall()
        for agent in dict.fromkeys(row["agent"] for row in changed):
            rows = [row for row in changed if row["agent"] == agent]
            self._emit(
                "rule.changed",
                {"agent": agent, "ids": [r["id"] for r in rows], "keys": [r["key"] for r in rows]},
            )

    def _person(self, *, owner=False):
        if not self.principal.person or (owner and self.principal.role != "owner"):
            raise ToolError(
                "no: only a workspace owner can do that" if owner else "no: people only"
            )

    def _board_visible(self, slug):
        if slug not in self._board_visibility:
            self._board_visibility[slug] = self._lookup_board_visible(slug)
        return self._board_visibility[slug]

    def _lookup_board_visible(self, slug):
        # Opening BoardStore would migrate it; rules only inspect the existing store.
        path = Path(self.ctx.workspace_dir()) / "boards.sqlite3"
        if not path.is_file():
            return False
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
            row = conn.execute("SELECT wing FROM boards WHERE slug=?", (slug,)).fetchone()
        return row is not None and self.principal.policy.can_read_wing(row[0])

    def _visible(self, row):
        return self.principal.person or (
            row["agent"] in (self.principal.actor, "*")
            and (row["scope"] != "board" or self._board_visible(row["scope_ref"]))
        )

    def _snapshot(self, conn, rule_id, *, note=None, source=None):
        row = row_for(conn, rule_id)
        p = self.principal
        conn.execute(
            "INSERT INTO rule_versions(rule_id,version,text,kind,scope,scope_ref,status,source,"
            "note,stamp_user,stamp_connection,stamp_trust,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                row["id"],
                row["version"],
                row["text"],
                row["kind"],
                row["scope"],
                row["scope_ref"],
                row["status"],
                source or row["source"],
                note,
                p.user_id,
                None if p.person else p.actor.removeprefix("conn:"),
                p.policy.trust,
                now(),
            ),
        )
        return rule_json(row)

    def _change(self, conn, row, updates, *, note=None, source=None):
        conn.execute("UPDATE meta SET value=value+1 WHERE key='seq'")
        updates = dict(
            updates,
            version=row["version"] + 1,
            seq=sequence(conn),
            updated_at=now(),
            updated_by=self.principal.actor,
        )
        conn.execute(
            "UPDATE rules SET " + ",".join(f"{key}=?" for key in updates) + " WHERE id=?",
            (*updates.values(), row["id"]),
        )
        return self._snapshot(conn, row["id"], note=note, source=source)

    def _create(self, conn, agent, key, values, *, owner=False):
        conn.execute("UPDATE meta SET value=value+1 WHERE key='seq'")
        stamp = now()
        columns = dict(
            agent=agent,
            key=key,
            **values,
            agent_text=None if owner else values["text"],
            status="accepted" if owner else "proposed",
            source="owner" if owner else "agent",
            seq=sequence(conn),
            created_at=stamp,
            updated_at=stamp,
            updated_by=self.principal.actor,
        )
        cursor = conn.execute(
            "INSERT INTO rules("
            + ",".join(columns)
            + ") VALUES ("
            + ",".join("?" for _ in columns)
            + ")",
            tuple(columns.values()),
        )
        return self._snapshot(conn, cursor.lastrowid)

    def rules_publish(self, rules, replace=False):
        if self.principal.person or not self.principal.policy.has_scope("memory:write"):
            raise ToolError("no: an agent with memory:write is required")
        if not isinstance(rules, list) or len(rules) > 200 or type(replace) is not bool:
            raise ToolError("no: publish requires up to 200 rules and a boolean replace")
        prepared = {}
        for item in rules:
            if not isinstance(item, dict) or set(item) - {
                "key",
                "text",
                "kind",
                "scope",
                "board",
                "repo",
            }:
                raise ToolError("no: invalid rule")
            scope = item.get("scope", "global")
            if scope not in SCOPES:
                raise ToolError("no: invalid scope")
            values = fields(item.get("text"), item.get("kind"), scope, item.get(scope))
            key = item.get("key", rule_key(values["text"]))
            if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", key):
                raise ToolError("no: key must match [A-Za-z0-9._-]{1,100}")
            if key in prepared:
                raise ToolError("no: duplicate rule key")
            if scope == "board" and not self._board_visible(values["scope_ref"]):
                raise ToolError("no: board not found")
            prepared[key] = values
        result = []
        with self.transaction() as conn:
            for key, values in prepared.items():
                row = conn.execute(
                    "SELECT * FROM rules WHERE agent=? AND key=?", (self.principal.actor, key)
                ).fetchone()
                if row is None:
                    result.append(self._create(conn, self.principal.actor, key, values))
                    continue
                if not self._visible(row):
                    raise ToolError("no: rule not found")
                if row["retired_by"] == "agent":
                    updates = dict(
                        values,
                        agent_text=values["text"],
                        source="agent",
                        status="proposed",
                        pending=0,
                        retired_by=None,
                    )
                elif row["source"] == "owner" or row["retired_by"] == "owner":
                    updates = (
                        {
                            "agent_text": values["text"],
                            "pending": int(values["text"] != row["text"]),
                        }
                        if values["text"] != row["agent_text"]
                        else {}
                    )
                else:
                    updates = dict(values, agent_text=values["text"], status="proposed", pending=0)
                    if all(row[k] == v for k, v in values.items()):
                        updates = {}
                result.append(self._change(conn, row, updates) if updates else rule_json(row))
            if replace:
                rows = conn.execute(
                    "SELECT * FROM rules WHERE agent=? AND source='agent' AND status!='retired' "
                    "AND retired_by IS NULL",
                    (self.principal.actor,),
                ).fetchall()
                for row in rows:
                    if row["key"] not in prepared and self._visible(row):
                        result.append(
                            self._change(
                                conn,
                                row,
                                {"status": "retired", "retired_by": "agent"},
                                note="dropped by agent",
                            )
                        )
            total, active = conn.execute(
                "SELECT count(*),coalesce(sum(status!='retired'),0) FROM rules WHERE agent=?",
                (self.principal.actor,),
            ).fetchone()
            if active > 500 or total > 2000:
                raise ToolError("no: agent rule limit exceeded (500 non-retired, 2000 total)")
            return {"rules": result, "seq": sequence(conn)}

    def rules_get(self, board=None, repo=None, format="text", include_retired=False):
        if format not in ("text", "md") or type(include_retired) is not bool:
            raise ToolError("no: invalid format or include_retired")
        if board is not None:
            validate_text(board, "board", required=True, limit=1000)
        if repo is not None:
            repo = fields("repo", "instruction", "repo", repo)["scope_ref"]
        with self.store.read() as conn:
            rows = conn.execute("SELECT * FROM rules ORDER BY id").fetchall()
            return {
                "rules": [
                    rule_json(row)
                    for row in rows
                    if self._visible(row)
                    and (
                        row["scope"] == "global"
                        or (row["scope"] == "board" and row["scope_ref"] == board)
                        or (row["scope"] == "repo" and row["scope_ref"] == repo)
                    )
                    and (row["status"] != "retired" or (include_retired and format != "md"))
                ],
                "seq": sequence(conn),
            }

    def rules_changed(self, since):
        if type(since) is not int or since < 0:
            raise ToolError("no: since must be a nonnegative integer")
        with self.store.read() as conn:
            seq = sequence(conn)
            if since > seq:
                return {"rules": [], "seq": seq, "reset": True}
            rows = conn.execute(
                "SELECT agent,key,status,scope,scope_ref FROM rules WHERE seq>? ORDER BY seq",
                (since,),
            )
            return {
                "rules": [
                    {
                        "key": row["key"],
                        "status": row["status"],
                        "target": "all" if row["agent"] == "*" else "own",
                    }
                    for row in rows
                    if self._visible(row)
                ],
                "seq": seq,
            }

    def list_rules(self):
        self._person()
        with self.store.read() as conn:
            return {
                "rules": [rule_json(r) for r in conn.execute("SELECT * FROM rules ORDER BY id")],
                "suggestions": [
                    dict(r)
                    for r in conn.execute(
                        "SELECT * FROM rule_suggestions WHERE status='open' ORDER BY id"
                    )
                ],
                "agents": [
                    r["name"]
                    for r in self.ctx.db.connections(self.principal.workspace_id)
                    if not r["revoked_at"]
                ]
                if self.principal.role == "owner"
                else [],
                "seq": sequence(conn),
                "can_edit": self.principal.role == "owner",
                "waiting": conn.execute(f"SELECT count(*) FROM rules WHERE {WAITING}").fetchone()[
                    0
                ],
                "waiting_by_agent": {
                    r["agent"]: r["waiting"]
                    for r in conn.execute(
                        f"SELECT agent,count(*) AS waiting FROM rules WHERE {WAITING} GROUP BY agent"
                    )
                },
            }

    def count(self):
        self._person()
        with self.store.read() as conn:
            return {
                "waiting": conn.execute(f"SELECT count(*) FROM rules WHERE {WAITING}").fetchone()[
                    0
                ],
                "seq": sequence(conn),
            }

    def history(self, rule_id):
        self._person()
        with self.store.connection() as conn:
            row = row_for(conn, rule_id)
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM rule_versions WHERE rule_id=? ORDER BY id", (row["id"],)
                )
            ]

    def create(self, agent, text, kind, scope="global", scope_ref=None):
        self._person(owner=True)
        if not isinstance(agent, str) or (
            agent != "*" and (not agent.startswith("conn:") or not agent[5:].strip())
        ):
            raise ToolError("no: agent must be * or conn:<name>")
        values = fields(text, kind, scope, scope_ref)
        with self.transaction() as conn:
            key = rule_key(text)
            if conn.execute("SELECT 1 FROM rules WHERE agent=? AND key=?", (agent, key)).fetchone():
                raise ToolError("no: rule key already exists")
            return self._create(conn, agent, key, values, owner=True)

    def _versioned(self, conn, rule_id, version):
        row = row_for(conn, rule_id)
        if type(version) is not int:
            raise ToolError("no: version must be an integer")
        if row["version"] != version:
            raise VersionClash(rule_json(row))
        return row

    def edit(self, rule_id, version, **changes):
        self._person(owner=True)
        if set(changes) - {"text", "kind", "scope", "scope_ref", "note"}:
            raise ToolError("no: invalid edit")
        note = changes.pop("note", None)
        if note is not None:
            validate_text(note, "note")
        with self.transaction() as conn:
            row = self._versioned(conn, rule_id, version)
            if row["status"] == "retired":
                raise ToolError("no: restore the rule first")
            values = fields(
                **{k: changes.get(k, row[k]) for k in ("text", "kind", "scope", "scope_ref")}
            )
            if all(row[k] == value for k, value in values.items()):
                raise ToolError("no: nothing to change")
            return self._change(
                conn,
                row,
                dict(values, status="edited", source="owner", pending=0, retired_by=None),
                note=note,
            )

    def action(self, rule_id, version, action):
        self._person(owner=True)
        with self.transaction() as conn:
            row = self._versioned(conn, rule_id, version)
            if action == "accept" and row["status"] == "proposed":
                updates = {"status": "accepted"}
            elif action == "retire" and row["status"] != "retired":
                updates = {"status": "retired", "retired_by": "owner"}
            elif action == "restore" and row["status"] == "retired":
                updates = {
                    "status": "edited" if row["source"] == "owner" else "accepted",
                    "retired_by": None,
                }
                if row["retired_by"] == "agent":
                    updates.update(status="accepted", source="owner")
            elif action == "accept_agent" and row["pending"] == 1 and row["agent_text"] is not None:
                updates = dict(
                    text=row["agent_text"],
                    source="agent",
                    status="accepted",
                    pending=0,
                    retired_by=None,
                )
            elif action == "keep_edit" and row["pending"] == 1:
                updates = {"pending": 0}
            else:
                raise ToolError("no: invalid action")
            return self._change(conn, row, updates)

    def suggest(self, rule_id, text, from_agent):
        self._person(owner=True)
        rule_text(text)
        validate_text(from_agent, "from_agent", required=True, limit=1000)
        with self.transaction() as conn:
            row = row_for(conn, rule_id)
            cursor = conn.execute(
                "INSERT INTO rule_suggestions(rule_id,text,from_agent,base_version,status,stamp_user,created_at) VALUES (?,?,?,?,'open',?,?)",
                (row["id"], text, from_agent, row["version"], self.principal.user_id, now()),
            )
            result = dict(
                conn.execute(
                    "SELECT * FROM rule_suggestions WHERE id=?", (cursor.lastrowid,)
                ).fetchone()
            )
        self._emit(
            "rule.suggestion",
            {
                "rule_id": row["id"],
                "suggestion_id": result["id"],
                "action": "stored",
            },
        )
        return result

    def decide_suggestion(self, suggestion_id, action, version=None):
        self._person(owner=True)
        if action not in ("accept", "dismiss"):
            raise ToolError("no: invalid action")
        if type(suggestion_id) not in (str, int) or not str(suggestion_id).isdigit():
            raise ToolError("no: suggestion not found")
        with self.transaction() as conn:
            suggestion = conn.execute(
                "SELECT * FROM rule_suggestions WHERE id=?", (suggestion_id,)
            ).fetchone()
            if suggestion is None:
                raise ToolError("no: suggestion not found")
            if suggestion["status"] != "open":
                raise ToolError("no: suggestion already decided")
            row = row_for(conn, suggestion["rule_id"])
            if action == "accept":
                row = self._versioned(conn, row["id"], version)
                if row["version"] != suggestion["base_version"]:
                    raise VersionClash(rule_json(row))
                if row["status"] == "retired":
                    raise ToolError("no: cannot accept a suggestion on a retired rule")
            updates = (
                dict(text=suggestion["text"], status="edited", source="owner", pending=0)
                if action == "accept"
                else {}
            )
            conn.execute(
                "UPDATE rule_suggestions SET status=?,decided_at=? WHERE id=?",
                ("accepted" if action == "accept" else "dismissed", now(), suggestion_id),
            )
            result = (
                self._change(
                    conn,
                    row,
                    updates,
                    note=suggestion["text"],
                    source="suggestion" if action == "accept" else None,
                )
                if action == "accept"
                else rule_json(row)
            )
        if action == "dismiss":
            self._emit(
                "rule.suggestion",
                {
                    "rule_id": row["id"],
                    "suggestion_id": suggestion["id"],
                    "action": "dismissed",
                },
            )
        return result

"""Setup proposals approved by the owner (card #675, ``docs/system/setup-proposals.md``).

An agent of the owner proposes one workspace setup: bots, a Coworker per project bound to one of
those bots, and a model per role for projects and the workspace default. The proposal is stored as
a versioned record whose ``sha256`` is the digest of its canonical JSON. Reads carry a plain-words
diff against the workspace as it is now.

Only the workspace owner reads or proposes: the signed-in owner or a connection of the owner.
Everyone else gets :class:`Hidden` (the REST 404 of an unknown route). Proposing writes only the
proposal row; nothing else in the workspace changes.

Only the signed-in owner (a web session) applies or rejects, bound to the exact ``sha256`` shown.
Apply runs every item as that owner through the existing service paths: the Coworker bot store
(``create_bot``, never with a connection), a project-bound Coworker conversation
(``create_conversation``) and ``models.catalog_add`` / ``models.put`` / ``models.put_workspace``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone

from commonplace import redact
from commonplace.projects import models
from commonplace.projects.types import MODEL_ROLES, ProjectDenied

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS setup_proposals (
    workspace_id TEXT NOT NULL,
    id INTEGER NOT NULL,                      -- version per workspace: v1, v2, ...; never reused
    digest TEXT NOT NULL,                     -- lowercase hex sha256 of body (the API's sha256)
    body TEXT NOT NULL,                       -- canonical JSON (docs/system/setup-proposals.md)
    status TEXT NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed', 'applying', 'applied', 'partial', 'rejected')),
    proposed_by TEXT NOT NULL,                -- actor ref: user:<id> or conn:<name>
    proposed_at TEXT NOT NULL,
    decided_by TEXT,                          -- actor ref of the owner who applied or rejected
    decided_at TEXT,
    results TEXT,                             -- JSON per-item results of an apply
    PRIMARY KEY (workspace_id, id)
);
"""

KEY = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
BOARD = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
MAX_BYTES = 64 * 1024
MAX_BOTS = 20
MAX_COWORKERS = 40
MAX_PROJECTS = 50
MAX_PENDING = 20
MAX_LISTED = 20
BOT_NAME_MAX = 80  # the Coworker bot store's limits
INSTRUCTIONS_MAX = 4_000
TITLE_MAX = 120
SUMMARY_LINES = 3
STATUSES = ("proposed", "applying", "applied", "partial", "rejected")
# The words of the Agent models pickers in Settings and project settings.
ROLE_WORDS = {"main": "Bot", "worker": "Builder", "reviewer": "Reviewer", "gate": "Gate"}


class SetupError(ValueError):
    """A refused request: ``code`` for machines, ``path`` names the field, ``status`` for REST."""

    def __init__(self, code: str, message: str, *, path: str = "", status: int = 400):
        self.code, self.path, self.status = code, path, status
        super().__init__(message)


class Hidden(SetupError):
    """Not the owner, or a proposal the caller may not see: the same answer as an unknown route."""

    def __init__(self):
        super().__init__("not_found", "not found", status=404)


def ensure_schema(db) -> None:
    with db._lock:
        db._conn.executescript(_SCHEMA)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- body


def _refuse(code: str, message: str, path: str) -> SetupError:
    return SetupError(code, message, path=path)


def _table(value, path: str, allowed, required=()) -> dict:
    if not isinstance(value, dict):
        raise _refuse("invalid", "must be an object", path)
    extra = sorted(set(value) - set(allowed))
    if extra:
        raise _refuse("unknown_field", f"unknown field {extra[0]!r}", path)
    missing = [key for key in required if key not in value]
    if missing:
        raise _refuse("missing_field", f"missing field {missing[0]!r}", path)
    return value


def _text(value, path: str, low: int, high: int) -> str:
    if not isinstance(value, str) or not low <= len(value) <= high:
        raise _refuse("invalid", f"must be text of {low} to {high} characters", path)
    if low and not value.strip():
        raise _refuse("invalid", "must not be blank", path)
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise _refuse("invalid", "must be valid text", path) from None
    if redact.redact(value)[1]:
        raise _refuse("looks_secret", "looks like a credential; never put one in a setup", path)
    return value


def _key(value, path: str) -> str:
    if not isinstance(value, str) or KEY.fullmatch(value) is None:
        raise _refuse("invalid", "must be a short key: a-z, 0-9, '-' or '_'", path)
    return value


def _list(value, path: str, limit: int) -> list:
    if not isinstance(value, list) or len(value) > limit:
        raise _refuse("invalid", f"must be a list of at most {limit}", path)
    return value


def _board(value, path: str) -> str:
    if not isinstance(value, str) or BOARD.fullmatch(value) is None:
        raise _refuse("invalid", "must be a project's board slug", path)
    return value


def _choices(value, path: str) -> dict:
    roles = _table(value, path, MODEL_ROLES)
    result = {}
    for role in MODEL_ROLES:
        if role not in roles:
            continue
        choice = roles[role]
        if choice is None:
            result[role] = None
            continue
        try:
            entry = models._shape(choice)
        except ValueError:
            raise _refuse(
                "invalid", "must be {provider, model} or null", f"{path}.{role}"
            ) from None
        for field in ("provider", "model"):
            if redact.redact(getattr(entry, field))[1]:
                raise _refuse(
                    "looks_secret",
                    "looks like a credential; never put one in a setup",
                    f"{path}.{role}.{field}",
                )
        result[role] = entry.public()
    return result


def validate(data) -> dict:
    """The setup as stored: checked, with only known fields, ``null`` kept as "clear"."""
    body = _table(data, "", ("bots", "coworkers", "models"))
    bots, keys = [], set()
    for index, item in enumerate(_list(body.get("bots", []), "bots", MAX_BOTS)):
        path = f"bots[{index}]"
        item = _table(item, path, ("key", "name", "instructions"), ("key", "name"))
        key = _key(item["key"], f"{path}.key")
        if key in keys:
            raise _refuse("duplicate_key", "another bot has this key", f"{path}.key")
        keys.add(key)
        bots.append(
            {
                "key": key,
                "name": _text(item["name"], f"{path}.name", 1, BOT_NAME_MAX),
                "instructions": _text(
                    item.get("instructions", ""), f"{path}.instructions", 0, INSTRUCTIONS_MAX
                ),
            }
        )
    coworkers, seen = [], set()
    for index, item in enumerate(_list(body.get("coworkers", []), "coworkers", MAX_COWORKERS)):
        path = f"coworkers[{index}]"
        item = _table(item, path, ("key", "board", "bot", "title"), ("key", "board", "bot"))
        key = _key(item["key"], f"{path}.key")
        if key in seen:
            raise _refuse("duplicate_key", "another Coworker has this key", f"{path}.key")
        seen.add(key)
        bot = _key(item["bot"], f"{path}.bot")
        if bot not in keys:
            raise _refuse("unknown_bot", "names no bot of this setup", f"{path}.bot")
        coworkers.append(
            {
                "key": key,
                "board": _board(item["board"], f"{path}.board"),
                "bot": bot,
                "title": _text(item.get("title", ""), f"{path}.title", 0, TITLE_MAX),
            }
        )
    result = {"bots": bots, "coworkers": coworkers}
    if "models" in body:
        raw = _table(body["models"], "models", ("workspace", "projects"))
        chosen = {}
        if "workspace" in raw:
            chosen["workspace"] = _choices(raw["workspace"], "models.workspace")
        projects = raw.get("projects", {})
        if not isinstance(projects, dict) or len(projects) > MAX_PROJECTS:
            raise _refuse(
                "invalid", f"must be an object of at most {MAX_PROJECTS}", "models.projects"
            )
        chosen["projects"] = {
            _board(slug, "models.projects"): _choices(roles, f"models.projects.{slug}")
            for slug, roles in projects.items()
        }
        result["models"] = chosen
    if not bots and not coworkers and not _model_count(result):
        raise _refuse("empty", "a setup needs at least one bot, Coworker or model", "")
    return result


def _model_count(body) -> int:
    chosen = body.get("models") or {}
    return len(chosen.get("workspace") or {}) + sum(
        len(roles) for roles in (chosen.get("projects") or {}).values()
    )


def canonical(body: dict) -> bytes:
    """Sorted keys, no spaces, UTF-8 without escapes; list order is creation order."""
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def boards_named(body: dict) -> set[str]:
    return {item["board"] for item in body.get("coworkers", [])} | set(
        (body.get("models") or {}).get("projects", {})
    )


# ---------------------------------------------------------------- authority


def is_owner(principal) -> bool:
    """The workspace owner: the signed-in owner or a connection of the owner; never a machine."""
    return (
        principal is not None
        and getattr(principal, "role", None) == "owner"
        and getattr(principal, "runner", None) is None
    )


def require_owner(principal) -> None:
    if not is_owner(principal):
        raise Hidden()


def readable_boards(ctx) -> dict[str, str]:
    """Live boards the caller's policy reads, slug -> display name."""
    from commonplace.boards.service import BoardService

    with BoardService(ctx).store.connection() as conn:
        rows = conn.execute("SELECT slug, name, wing, archived FROM boards").fetchall()
    policy = ctx.principal.policy
    return {
        row["slug"]: row["name"] or row["slug"]
        for row in rows
        if not row["archived"] and policy.can_read_wing(row["wing"])
    }


# ---------------------------------------------------------------- diff


def _label(choice) -> str:
    if choice is None:
        return "no choice"
    return f"{choice['provider']}/{choice['model']}"


def changes(db, workspace_id: str, body: dict, boards: dict[str, str]) -> list[dict]:
    """One line per item, against the workspace now. ``problem`` marks a line that would fail."""
    lines = []
    names = {bot["key"]: bot["name"] for bot in body.get("bots", [])}
    for bot in body.get("bots", []):
        lines.append(
            {
                "kind": "bot",
                "key": bot["key"],
                "action": "create",
                "text": f'Creates the bot "{bot["name"]}"',
            }
        )
    for item in body.get("coworkers", []):
        board = item["board"]
        line = {
            "kind": "coworker",
            "key": item["key"],
            "board": board,
            "action": "create",
            "text": f"Creates a Coworker on {boards.get(board, board)} with the bot "
            f'"{names[item["bot"]]}"' + (f': "{item["title"]}"' if item["title"] else ""),
        }
        if board not in boards:
            line["problem"] = "unknown_project"
        lines.append(line)
    chosen = body.get("models") or {}
    scopes = []
    if "workspace" in chosen:
        scopes.append((models.WORKSPACE, "Workspace default", chosen["workspace"]))
    for slug, roles in chosen.get("projects", {}).items():
        scopes.append((slug, boards.get(slug, slug), roles))
    for scope, where, roles in scopes:
        known = scope == models.WORKSPACE or scope in boards
        current = models.get(db, workspace_id, scope) if known else dict.fromkeys(MODEL_ROLES)
        offered = (
            {(c.provider, c.model) for c in models.options(db, workspace_id, scope)}
            if known
            else set()
        )
        for role, new in roles.items():
            old = current.get(role)
            old = old.public() if old is not None else None
            word = ROLE_WORDS[role]
            line = {
                "kind": "model",
                "board": None if scope == models.WORKSPACE else scope,
                "role": role,
                "old": old,
                "new": new,
            }
            if new == old:
                line.update(
                    action="unchanged", text=f"{where}: the {word} model stays {_label(old)}"
                )
            elif new is None:
                line.update(
                    action="clear",
                    text=f"{where}: the {word} model choice {_label(old)} is cleared",
                )
            else:
                line.update(
                    action="set",
                    text=f"{where}: the {word} model goes from {_label(old)} to {_label(new)}",
                )
                if (new["provider"], new["model"]) not in offered:
                    line["adds_to_list"] = True
                    line["text"] += " (added to the model list)"
            if not known:
                line["problem"] = "unknown_project"
            lines.append(line)
    return lines


def summarize(lines: list[dict]) -> str:
    """The first three lines that change something, then "and N more"."""
    texts = [line["text"] for line in lines if line["action"] != "unchanged"]
    if not texts:
        return "Changes nothing"
    shown, rest = texts[:SUMMARY_LINES], len(texts) - SUMMARY_LINES
    text = "; ".join(shown)
    if rest > 0:
        text += f" and {rest} more change{'s' if rest != 1 else ''}"
    return text


# ---------------------------------------------------------------- store


def _proposer_name(db, actor: str) -> str:
    if actor.startswith("conn:"):
        return actor[5:]
    if actor.startswith("user:"):
        try:
            row = db.user(actor[5:])
        except Exception:
            return "A person"
        return row["name"] or "A person"  # a display name only, never part of an address
    return actor


def _view(ctx, row, boards: dict[str, str]) -> dict:
    db = ctx.db
    body = json.loads(row["body"])
    lines = changes(db, ctx.principal.workspace_id, body, boards)
    view = {
        "id": row["id"],
        "ref": f"v{row['id']}",
        "sha256": row["digest"],
        "status": row["status"],
        "proposed_by": row["proposed_by"],
        "proposer_name": _proposer_name(db, row["proposed_by"]),
        "proposed_at": row["proposed_at"],
        "decided_by": row["decided_by"],
        "decided_at": row["decided_at"],
        "body": row["body"],
        "setup": body,
        "changes": lines,
        "summary": summarize(lines),
        "problems": sum(1 for line in lines if "problem" in line),
    }
    if row["results"] is not None:
        view["results"] = json.loads(row["results"])
    return view


def _visible(ctx, row, boards: dict[str, str]) -> bool:
    """The signed-in owner sees every proposal; a connection only one whose projects it reads."""
    if ctx.principal.person:
        return True
    return boards_named(json.loads(row["body"])) <= set(boards)


def propose(ctx, data) -> tuple[dict, bool]:
    """Store a proposal; ``(view, created)``. The same body still waiting answers that proposal."""
    principal, db = ctx.principal, ctx.db
    require_owner(principal)
    body = validate(data)
    raw = canonical(body)
    if len(raw) > MAX_BYTES:
        raise _refuse("too_large", f"a setup is at most {MAX_BYTES} bytes", "")
    boards = readable_boards(ctx)
    for slug in sorted(boards_named(body)):
        if slug not in boards:
            raise SetupError(
                "unknown_project", f"no project {slug!r} you can read", path="board", status=400
            )
    sha = digest(raw)
    ws = principal.workspace_id
    with db._tx() as conn:
        same = conn.execute(
            "SELECT * FROM setup_proposals WHERE workspace_id=? AND digest=? "
            "AND status='proposed' ORDER BY id DESC LIMIT 1",
            (ws, sha),
        ).fetchone()
        if same is not None:
            return _view(ctx, same, boards), False
        waiting = conn.execute(
            "SELECT count(*) FROM setup_proposals WHERE workspace_id=? AND status='proposed'",
            (ws,),
        ).fetchone()[0]
        if waiting >= MAX_PENDING:
            raise SetupError(
                "too_many_pending",
                f"{MAX_PENDING} setups are already waiting for the owner",
                status=409,
            )
        number = conn.execute(
            "SELECT COALESCE(MAX(id), 0) + 1 FROM setup_proposals WHERE workspace_id=?", (ws,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO setup_proposals (workspace_id, id, digest, body, status, proposed_by, "
            "proposed_at) VALUES (?, ?, ?, ?, 'proposed', ?, ?)",
            (ws, number, sha, raw.decode("utf-8"), principal.actor, _now()),
        )
        db.audit(
            action="setup.propose",
            decision="ok",
            workspace_id=ws,
            user_id=principal.user_id,
            connection_id=None if principal.person else principal.connection_id,
            auth=principal.auth,
            target_ids=(f"v{number}", sha),
        )
        row = conn.execute(
            "SELECT * FROM setup_proposals WHERE workspace_id=? AND id=?", (ws, number)
        ).fetchone()
    _emit(ctx, "setup.proposed", number, sha)
    return _view(ctx, row, boards), True


def show(ctx, number) -> dict:
    """One proposal with its diff (and results once decided)."""
    principal, db = ctx.principal, ctx.db
    require_owner(principal)
    if type(number) is not int or number < 1:
        raise Hidden()
    with db._lock:
        row = db._conn.execute(
            "SELECT * FROM setup_proposals WHERE workspace_id=? AND id=?",
            (principal.workspace_id, number),
        ).fetchone()
    boards = readable_boards(ctx)
    if row is None or not _visible(ctx, row, boards):
        raise Hidden()
    return _view(ctx, row, boards)


def listing(ctx, status=None) -> dict:
    """The newest proposals the caller may see (at most 20), optionally of one status."""
    principal, db = ctx.principal, ctx.db
    require_owner(principal)
    if status is not None and status not in STATUSES:
        raise _refuse("invalid", "unknown status", "status")
    sql = "SELECT * FROM setup_proposals WHERE workspace_id=?"
    params: list = [principal.workspace_id]
    if status is not None:
        sql += " AND status=?"
        params.append(status)
    boards = readable_boards(ctx)
    visible = []
    with db._lock:
        # Newest first, stopping at the cap: the signed-in owner sees every row, so the first 20
        # are the answer; a connection skips the ones naming a project it cannot read.
        for row in db._conn.execute(sql + " ORDER BY id DESC", params):
            if _visible(ctx, row, boards):
                visible.append(row)
                if len(visible) >= MAX_LISTED:
                    break
    return {"proposals": [_view(ctx, row, boards) for row in visible]}


def _emit(ctx, kind: str, number: int, sha: str) -> None:
    """Refs-only event after the commit; a failure to emit never fails the call."""
    from commonplace import events
    from commonplace.http_util import log_failure

    try:
        events.emit(
            ctx.db,
            ctx.principal.workspace_id,
            kind,
            subject=f"v{number}",
            actor=ctx.principal.actor,
            data={"id": number, "sha256": sha},
        )
    except Exception as exc:
        log_failure(logger, "setup proposal event failed", exc)


# ---------------------------------------------------------------- decide


def is_signed_in_owner(principal) -> bool:
    """The owner in a web session: the only principal that applies or rejects a setup."""
    return (
        is_owner(principal)
        and getattr(principal, "person", False)
        and getattr(principal, "auth", None) == "session"
    )


def _decidable(ctx, number):
    """The proposal row for a decision; Hidden for anyone who may not see it."""
    principal, db = ctx.principal, ctx.db
    require_owner(principal)
    if not is_signed_in_owner(principal):
        raise SetupError(
            "owner_only", "only the signed-in owner can approve or reject a setup", status=403
        )
    if type(number) is not int or number < 1:
        raise Hidden()
    with db._lock:
        row = db._conn.execute(
            "SELECT * FROM setup_proposals WHERE workspace_id=? AND id=?",
            (principal.workspace_id, number),
        ).fetchone()
    if row is None:
        raise Hidden()
    return row


def _claim(ctx, number: int, sha: str, status: str) -> None:
    """Move ``proposed`` to ``status`` only for the exact digest shown; refuse anything else."""
    principal, db = ctx.principal, ctx.db
    if not isinstance(sha, str) or re.fullmatch(r"[0-9a-f]{64}", sha) is None:
        raise SetupError(
            "sha256_invalid", "sha256 must be 64 lowercase hex characters", path="sha256"
        )
    with db._tx() as conn:
        claimed = conn.execute(
            "UPDATE setup_proposals SET status=?, decided_by=?, decided_at=? "
            "WHERE workspace_id=? AND id=? AND status='proposed' AND digest=?",
            (status, principal.actor, _now(), principal.workspace_id, number, sha),
        ).rowcount
        if claimed:
            return
        row = conn.execute(
            "SELECT status FROM setup_proposals WHERE workspace_id=? AND id=?",
            (principal.workspace_id, number),
        ).fetchone()
    if row["status"] != "proposed":
        raise SetupError(
            "not_proposed", f"this setup is already {row['status']}", path="status", status=409
        )
    raise SetupError(
        "sha256_mismatch",
        "this is not the version you approved; read it again",
        path="sha256",
        status=409,
    )


def _record(ctx, number: int, results: list, status: str | None = None) -> None:
    """Write the results so far (and the final status) right after each item."""
    principal, db = ctx.principal, ctx.db
    payload = json.dumps(results, ensure_ascii=False, sort_keys=True)
    with db._tx() as conn:
        if status is None:
            conn.execute(
                "UPDATE setup_proposals SET results=? WHERE workspace_id=? AND id=?",
                (payload, principal.workspace_id, number),
            )
        else:
            conn.execute(
                "UPDATE setup_proposals SET results=?, status=? WHERE workspace_id=? AND id=?",
                (payload, status, principal.workspace_id, number),
            )


def _attempt(item: dict, call, *args) -> dict:
    """Run one item; its result is ``ok`` with what it created, or ``ok: false`` with a code.

    A failure never stops the next item and never leaves the proposal in ``applying``."""
    from commonplace.chat.coworkers.store import StoreError
    from commonplace.http_util import log_failure

    try:
        item.update(ok=True, **call(*args))
    except StoreError as exc:
        item.update(ok=False, error=exc.code)
    except models.UnknownModel:
        item.update(ok=False, error="unknown_model")
    except ProjectDenied:
        item.update(ok=False, error="owner_only")
    except ValueError:
        item.update(ok=False, error="invalid")
    except Exception as exc:
        log_failure(logger, "setup item failed", exc)
        item.update(ok=False, error="failed")
    return item


def _apply_bot(store, user_id, bot) -> dict:
    created = store.create_bot(user_id, bot["name"], bot["instructions"])
    return {"id": created["id"], "name": created["name"]}


def _apply_coworker(store, user_id, item, bot_id) -> dict:
    created = store.create_conversation(
        user_id, "dm", item["title"], bot_ids=[bot_id], board=item["board"]
    )
    return {"id": created["id"], "title": created["title"]}


def _apply_models(ctx, scope, roles) -> dict:
    principal, db = ctx.principal, ctx.db
    offered = {(c.provider, c.model) for c in models.options(db, principal.workspace_id, scope)}
    for choice in roles.values():
        if choice is not None and (choice["provider"], choice["model"]) not in offered:
            models.catalog_add(db, principal, scope, choice)
    if scope == models.WORKSPACE:
        models.put_workspace(db, principal, roles)
        effective = models.workspace_roles(db, principal.workspace_id)
    else:
        models.put(db, principal, scope, roles)
        effective = models.effective_roles(db, principal.workspace_id, scope)
    return {"roles": {role: effective[role].public() for role in roles}}


def apply(ctx, number, sha) -> dict:
    """Apply one proposal as the signed-in owner, item by item through the existing services.

    The digest check and the move to ``applying`` are one statement, so a proposal applies at
    most once and a changed proposal never applies. Each item result is stored as soon as it is
    known; the proposal ends ``applied`` (every item succeeded) or ``partial``."""
    from commonplace.chat.coworkers.store import open_store

    principal, db = ctx.principal, ctx.db
    _decidable(ctx, number)
    _claim(ctx, number, sha, "applying")
    with db._lock:
        row = db._conn.execute(
            "SELECT body FROM setup_proposals WHERE workspace_id=? AND id=?",
            (principal.workspace_id, number),
        ).fetchone()
    body = validate(json.loads(row["body"]))
    results: list = []
    bot_ids: dict = {}
    store = open_store(ctx.gateway, principal.workspace_id)
    for bot in body["bots"]:
        item = _attempt(
            {"kind": "bot", "key": bot["key"]}, _apply_bot, store, principal.user_id, bot
        )
        if item["ok"]:
            bot_ids[bot["key"]] = item["id"]
        results.append(item)
        _record(ctx, number, results)
    for coworker in body["coworkers"]:
        item = {"kind": "coworker", "key": coworker["key"], "board": coworker["board"]}
        bot_id = bot_ids.get(coworker["bot"])
        if bot_id is None:
            item.update(ok=False, error="bot_failed")
        else:
            item["bot_id"] = bot_id
            _attempt(item, _apply_coworker, store, principal.user_id, coworker, bot_id)
        results.append(item)
        _record(ctx, number, results)
    chosen = body.get("models") or {}
    scopes = [(models.WORKSPACE, chosen["workspace"])] if "workspace" in chosen else []
    scopes += list(chosen.get("projects", {}).items())
    boards = readable_boards(ctx)
    for scope, roles in scopes:
        item = {"kind": "models", "board": None if scope == models.WORKSPACE else scope}
        if scope != models.WORKSPACE and scope not in boards:
            item.update(ok=False, error="unknown_project")
        else:
            _attempt(item, _apply_models, ctx, scope, roles)
        results.append(item)
        _record(ctx, number, results)
    status = "applied" if all(item["ok"] for item in results) else "partial"
    _record(ctx, number, results, status)
    db.audit(
        action="setup.apply",
        decision="ok" if status == "applied" else "partial",
        workspace_id=principal.workspace_id,
        user_id=principal.user_id,
        auth=principal.auth,
        target_ids=(f"v{number}", sha, *(item["id"] for item in results if item.get("id"))),
    )
    _emit(ctx, "setup.applied", number, sha)
    return show(ctx, number)


def reject(ctx, number, sha) -> dict:
    """The owner says no to the version shown: ``proposed`` becomes ``rejected``, nothing else."""
    principal, db = ctx.principal, ctx.db
    _decidable(ctx, number)
    _claim(ctx, number, sha, "rejected")
    db.audit(
        action="setup.reject",
        decision="ok",
        workspace_id=principal.workspace_id,
        user_id=principal.user_id,
        auth=principal.auth,
        target_ids=(f"v{number}", sha),
    )
    _emit(ctx, "setup.rejected", number, sha)
    return show(ctx, number)

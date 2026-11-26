"""Project model choices and workspace-default resolution."""

from datetime import datetime, timezone

from commonplace.projects.types import (
    CATALOG_ROLES,
    MODEL,
    MODEL_ROLES,
    PROVIDER,
    Effective,
    ModelChoice,
    ProjectDenied,
)

# Built-in suggestions every picker offers next to the workspace's list (card #661). A suggestion
# is never part of a list and never a default: a project with no choice keeps today's behaviour,
# and an external provider is used only when an owner picks it.
SUGGESTED = (ModelChoice("grok", "grok-4.7"),)
WORKSPACE = "*"


class UnknownModel(ValueError):
    """A choice names a model that is neither in the workspace's list nor a suggestion."""


def _shape(choice) -> ModelChoice:
    if (
        not isinstance(choice, dict)
        or set(choice) != {"provider", "model"}
        or not isinstance(choice["provider"], str)
        or PROVIDER.fullmatch(choice["provider"]) is None
        or not isinstance(choice["model"], str)
        or MODEL.fullmatch(choice["model"]) is None
    ):
        raise ValueError("Invalid project models")
    return ModelChoice(choice["provider"], choice["model"])


def catalog(db, workspace_id: str, board: str) -> list[ModelChoice]:
    """The models a project can pick: the board's own list and choices, and workspace defaults.

    Rows of other boards never appear, so a reader of one project sees no other project's labels."""
    marks = ", ".join("?" for _ in CATALOG_ROLES)
    with db._lock:
        rows = db._conn.execute(
            "SELECT provider, model FROM project_model_catalog "
            "WHERE workspace_id=? AND scope IN (?, '*') "
            "UNION SELECT provider, model FROM chat_model_settings "
            f"WHERE workspace_id=? AND scope IN (?, '*') AND role IN ({marks}) "
            "AND provider!='none' AND model!='' ORDER BY provider, model",
            (workspace_id, board, workspace_id, board, *CATALOG_ROLES),
        ).fetchall()
    return [ModelChoice(row["provider"], row["model"]) for row in rows]


def suggestions(listed: list[ModelChoice]) -> list[ModelChoice]:
    """The built-in suggestions a list does not hold yet."""
    return [choice for choice in SUGGESTED if choice not in listed]


def options(db, workspace_id: str, board: str) -> list[ModelChoice]:
    """What a picker offers: the list (``catalog``), then each built-in suggestion not on it."""
    listed = catalog(db, workspace_id, board)
    return listed + suggestions(listed)


def _require_owner(principal) -> None:
    if not principal.person or principal.role != "owner":
        raise ProjectDenied("Only a signed-in workspace owner can change project models")


def catalog_add(db, principal, board: str, choice: dict) -> list[ModelChoice]:
    """An owner adds one model to a project list (validated, audited, idempotent)."""
    _require_owner(principal)
    entry = _shape(choice)
    added_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db._lock, db._tx() as conn:
        added = conn.execute(
            "INSERT OR IGNORE INTO project_model_catalog "
            "(workspace_id, scope, provider, model, added_by, added_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                principal.workspace_id,
                board,
                entry.provider,
                entry.model,
                principal.user_id,
                added_at,
            ),
        )
        if added.rowcount:
            db.audit(
                action="project.model_catalog",
                decision="ok",
                workspace_id=principal.workspace_id,
                user_id=principal.user_id,
                target_ids=(board, f"{entry.provider}/{entry.model}"),
            )
    return catalog(db, principal.workspace_id, board)


def missing_roles(db, workspace_id: str, board: str, config) -> list[dict]:
    """Roles a start of this config runs that cannot get any model, in slot order.

    A role with no choice runs on the default (``effective``), so a slot is missing only when
    the project's model list is empty too.

    One entry per model role: ``role`` (``None`` when a config role names no ``model_role``,
    which no setting can fix) and the ``config_roles`` that use it. The roles are the ones
    ``prepare()`` selects, so Needs me and a refused start agree."""
    grouped: dict = {}
    for name in config.start_roles:
        grouped.setdefault(config.roles[name].model_role, []).append(name)
    missing = [
        {"role": role, "config_roles": names}
        for role, names in grouped.items()
        if role is None or _unresolved(db, workspace_id, board, role)
    ]
    return sorted(missing, key=lambda item: _slot_order(item["role"]))


def _unresolved(db, workspace_id: str, board: str, role: str) -> bool:
    """A role with no choice, no default and no listed model is unusable; a role outside the
    configurable slots has no model to resolve and counts as unset."""
    try:
        return effective(db, workspace_id, board, role).choice is None
    except ValueError:
        return True


def _slot_order(role):
    """Slot order first, any other role after it by name, a missing model_role last."""
    if role is None:
        return (len(MODEL_ROLES) + 1, "")
    return (MODEL_ROLES.index(role) if role in MODEL_ROLES else len(MODEL_ROLES), role)


def get(db, workspace_id: str, board: str) -> dict[str, ModelChoice | None]:
    """Return explicit choices of one scope (a board, or ``*`` for the workspace's defaults);
    inherited choices belong to resolve()."""
    choices: dict[str, ModelChoice | None] = dict.fromkeys(MODEL_ROLES)
    with db._lock:
        rows = db._conn.execute(
            "SELECT role, provider, model FROM chat_model_settings "
            f"WHERE workspace_id=? AND scope=? AND role IN ({', '.join('?' for _ in MODEL_ROLES)})",
            (workspace_id, board, *MODEL_ROLES),
        ).fetchall()
    for row in rows:
        if row["role"] in choices:
            choices[row["role"]] = ModelChoice(row["provider"], row["model"])
    return choices


def put(db, principal, board: str, body: dict) -> dict[str, ModelChoice | None]:
    """Validate an owner's partial update of one project, then atomically store and audit it."""
    return _store(db, principal, board, body, "project.models")


def put_workspace(db, principal, body: dict) -> dict[str, ModelChoice | None]:
    """The owner's workspace default per role: every project without its own choice uses it."""
    return _store(db, principal, WORKSPACE, body, "models.workspace")


def _store(db, principal, scope: str, body: dict, action: str) -> dict[str, ModelChoice | None]:
    _require_owner(principal)
    if not isinstance(body, dict) or set(body) - set(MODEL_ROLES):
        raise ValueError("Invalid project models")
    known = None
    for choice in body.values():
        if choice is None:
            continue
        entry = _shape(choice)
        if known is None:
            known = set(options(db, principal.workspace_id, scope))
        if entry not in known:
            raise UnknownModel("Unknown project model")

    updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db._lock, db._tx() as conn:
        for role, choice in body.items():
            if choice is None:
                conn.execute(
                    "DELETE FROM chat_model_settings WHERE workspace_id=? AND scope=? AND role=?",
                    (principal.workspace_id, scope, role),
                )
            else:
                added = conn.execute(
                    "INSERT OR IGNORE INTO project_model_catalog "
                    "(workspace_id, scope, provider, model, added_by, added_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        principal.workspace_id,
                        scope,
                        choice["provider"],
                        choice["model"],
                        principal.user_id,
                        updated_at,
                    ),
                )
                if added.rowcount:
                    db.audit(
                        action="project.model_catalog",
                        decision="ok",
                        workspace_id=principal.workspace_id,
                        user_id=principal.user_id,
                        target_ids=(scope, f"{choice['provider']}/{choice['model']}"),
                    )
                conn.execute(
                    "INSERT OR REPLACE INTO chat_model_settings "
                    "(workspace_id, scope, role, enabled, provider, model, base_url, key_ref, "
                    "updated_by, updated_at) VALUES (?, ?, ?, 1, ?, ?, NULL, NULL, ?, ?)",
                    (
                        principal.workspace_id,
                        scope,
                        role,
                        choice["provider"],
                        choice["model"],
                        principal.user_id,
                        updated_at,
                    ),
                )
        db.audit(
            action=action,
            decision="ok",
            workspace_id=principal.workspace_id,
            user_id=principal.user_id,
            target_ids=(scope,),
        )
        return get(db, principal.workspace_id, scope)


def _chosen(db, workspace_id: str, board: str, role: str) -> tuple[ModelChoice, str] | None:
    """The owner's own choice for a role: the project's, else the workspace's, with its source."""
    if role not in MODEL_ROLES:
        raise ValueError("Invalid project model role")
    with db._lock:
        row = db._conn.execute(
            "SELECT provider, model, scope FROM chat_model_settings "
            "WHERE workspace_id=? AND scope IN (?, '*') AND role=? "
            "ORDER BY CASE WHEN scope=? THEN 0 ELSE 1 END LIMIT 1",
            (workspace_id, board, role, board),
        ).fetchone()
    if row is None:
        return None
    return ModelChoice(row["provider"], row["model"]), "project" if row["scope"] == board else (
        "workspace"
    )


def resolve(db, workspace_id: str, board: str, role: str) -> ModelChoice | None:
    """The owner's own choice for a role (project, then workspace); ``None`` when none is set.

    A start never calls this alone: ``effective`` adds the documented default."""
    chosen = _chosen(db, workspace_id, board, role)
    return chosen[0] if chosen is not None else None


def default_choice(db, workspace_id: str, board: str) -> ModelChoice | None:
    """The documented default for a role with no choice: the first model of the project's list.

    The list is the project's own models plus the workspace's, in provider then model order
    (``catalog``). ``None`` only when the list is empty."""
    listed = catalog(db, workspace_id, board)
    return listed[0] if listed else None


def effective(db, workspace_id: str, board: str, role: str) -> Effective:
    """The model a start of ``role`` runs on and where it came from.

    ``project`` or ``workspace`` for the owner's choice, ``default`` for the first listed model,
    ``none`` when there is no usable model at all (a start is then refused and Needs me asks)."""
    chosen = _chosen(db, workspace_id, board, role)
    if chosen is not None:
        return Effective(chosen[0], chosen[1])
    fallback = default_choice(db, workspace_id, board)
    return Effective(fallback, "default" if fallback is not None else "none")


def effective_roles(db, workspace_id: str, board: str) -> dict[str, Effective]:
    """``effective`` for each configurable slot, in slot order."""
    return {role: effective(db, workspace_id, board, role) for role in MODEL_ROLES}


def workspace_roles(db, workspace_id: str) -> dict[str, Effective]:
    """What a project with no choice of its own runs on, per role: the workspace's choice
    (``workspace``), else the first model of the workspace's list (``default``), else ``none``."""
    chosen = get(db, workspace_id, WORKSPACE)
    fallback = default_choice(db, workspace_id, WORKSPACE)
    return {
        role: Effective(choice, "workspace")
        if choice is not None
        else Effective(fallback, "default" if fallback is not None else "none")
        for role, choice in chosen.items()
    }


def describe(item: Effective) -> str:
    """One labelled line for what a role runs on: chosen (project or workspace), default or none."""
    if item.choice is None:
        return "none (no model is set and the project's model list is empty; see Needs me)"
    name = f"`{item.choice.provider}/{item.choice.model}`"
    return {
        "project": f"{name} (chosen for this project)",
        "workspace": f"{name} (chosen for the workspace)",
        "default": f"{name} (default: the first model in the project's list; none was chosen)",
    }[item.source]

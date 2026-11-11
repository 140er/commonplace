"""Organigram: the reporting line between a config's roles, and what follows it.

The data is the ``parent`` key of ``[roles.<name>]`` (``commonplace.orch.schema``
refuses an unknown parent and a cycle, so the lines form a forest). A role reports
to its parent; a role without a parent reports to the workspace owner.

Pure helpers (a validated ``schema.Config`` in, names out):

- ``reports_to(config, role)``, ``line(config, role)``, ``direct_reports(config, role)``
- ``escalation_target(config, role)``: the parent role, or ``OWNER`` at the top

Two places respect the line:

- Escalation: ``escalations_in`` lists the unsettled steps (``in_doubt``,
  ``needs_person``) of a role's direct reports; the checkpoint of the role they
  report to carries them. The reporting role's own checkpoint is unchanged.
- Delegation: ``reassign_step`` lets the lease holder of the role a step's owner
  reports to take that step back (void it, history kept) so the work can be given
  out again. Anyone else is refused; the owner keeps ``orch_void_step``. Steps that
  start a job, gate a head or hold a card stay owner-only (``OWNER_KINDS``).

A config without ``parent`` keys has no lines: no checkpoint gains a key and every
``reassign_step`` is refused with ``not_in_line``.
"""

from __future__ import annotations

from . import durable, schema

OWNER = "owner"
ESCALATED_STATES = ("in_doubt", "needs_person")
# A runner start cancels a job, a gate void reopens a head, a hold guards a card.
OWNER_KINDS = frozenset({"runner_start", "gate", "hold"})


def reports_to(config: schema.Config, role: str) -> str | None:
    """The role ``role`` reports to; ``None`` at the top of a line or for an unknown role."""
    found = config.roles.get(role)
    return None if found is None else found.parent


def line(config: schema.Config, role: str) -> tuple[str, ...]:
    """The reporting line above ``role``, nearest first (empty at the top)."""
    out = []
    current = reports_to(config, role)
    while current is not None and current not in out:
        out.append(current)
        current = reports_to(config, current)
    return tuple(out)


def direct_reports(config: schema.Config, role: str) -> tuple[str, ...]:
    """The roles that report directly to ``role``, sorted by name."""
    return tuple(sorted(name for name, r in config.roles.items() if r.parent == role))


def escalation_target(config: schema.Config, role: str) -> str:
    """Where a question or blocked result of ``role`` goes: its parent role, else ``OWNER``."""
    return reports_to(config, role) or OWNER


def escalations_in(conn, config: schema.Config, board: str, role: str) -> list[dict] | None:
    """Unsettled, unvoided steps of the roles reporting directly to ``role``, oldest
    first (at most ``durable.MAX_LIST``, the newest kept). ``None`` when nobody reports
    to ``role``, so such a checkpoint keeps its shape."""
    reports = direct_reports(config, role)
    if not reports:
        return None
    marks = ",".join("?" * len(reports))
    rows = conn.execute(
        "SELECT s.*, j.state AS job_state FROM orch_steps s "
        "LEFT JOIN runner_jobs j ON j.id=s.job_id "
        f"WHERE s.board=? AND s.role IN ({marks}) AND s.voided_at IS NULL ORDER BY s.id",
        (board, *reports),
    ).fetchall()
    out = []
    for row in rows:
        data = dict(row)
        job_state = data.pop("job_state")
        step = durable._derived(durable.step_public(data), job_state)
        if step["state"] in ESCALATED_STATES:
            out.append(step)
    return out[-durable.MAX_LIST :]


def reassign_step(
    ctx, board: str, role: str, epoch: int, token: str, report: str, step_key: str, *, now=None
) -> dict:
    """``orch_reassign_step``: the lease holder of ``role`` takes back step ``step_key``
    of ``report``, a role that reports directly to ``role`` in the live config.

    The step is voided (history kept) and returned; a repeat returns the first void
    unchanged. ``not_in_line`` (403) unless ``report`` reports to ``role``;
    ``owner_only_kind`` (403) for ``OWNER_KINDS``; ``step_not_found`` (404)."""
    now = durable._clock(now)
    durable._check_role(report)
    durable._check_step_key(step_key)
    st, version = durable._holder(ctx, board, role)
    config = None if version is None else version.config()
    if config is None or report not in config.roles:
        raise durable.DurableError("unknown_role", 404, "role not found")
    if reports_to(config, report) != role:
        raise durable.DurableError(
            "not_in_line", 403, "only the role this role reports to, or the owner, reassigns"
        )
    actor = durable._actor_ref(ctx.principal)
    with st.transaction() as conn:
        durable._same_version(conn, board, version)
        durable._fence(conn, board, role, epoch, token, now)
        step = durable.step_in(conn, board=board, role=report, step_key=step_key)
        if step is None:
            raise durable.DurableError("step_not_found", 404, "step not found")
        if step["kind"] in OWNER_KINDS:
            raise durable.DurableError(
                "owner_only_kind", 403, "only the workspace owner voids this kind of step"
            )
        if step["voided"]:
            return step
        conn.execute(
            "UPDATE orch_steps SET voided_at=?, voided_by=?, updated_at=? "
            "WHERE board=? AND role=? AND step_key=?",
            (now, actor, now, board, report, step_key),
        )
        durable._log(conn, board, report, step_key, None, "reassign", actor, now)
        return durable.step_in(conn, board=board, role=report, step_key=step_key)

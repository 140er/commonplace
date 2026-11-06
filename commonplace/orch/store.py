"""Orchestrator config versions, owner-only accept and the restore flag.

Everything lives in the workspace's ``runner.sqlite3`` (the runner's job store), in the
tables of ``schema.sql``. Board key = board slug.

Versions: every board has ``v1, v2, ...`` with a ``parent_version``, a ``channel``
(``live`` or ``exp:<name>``), an optional unverified ``source_ref`` and the sha256 of the
body. Wing writers propose (``status='proposed'``); the orchestrator owner accepts,
rejects, retires an experiment or writes a version directly (accepted at once). At most
one accepted row per (board, channel); accepting supersedes the previous one. Only
accepted versions run.

Cursor rows (``orch_cursor``/``orch_cursor_gen``): a live accept that makes the board
configured (``[runner]`` present, where it was not) sets ``cursor=NULL, gen=gen+1``
("arming"); one that removes ``[runner]`` deletes the cursor row; one that keeps it does
not touch the cursor. Arming (reading ``boards.sqlite3``) and the sweep belong to the
triggers module.

Restore: ``commonplace.backup.restore()`` leaves ``RESTORE_MARKER`` in every restored
workspace directory. The first ``Store`` open converts it into ``orch_state.restore_flag=1``
with a new incarnation (old lease tokens, bound to the incarnation, die), and deletes the
marker only after that commit. While the flag is set, lease claims, every config-driven
start and the sweep refuse (``check_restore_in``). Only ``restore_ack`` with the exact
text ``"effects after <T> may be unknown"`` clears it; it re-arms the cursor of every
board configured at that moment.

Authority: ``orch_owner`` (a signed-in person with role owner, or the local CLI). The
store does not know board wings: callers check wing read access (reads) and wing write
access (``propose``) before calling.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from commonplace.backup import RESTORE_MARKER
from commonplace.extensions import ToolError

from . import schema

SCHEMA_SQL = Path(__file__).with_name("schema.sql")
DB_NAME = "runner.sqlite3"
BOARD = None  # re.Pattern, board slug ``[a-z0-9][a-z0-9_-]{0,63}``
CHANNEL = None  # re.Pattern, ``live`` | ``exp:[a-z0-9-]{1,32}``
LIVE = "live"
MAX_OPEN_PER_BOARD = 20
MAX_OPEN_PER_PROPOSER = 5
MAX_EXPERIMENTS = 8
ACK_TEMPLATE = "effects after {after} may be unknown"
__all__ = ["RESTORE_MARKER"]


class OrchError(ToolError):
    """A refusal. ``code`` is stable; ``status`` is the HTTP status a surface should use;
    ``str()`` is ``"no: <message>"`` and never contains config text."""

    code: str
    status: int
    path: str


@dataclass(frozen=True)
class Version:
    board: str
    version: int
    parent_version: int | None
    channel: str
    status: str
    source_ref: str | None
    sha256: str
    body: str
    proposed_by: str
    proposer_trust: str
    decided_by: str | None
    decided_at: str | None
    created_at: str

    @property
    def ref(self) -> str:
        """``v<n>``."""
        raise NotImplementedError

    def config(self) -> schema.Config:
        """Parsed body (cached by text); raises ``schema.ConfigError``."""
        raise NotImplementedError

    def public(self, *, body: bool = False) -> dict:
        """JSON-safe dict for surfaces; the body only on request."""
        raise NotImplementedError


@dataclass(frozen=True)
class AcceptResult:
    version: Version  # the accepted row
    previous: Version | None  # superseded accepted row of the same channel, if any
    changes: tuple[schema.Change, ...]  # schema.diff(previous or empty, new)
    cursor: str | None  # live only: "arming" | "dropped" | None (untouched)
    gen: int | None  # new generation when cursor == "arming"

    @property
    def arm_needed(self) -> bool:
        """True when the caller should run the triggers module's arm_cursor(board) now."""
        raise NotImplementedError


@dataclass(frozen=True)
class RestoreState:
    incarnation: str
    flagged: bool
    after: str | None  # T, "YYYY-MM-DDTHH:MM:SSZ" (backup time), while flagged
    ack_text: str | None  # exact text restore_ack needs, while flagged
    restored_at: str | None
    acked_by: str | None
    acked_at: str | None


def orch_owner(principal) -> bool:
    """``(person and role == "owner" and not a runner child) or auth == "local"``."""
    raise NotImplementedError


def require_owner(principal) -> None:
    """Raise ``OrchError('owner_only', 403)`` unless ``orch_owner(principal)``."""
    raise NotImplementedError


# ---------------------------------------------------- connection-level helpers
# For callers that already hold a connection (or a BEGIN IMMEDIATE transaction) on the
# same runner.sqlite3, e.g. fenced_start, lease claims and the sweep.


def accepted_in(conn, board: str, channel: str = LIVE) -> Version | None:
    """The accepted version of ``(board, channel)`` or ``None``."""
    raise NotImplementedError


def configured_in(conn, board: str) -> bool:
    """True when the board's accepted live config has ``[runner]``.

    A stored live body that no longer parses counts as configured (fail closed)."""
    raise NotImplementedError


def configured_boards_in(conn) -> list[str]:
    """Sorted slugs of every configured board (the sweep scope)."""
    raise NotImplementedError


def resolve_in(conn, board: str, config_ref: str | None = None) -> Version:
    """The accepted version a config-driven start on ``board`` runs.

    ``config_ref`` ``None``/``"live"`` -> live; ``"exp:<name>"`` -> that experiment, which
    must still pass ``schema.check_experiment`` against the current live config.
    Refusals: ``not_configured`` (no live config with ``[runner]``), ``invalid_channel``,
    ``unknown_experiment``, ``experiment_not_allowed``, ``config_invalid``."""
    raise NotImplementedError


def restore_state_in(conn) -> RestoreState:
    raise NotImplementedError


def check_restore_in(conn) -> None:
    """Raise ``OrchError('restore_pending', 409)`` while the restore flag is set."""
    raise NotImplementedError


def incarnation_in(conn) -> str:
    """Current incarnation (32 hex); changes when a restore marker is converted."""
    raise NotImplementedError


def cursor_in(conn, board: str) -> tuple[int | None, int] | None:
    """``(cursor, gen)`` of the board's cursor row, or ``None`` when there is none."""
    raise NotImplementedError


def reset_cursor_in(conn, board: str) -> int:
    """Set ``cursor=NULL, gen=last_gen+1`` (row re-created if missing); return the gen.
    Caller holds a write transaction."""
    raise NotImplementedError


def drop_cursor_in(conn, board: str) -> None:
    """Delete the board's cursor row; ``orch_cursor_gen`` keeps the last gen."""
    raise NotImplementedError


# ---------------------------------------------------------------------- store


class Store:
    """``runner.sqlite3`` in ``directory`` (a resolved workspace directory).

    Opening creates the tables (idempotent) and converts a restore marker.
    ``now`` returns ``"YYYY-MM-DDTHH:MM:SSZ"`` (injected in tests)."""

    def __init__(self, directory, *, now: Callable[[], str] | None = None):
        self.directory: Path
        self.path: str
        raise NotImplementedError

    @contextmanager
    def connection(self) -> Iterator:
        """Autocommit connection (``isolation_level=None``), Row factory, 10 s busy wait."""
        raise NotImplementedError

    @contextmanager
    def transaction(self) -> Iterator:
        """``BEGIN IMMEDIATE`` ... COMMIT, rollback on any exception."""
        raise NotImplementedError

    # reads
    def get(self, board: str, version: int) -> Version:
        """Raises ``not_found`` (404)."""
        raise NotImplementedError

    def versions(
        self, board: str, *, channel: str | None = None, status: str | None = None
    ) -> list[Version]:
        """Newest first."""
        raise NotImplementedError

    def accepted(self, board: str, channel: str = LIVE) -> Version | None:
        raise NotImplementedError

    def resolve(self, board: str, config_ref: str | None = None) -> Version:
        raise NotImplementedError

    def configured_boards(self) -> list[str]:
        raise NotImplementedError

    def diff(self, board: str, old: int, new: int) -> list[schema.Change]:
        """``orch diff vA vB``."""
        raise NotImplementedError

    # writes
    def propose(
        self,
        principal,
        board: str,
        text: str | bytes,
        *,
        connections: Iterable[str],
        channel: str = LIVE,
        sha256: str | None = None,
        source_ref: str | None = None,
    ) -> Version:
        """New ``proposed`` version. Caller has checked wing write access.

        ``text`` is parsed (``schema.parse``, sha256 checked first when given), planner
        connections are checked against ``connections`` (workspace connection names),
        ``config.project`` must equal ``board`` (``project_mismatch``); an experiment needs
        a live config and must pass ``check_experiment`` against it (``no_live``). At most
        20 open proposals per board and 5 per proposer (``too_many_open``). Stamps
        ``principal.actor`` and ``principal.policy.trust``."""
        raise NotImplementedError

    def write(
        self,
        principal,
        board: str,
        text: str | bytes,
        *,
        connections: Iterable[str],
        sha256: str,
        channel: str = LIVE,
        source_ref: str | None = None,
    ) -> AcceptResult:
        """Owner writes a version directly (accepted at once); same checks as propose +
        accept."""
        raise NotImplementedError

    def accept(
        self, principal, board: str, version: int, *, sha256: str, connections: Iterable[str]
    ) -> AcceptResult:
        """Owner accepts a ``proposed`` version whose sha256 equals ``sha256`` (the text the
        owner approved). Re-validates the body, connections and (exp) the allow-list
        against the current live config; at most 8 accepted experiments per board. The
        previous accepted row of the channel becomes ``superseded``; live cursor rules in
        the module docstring. Refusals: ``owner_only``, ``not_found``, ``not_proposed``,
        ``sha256_mismatch``, schema codes, ``too_many_experiments``, ``no_live``."""
        raise NotImplementedError

    def reject(self, principal, board: str, version: int) -> Version:
        """Owner rejects a ``proposed`` version."""
        raise NotImplementedError

    def retire(self, principal, board: str, channel: str) -> list[Version]:
        """Owner closes ``exp:<name>``: its accepted row -> superseded, its open proposals ->
        rejected. Returns the changed rows; ``unknown_experiment`` when nothing changed."""
        raise NotImplementedError

    # restore
    def restore_state(self) -> RestoreState:
        raise NotImplementedError

    def restore_ack(self, principal, text: str) -> list[tuple[str, int]]:
        """Owner clears the restore flag with the exact ``RestoreState.ack_text``.

        Every board configured now gets ``cursor=NULL, gen+1`` (arming) in the same
        transaction; returns ``[(board, gen), ...]``. Refusals: ``owner_only``,
        ``restore_not_pending``, ``restore_text``."""
        raise NotImplementedError


def open_store(gateway, workspace_id: str, **kwargs) -> Store:
    """``Store(gateway.workspace_dir(workspace_id))``, the runner job store's directory."""
    raise NotImplementedError

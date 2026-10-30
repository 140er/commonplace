"""Shared types for context handoff. Every part codes against these.

Change a type here only with the orchestrator: every module imports them.
Times are UTC ISO-8601 strings. Ids are opaque strings.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

# ── Defaults ──────────────────────────────────────────────────────────────
DEFAULT_THRESHOLD = 0.80
DEFAULT_CONTEXT_WINDOW = 200_000
THREADS_WING = "threads"  # the stored threads' wing for access checks
# Wing the mirror writes drawers into. A ``private_`` wing, so ordinary Memory
# tools show it only to tokens with ``private:read``, even under ``*``.
MIRROR_WING = "private_threads"
CHUNK_MAX_CHARS = 4_000  # chunks group whole messages up to this size

# ── Handoff states ───────────────────────────────────────────────────────
# pending → stored → launched → done
#        ↘ failed (store failed: no successor, client compacts)
# stored/launched → launch_failed (thread kept; `commonplace handoff relaunch`)
STATES = ("pending", "stored", "launched", "done", "failed", "launch_failed")

# ── Event names (audit action and webhook event) ─────────────────────────
EVENT_STARTED = "handoff.started"
EVENT_STORED = "handoff.stored"
EVENT_COMPLETED = "handoff.completed"  # successor registered
EVENT_FAILED = "handoff.failed"  # store failed, client falls back to compaction
EVENT_LAUNCH_FAILED = "handoff.launch_failed"
EVENT_SETTINGS = "handoff.settings_changed"
EVENTS = (
    EVENT_STARTED,
    EVENT_STORED,
    EVENT_COMPLETED,
    EVENT_FAILED,
    EVENT_LAUNCH_FAILED,
    EVENT_SETTINGS,
)

# Scope a token needs to read stored threads (thread_recall, thread_get, chain).
SCOPE_THREADS_READ = "threads:read"
# Scope a token needs to hand off (POST /api/handoff) and register a successor.
SCOPE_HANDOFF = "handoff:write"

LAUNCHER_KINDS = ("client", "command", "webhook")
SETTING_SCOPES = ("workspace", "agent", "task")


@dataclass(frozen=True)
class ThreadMessage:
    """One entry of a thread, verbatim.

    ``seq`` is 0-based and dense within a thread. ``kind`` is ``message``
    (user/assistant text), ``tool_call``, ``tool_result``, ``system`` or
    ``other``. ``text`` is the readable content exactly as written (tool
    calls: the tool's input as JSON). ``raw`` is the source line or object
    exactly as received, so nothing is lost even for kinds we don't model.
    """

    seq: int
    role: str  # user | assistant | tool | system
    kind: str
    text: str
    raw: str
    name: str = ""  # tool name for tool_call / tool_result
    call_id: str = ""  # links a tool_result to its tool_call
    at: str = ""  # source timestamp if the transcript had one

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ContextUsage:
    """How full the context is, from the transcript's last model call."""

    used_tokens: int
    window_tokens: int
    model: str = ""

    @property
    def fraction(self) -> float:
        return self.used_tokens / self.window_tokens if self.window_tokens else 0.0


@dataclass(frozen=True)
class LauncherConfig:
    """How a successor starts. ``client``: the calling hook starts it locally
    (default; the hub can't start processes on the agent's machine).
    ``command``: the hub runs ``argv`` (no shell) with placeholders
    ``{prompt}``, ``{prompt_file}``, ``{handoff_id}``, ``{thread_id}``, ``{cwd}``.
    ``webhook``: the hub POSTs the handoff to ``url`` and reads
    ``{"agent_ref", "url"}`` back."""

    kind: str = "client"
    argv: tuple = ()
    url: str = ""
    secret: str = ""  # HMAC key for the webhook launcher; never returned by the API

    def public(self) -> dict:
        return {"kind": self.kind, "argv": list(self.argv), "url": self.url}


@dataclass(frozen=True)
class HandoffSettings:
    """The setting that applies to one agent run, after resolution.

    ``source`` says which level decided ``enabled``: ``default``,
    ``workspace``, ``agent`` or ``task``.
    """

    enabled: bool = False
    threshold: float = DEFAULT_THRESHOLD
    context_window: Optional[int] = None  # None: from the model name
    launcher: LauncherConfig = field(default_factory=LauncherConfig)
    source: str = "default"


@dataclass(frozen=True)
class SettingRow:
    """One stored setting. ``None`` fields inherit from the level below."""

    workspace_id: str
    scope: str  # workspace | agent | task
    ref: str  # "" for workspace; connection/agent name; task ref like "#142"
    enabled: Optional[bool] = None
    threshold: Optional[float] = None
    context_window: Optional[int] = None
    launcher: Optional[LauncherConfig] = None
    updated_by: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class ThreadMeta:
    """What the client knows about the thread being handed off."""

    thread_id: str  # client's id (Claude session id, Codex rollout id, T3 thread id)
    agent: str = ""  # agent name; default: the connection name
    client: str = ""  # claude-code | codex | t3 | other
    model: str = ""
    task_ref: str = ""  # board card, e.g. "#142"
    branch: str = ""
    pr_url: str = ""
    cwd: str = ""
    parent_handoff_id: str = ""  # set when this thread is itself a successor


@dataclass(frozen=True)
class HandoffRequest:
    """``POST /api/handoff`` body, parsed. ``transcript`` is the raw text
    (JSONL or JSON) and ``transcript_format`` one of ``claude-code``,
    ``codex``, ``messages`` (JSON list of {role, content}) or ``auto``."""

    meta: ThreadMeta
    transcript: str
    transcript_format: str = "auto"
    usage: Optional[ContextUsage] = None
    note: str = ""  # optional words from the agent itself, stored verbatim
    idempotency_key: str = ""  # default: workspace + thread_id
    # The user's message the old session won't answer (Codex hands off at the
    # start of a turn): added to the successor prompt verbatim, not stored.
    next_message: str = ""


@dataclass(frozen=True)
class StoredThread:
    id: str  # "thr_…"; the pointer handed to the successor
    workspace_id: str
    source_thread_id: str
    message_count: int
    chunk_count: int
    sha256: str  # of the canonical message list
    wing: str = THREADS_WING
    created_at: str = ""
    created_by: str = ""  # actor ref, server-stamped


@dataclass(frozen=True)
class HandoffDoc:
    """The short handoff. Every field quotes the thread verbatim or comes
    from metadata; nothing is paraphrased."""

    goal: str
    status: str
    open_points: tuple = ()
    decisions: tuple = ()
    branch: str = ""
    pr_url: str = ""
    task_ref: str = ""
    note: str = ""
    stored_thread_id: str = ""
    handoff_id: str = ""
    chain: tuple = ()  # older stored thread ids, newest first


@dataclass(frozen=True)
class SuccessorRef:
    agent_ref: str  # e.g. "claude:session-uuid" or "t3:thread-id"
    url: str = ""
    thread_id: str = ""  # successor's own thread id if known


@dataclass(frozen=True)
class Handoff:
    id: str  # "hof_…"
    workspace_id: str
    idempotency_key: str
    source_thread_id: str
    state: str
    agent: str = ""
    task_ref: str = ""
    branch: str = ""
    pr_url: str = ""
    stored_thread_id: str = ""
    parent_handoff_id: str = ""
    doc_markdown: str = ""
    successor: Optional[SuccessorRef] = None
    error: str = ""
    connection_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    cwd: str = ""  # the old agent's working directory, from ThreadMeta.cwd


@dataclass(frozen=True)
class HandoffResult:
    """What the API returns to the client hook.

    ``action``: ``handoff`` (stop this agent; a successor is or will be
    running), ``compact`` (do nothing special: handoff is off or failed).
    ``launch`` is true only for the one response that must start the
    successor itself (client launcher); retries get false.
    """

    action: str
    handoff: Optional[Handoff] = None
    launch: bool = False
    prompt: str = ""  # successor's first message when launch is true
    reason: str = ""


@dataclass(frozen=True)
class RecallHit:
    stored_thread_id: str
    chunk_index: int
    seq_start: int
    seq_end: int
    score: float  # higher is better (FTS: the negated SQLite bm25)
    text: str  # verbatim chunk text

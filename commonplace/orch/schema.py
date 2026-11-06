"""Orchestrator setup as code: the ``commonplace.orchestrator/1`` TOML format.

This module is pure: it parses, validates, renders and diffs one config file
and applies the experiment rule. It reads no database and no workspace state;
the store, runner, checks and surfaces call it.

Public API (stable for the other orchestrator modules):

- ``parse(text, *, expected_sha256=None) -> Config``
- ``validate(data) -> Config`` (a dict already loaded from TOML)
- ``sha256_text(text) -> str`` and ``verify_sha256(text, expected) -> str``
- ``check_connections(config, known_names)`` (planner bindings vs workspace)
- ``template(name)``, ``expand_prompt(text)``, ``role_instructions(config, role)``
- ``render(config) -> str``
- ``diff(old, new) -> list[Change]`` and ``format_diff(changes) -> str``
- ``experiment_violations(live, exp) -> list[str]`` and ``check_experiment(live, exp)``

Every failure is a ``ConfigError`` with a stable ``code`` and a dotted
``path``. Messages are fixed text: no config value is ever placed in an
error, so an inline secret is refused without being echoed.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

SCHEMA_ID = "commonplace.orchestrator/1"
CONFIG_PATH = ".commonplace/orchestrator.toml"

KINDS = ("orchestrator", "sub-orchestrator", "planner", "builder", "reviewer")
HARNESSES = ("t3", "claude-code", "codex")
RUNNER_HARNESS = "t3"
MODEL_ROLES = ("main", "worker")  # same slots as commonplace.projects.types.MODEL_ROLES
RUNTIME_MODES = ("approval-required", "auto-accept-edits", "auto", "full-access")
TRIGGER_EVENTS = ("card.todo",)
TEMPLATES = ("design_before_code", "resume_protocol")
DESIGN_TEMPLATE = "design_before_code"

# Fields an experiment channel may change; everything else must equal live.
EXPERIMENT_ROLE_FIELDS = frozenset({"prompt", "workflow", "topic", "model_role", "skills"})
EXPERIMENT_TOP_LEVEL = frozenset({"workflows"})

MAX_CONFIG_BYTES = 128 * 1024
MAX_ROLES = 16
MAX_PROMPT_BYTES = 32 * 1024
MAX_LAUNCH_CHARS = 120_000  # T3 thread launch message limit
SPAWN_RESERVE_CHARS = 1_024  # room kept for the runner's own SPAWN_MESSAGE
MAX_AGENTS_RANGE = (1, 20)  # same bounds as project_limits.max_agents
MAX_WORKFLOWS = 32
MAX_STEPS = 64
MAX_STEP_CHARS = 500
MAX_SKILLS = 32
MAX_MCPS = 16
MAX_CONNECTIONS = 16
MAX_TRIGGERS = 4

NAME = re.compile(r"[a-z0-9-]{1,32}")  # role, workflow and mcp names
PROJECT = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")  # as commonplace.runner.registry.PROJECT
TOPIC = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}")  # as registry.TOPIC
MACHINE_ID = re.compile(r"mach_[0-9a-f]{16}")  # as registry.MACHINE_ID
SECRET_REF = re.compile(r"env:[A-Z][A-Z0-9_]{1,63}")
SKILL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
CONNECTION_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,119}")  # as control_db
CONNECTION_ID = re.compile(r"conn_[0-9a-f]{16}")  # control_db._new_id("conn")
SHA256_HEX = re.compile(r"[0-9a-f]{64}")
INCLUDE = re.compile(r"\{\{include:([^{}]*)\}\}")

# Error codes (ConfigError.code). Callers map them; tests assert on them.
CODES = frozenset(
    {
        "config_too_large",  # > MAX_CONFIG_BYTES
        "not_utf8",
        "toml_invalid",
        "sha256_invalid",  # claimed sha256 is not 64 lowercase hex
        "sha256_mismatch",  # claimed sha256 differs from the text's
        "schema_unsupported",
        "unknown_key",
        "missing_key",
        "wrong_type",
        "invalid_value",  # pattern, range or enum failure
        "too_many",  # roles, workflows, steps, skills, mcps, connections, triggers
        "prompt_too_large",
        "launch_too_large",
        "unknown_template",
        "unknown_role",
        "unknown_workflow",
        "unknown_mcp",
        "parent_cycle",
        "harness_not_allowed",
        "runner_harness_not_t3",
        "runner_role_incomplete",  # runner role lacks topic or model_role
        "limits_required",  # [runner] present without limits.max_agents
        "trigger_without_runner",
        "inline_secret",
        "planner_missing_design_before_code",
        "connections_not_planner",
        "connection_id_shaped",
        "connection_unknown",
        "experiment_not_allowed",
    }
)


class ConfigError(ValueError):
    """A config the orchestrator refuses. ``str()`` never contains a config value."""

    def __init__(self, code: str, path: str = "", message: str = ""):
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{code}: {path or '<config>'}: {message}" if message else code)


@dataclass(frozen=True)
class Skill:
    id: str
    version: int


@dataclass(frozen=True)
class Role:
    name: str
    kind: str
    parent: str | None = None
    harness: str | None = None
    topic: str | None = None
    model_role: str | None = None
    workflow: str | None = None
    prompt: str | None = None
    skills: tuple[Skill, ...] = ()
    mcps: tuple[str, ...] = ()
    connections: tuple[str, ...] = ()


@dataclass(frozen=True)
class Workflow:
    name: str
    steps: tuple[str, ...]


@dataclass(frozen=True)
class Mcp:
    name: str
    url: str
    secret_ref: str | None = None


@dataclass(frozen=True)
class Runner:
    role: str
    machine: str
    project: str
    runtime_mode: str


@dataclass(frozen=True)
class Trigger:
    on: str


@dataclass(frozen=True)
class Config:
    """One validated config. ``sha256`` is set by ``parse`` (raw text), else ``None``."""

    schema: str
    project: str
    max_agents: int | None
    roles: Mapping[str, Role]
    workflows: Mapping[str, Workflow] = field(default_factory=dict)
    mcps: Mapping[str, Mcp] = field(default_factory=dict)
    runner: Runner | None = None
    triggers: tuple[Trigger, ...] = ()
    sha256: str | None = None

    @property
    def has_runner(self) -> bool:
        """True when the config has ``[runner]``: the board is "configured"."""
        return self.runner is not None

    @property
    def runner_role(self) -> Role | None:
        """The role the runner launches, or ``None`` without ``[runner]``."""
        return self.roles[self.runner.role] if self.runner else None

    @property
    def planner_connections(self) -> tuple[str, ...]:
        """Connection names of every planner role, in role order, deduplicated."""
        raise NotImplementedError

    def to_dict(self) -> dict[str, Any]:
        """Canonical plain dict (no ``None`` fields, no sha256); input of ``diff``."""
        raise NotImplementedError


@dataclass(frozen=True)
class Change:
    """One diff entry. ``path`` is dotted (``roles.main.prompt``); op add/remove/change."""

    path: str
    op: str
    old: Any = None
    new: Any = None


def sha256_text(text: str | bytes) -> str:
    """Lowercase hex sha256 of the UTF-8 bytes the owner approves."""
    raise NotImplementedError


def verify_sha256(text: str | bytes, expected: str) -> str:
    """Return the text's sha256; raise ``sha256_invalid`` / ``sha256_mismatch``."""
    raise NotImplementedError


def parse(text: str | bytes, *, expected_sha256: str | None = None) -> Config:
    """Size check, optional sha256 check (before parsing), TOML load, ``validate``."""
    raise NotImplementedError


def validate(data: Mapping[str, Any]) -> Config:
    """Validate a loaded TOML document and return the typed ``Config``."""
    raise NotImplementedError


def check_connections(config: Config, known_names: Iterable[str]) -> None:
    """Raise ``connection_unknown`` when a planner connection is not in ``known_names``."""
    raise NotImplementedError


def template(name: str) -> str:
    """Text of a shipped template (``TEMPLATES``); ``unknown_template`` otherwise."""
    raise NotImplementedError


def expand_prompt(text: str) -> str:
    """Replace every ``{{include:<name>}}`` with its template text."""
    raise NotImplementedError


def role_instructions(config: Config, role: str) -> str:
    """Expanded prompt plus numbered workflow steps; the runner prepends SPAWN_MESSAGE."""
    raise NotImplementedError


def render(config: Config) -> str:
    """Markdown bundle of the whole config for advisory sessions and ``orch render``."""
    raise NotImplementedError


def diff(old: Config, new: Config) -> list[Change]:
    """Structural diff of ``to_dict()`` forms, sorted by path; lists compare whole."""
    raise NotImplementedError


def format_diff(changes: Iterable[Change]) -> str:
    """Human-readable diff (shown on accept); multi-line strings as unified diffs."""
    raise NotImplementedError


def experiment_violations(live: Config, exp: Config) -> list[str]:
    """Paths an experiment changes outside the allow-list; empty when allowed."""
    raise NotImplementedError


def check_experiment(live: Config, exp: Config) -> None:
    """Raise ``experiment_not_allowed`` (path = first violation) unless allowed."""
    raise NotImplementedError

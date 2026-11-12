"""Factory policy as data: the optional ``[factory]`` section of an orchestrator config.

The section declares what guards a merge on this board: whether a gate is required,
which checks must pass and which of their outcomes count as a pass, and how often the
full suite runs. It is part of the versioned config, so the owner accepts it by sha256
and every reader gets it from the accepted version.

This module is pure, like ``schema``: ``parse(value)`` validates the loaded ``[factory]``
table and returns a ``Factory``; ``Factory.to_dict()`` is its canonical form (config
diff, ``orch_config_get``, checkpoint) and ``Factory.summary()`` the plain-language
lines shown by ``orch render`` and the app. Failures are ``schema.ConfigError`` with
fixed text; a policy that contradicts itself is ``factory_contradiction``.

A config without ``[factory]`` has no policy and nothing here runs for it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .schema import ConfigError, _list, _names, _str, _table

MAX_CHECKS = 16
MAX_OUTCOMES = 16
LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}")  # display name of the gate
OUTCOME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}")  # a check result word
BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,99}")
SCOPES = ("affected", "full")  # tests a per-PR check runs
FULL_SUITE = ("per-pr", "per-batch", "off")
ON_RED = ("hold-merges", "report")
# Outcomes no policy may count as a pass, whatever the check is called.
NEVER_PASS = frozenset({"timeout", "skip", "skipped", "error"})
_ALWAYS_LISTED = ("TIMEOUT", "SKIP", "ERROR")  # named in every summary


@dataclass(frozen=True)
class Check:
    name: str
    required: bool
    scope: str
    passes: tuple[str, ...]
    never_pass: tuple[str, ...] = ()


@dataclass(frozen=True)
class Batch:
    branch: str
    full_suite: str
    on_red: str | None = None


@dataclass(frozen=True)
class Factory:
    gate_required: bool
    gate_name: str | None = None
    checks: tuple[Check, ...] = ()  # sorted by name
    batch: Batch | None = None

    def to_dict(self) -> dict:
        """Canonical plain dict in the shape of the TOML section (no ``None`` fields)."""
        gate: dict = {"required": self.gate_required}
        if self.gate_name is not None:
            gate["name"] = self.gate_name
        out: dict = {"gate": gate}
        if self.checks:
            out["checks"] = {
                check.name: {
                    "required": check.required,
                    "scope": check.scope,
                    "pass": list(check.passes),
                    **({"never_pass": list(check.never_pass)} if check.never_pass else {}),
                }
                for check in self.checks
            }
        if self.batch is not None:
            out["batch"] = {
                key: value
                for key in ("branch", "full_suite", "on_red")
                if (value := getattr(self.batch, key)) is not None
            }
        return out

    def summary(self) -> list[str]:
        """The policy in plain sentences, one per line, in a fixed order."""
        name = f" ({self.gate_name})" if self.gate_name else ""
        if self.gate_required:
            lines = [
                f"Gate{name}: required. Only a pass verdict counts; "
                "fail, error and budget never do."
            ]
        else:
            lines = [f"Gate{name}: not required."]
        for check in self.checks:
            tests = "the full suite" if check.scope == "full" else "affected tests"
            listed = {word.lower() for word in check.never_pass}
            never = [*check.never_pass, *(w for w in _ALWAYS_LISTED if w.lower() not in listed)]
            lines.append(
                f"Check {check.name} ({tests}): {'required' if check.required else 'optional'}. "
                f"Passes on {', '.join(check.passes)}. Never a pass: {', '.join(never)}."
            )
        if self.batch is not None:
            when = {
                "per-pr": "on every pull request",
                "per-batch": "once per batch of merges",
                "off": "not run",
            }[self.batch.full_suite]
            red = {
                "hold-merges": " A red run holds merges until it is green.",
                "report": " A red run is reported and does not hold merges.",
                None: "",
            }[self.batch.on_red]
            lines.append(f"Full suite on {self.batch.branch}: {when}.{red}")
        return lines


def _bool(value: object, path: str) -> bool:
    if type(value) is not bool:
        raise ConfigError("wrong_type", path, "must be true or false")
    return value


def _outcomes(value: object, path: str) -> tuple[str, ...]:
    out: dict[str, str] = {}
    for index, item in enumerate(_list(value, path, MAX_OUTCOMES)):
        word = _str(item, f"{path}[{index}]", OUTCOME)
        out.setdefault(word.lower(), word)
    return tuple(out.values())


def _check(name: str, value: object) -> Check:
    path = f"factory.checks.{name}"
    body = _table(value, path, ("required", "scope", "pass", "never_pass"), ("pass",))
    passes = _outcomes(body["pass"], f"{path}.pass")
    if not passes:
        raise ConfigError("invalid_value", f"{path}.pass", "needs at least one outcome")
    if any(word.lower() in NEVER_PASS for word in passes):
        raise ConfigError(
            "factory_contradiction",
            f"{path}.pass",
            "timeout, skip and error outcomes never count as a pass",
        )
    never = _outcomes(body.get("never_pass", []), f"{path}.never_pass")
    if {word.lower() for word in passes} & {word.lower() for word in never}:
        raise ConfigError(
            "factory_contradiction",
            f"{path}.never_pass",
            "an outcome cannot be both a pass and never a pass",
        )
    return Check(
        name=name,
        required=_bool(body["required"], f"{path}.required") if "required" in body else True,
        scope=(
            _str(body["scope"], f"{path}.scope", choices=SCOPES) if "scope" in body else "affected"
        ),
        passes=passes,
        never_pass=never,
    )


def _batch(value: object) -> Batch:
    body = _table(
        value, "factory.batch", ("branch", "full_suite", "on_red"), ("branch", "full_suite")
    )
    batch = Batch(
        branch=_str(body["branch"], "factory.batch.branch", BRANCH),
        full_suite=_str(body["full_suite"], "factory.batch.full_suite", choices=FULL_SUITE),
        on_red=(
            _str(body["on_red"], "factory.batch.on_red", choices=ON_RED)
            if "on_red" in body
            else None
        ),
    )
    if batch.full_suite == "off" and batch.on_red is not None:
        raise ConfigError(
            "factory_contradiction", "factory.batch.on_red", "no full suite runs, so none is red"
        )
    return batch


def parse(value: object) -> Factory:
    """Validate the loaded ``[factory]`` table; raise ``ConfigError`` on any refusal."""
    doc = _table(value, "factory", ("gate", "checks", "batch"), ("gate",))
    gate = _table(doc["gate"], "factory.gate", ("required", "name"), ("required",))
    checks = doc.get("checks", {})
    table = _table(checks, "factory.checks", checks if isinstance(checks, dict) else ())
    _names(table, "factory.checks", MAX_CHECKS)
    policy = Factory(
        gate_required=_bool(gate["required"], "factory.gate.required"),
        gate_name=_str(gate["name"], "factory.gate.name", LABEL) if "name" in gate else None,
        checks=tuple(_check(name, table[name]) for name in sorted(table)),
        batch=_batch(doc["batch"]) if "batch" in doc else None,
    )
    required = [check for check in policy.checks if check.required]
    if not policy.gate_required and not required:
        raise ConfigError(
            "factory_contradiction", "factory", "a policy needs a required gate or check"
        )
    full = [check for check in required if check.scope == "full"]
    suite = policy.batch.full_suite if policy.batch else None
    if suite == "per-batch" and full:
        raise ConfigError(
            "factory_contradiction",
            f"factory.checks.{full[0].name}.scope",
            "the full suite runs per batch, so no required check runs it per pull request",
        )
    if suite == "per-pr" and not full:
        raise ConfigError(
            "factory_contradiction",
            "factory.batch.full_suite",
            "per-pr needs a required check with scope = 'full'",
        )
    return policy

"""Aggregate-only wire contract. No source text is an allowed label or error."""

from __future__ import annotations

import json
import math
import re

VERSION = 1
MAX_BYTES = 4 * 1024 * 1024
MAX_THREADS = 200
MODELS = frozenset(
    "other gpt-6.1-sol gpt-6.1-terra gpt-6-astra gpt-6-sol gpt-6-luna "
    "claude-sonnet-5-5 claude-opus-4-6 claude-opus-4-7 claude-haiku-4-5 "
    "claude-sonnet-4-6 claude-sonnet-4-5".split()
)
TOOLS = frozenset("shell build test check file web delegate other compaction wait unknown".split())
PHASES = ("wait", "compaction", "tool", "unknown")
SCAN_SOURCES = frozenset(
    "runs native run_details items asks retries turns transfers pr_links logs".split()
)
STATUSES = frozenset(
    "queued preparing starting running waiting completed failed cancelled interrupted other".split()
)
REASONS = frozenset(
    "task_mapping_missing pr_snapshot_missing correlation_missing thinking_not_measured "
    "usage_missing context_missing edit_lines_unavailable edit_timing_missing no_samples no_timing "
    "partial_scan no_attribution not_recorded subagent_usage_unknown".split()
)
METRICS = tuple(
    "agent_hours runs_started runs_requested threads tasks_finished tasks_failed tasks_stopped "
    "tasks_running peak_agents prs_merged prs_open prs_closed tool_hours waiting_hours asks "
    "asks_answered asks_cancelled ask_typical_seconds ask_longest_seconds waiting_on_owner_hours "
    "chief_asks tool_calls failed_commands retries failed_hours no_run_gap_hours compactions "
    "context_over_90 turns_with_context thinking_hours lines_added lines_deleted tokens_input "
    "tokens_cached tokens_output cache_creation reasoning_tokens tasks_from_person tasks_from_agents "
    "handoffs".split()
)


class InvalidSnapshot(ValueError):
    def __init__(self):
        super().__init__("invalid_activity_snapshot")


def _object(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise InvalidSnapshot()


def _number(value, maximum=10**15):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= maximum:
        raise InvalidSnapshot()


def _integer(value, maximum=10**15):
    if type(value) is not int or not 0 <= value <= maximum:
        raise InvalidSnapshot()


def _stamp(value):
    _integer(value, 4_102_444_800)
    if value < 946_684_800:
        raise InvalidSnapshot()


def _identity(value, prefix):
    if type(value) is not str or not re.fullmatch(prefix + r"_[0-9a-f]{24}", value):
        raise InvalidSnapshot()


def _enum(value, values):
    if type(value) is not str or value not in values:
        raise InvalidSnapshot()


def _rows(value, limit):
    if type(value) is not list or len(value) > limit:
        raise InvalidSnapshot()
    return value


def _nullable_number(value):
    if value is not None:
        _number(value)


def _coverage(value):
    if type(value) is not dict:
        raise InvalidSnapshot()
    keys = (
        "runs_seen runs_scanned runs_timed runs_untimed items_seen items_timed turns_seen turns_complete turns_inclusive active_threads active_native "
        "native_agents native_agents_timed unknown_models unknown_tools threads_total threads_kept "
        "logs_read logs_skipped log_spans pr_links pr_unique pr_states metadata_ms detail_ms peak_rss_kib source_bytes log_bytes"
    ).split()
    _object(
        value,
        [
            *keys,
            "partial",
            "log_first",
            "log_last",
            "pr_first",
            "pr_last",
            "source_updated",
            *(["scans"] if "scans" in value else []),
        ],
    )
    for key in keys:
        _number(value[key])
    if type(value["partial"]) is not bool:
        raise InvalidSnapshot()
    for key in ("log_first", "log_last", "pr_first", "pr_last", "source_updated"):
        if value[key] is not None:
            _stamp(value[key])
    if value["runs_scanned"] > value["runs_seen"] or value["threads_kept"] > value["threads_total"]:
        raise InvalidSnapshot()
    if "scans" in value:
        _object(value["scans"], SCAN_SOURCES)
        for scan in value["scans"].values():
            _object(scan, ("scanned", "limit", "truncated"))
            _integer(scan["scanned"])
            _integer(scan["limit"], 500000)
            if scan["scanned"] > scan["limit"] or type(scan["truncated"]) is not bool:
                raise InvalidSnapshot()
            if scan["truncated"] and not value["partial"]:
                raise InvalidSnapshot()


def _summaries(value):
    for row in _rows(value["daily"], 33):
        _object(row, ("day", "seconds", "runs"))
        if type(row["day"]) is not str or not re.fullmatch(r"20\d\d-\d\d-\d\d", row["day"]):
            raise InvalidSnapshot()
        _number(row["seconds"])
        _integer(row["runs"])
    for row in _rows(value["models"], len(MODELS)):
        _object(
            row,
            "model runs seconds tool_seconds input cached output usage_turns complete_turns inclusive_turns".split(),
        )
        _enum(row["model"], MODELS)
        for key in (
            "runs",
            "seconds",
            "tool_seconds",
            "usage_turns",
            "complete_turns",
            "inclusive_turns",
        ):
            _number(row[key])
        for key in ("input", "cached", "output"):
            _nullable_number(row[key])
    for row in _rows(value["sinks"], 32):
        _object(row, "category seconds share count timed failed p95 thread project".split())
        _enum(row["category"], TOOLS | {"failed_run", "recorded_retry", "no_run_gap"})
        for key in ("seconds", "count", "timed", "failed"):
            _number(row[key])
        _nullable_number(row["p95"])
        _nullable_number(row["share"])
        if row["share"] is not None and row["share"] > 1:
            raise InvalidSnapshot()
        for key in ("thread", "project"):
            if row[key] is not None:
                _identity(row[key], key)


def _threads(value):
    ids = set()
    for row in _rows(value["threads"], MAX_THREADS):
        _object(
            row, "id project model status creator seconds runs first last phases buckets".split()
        )
        _identity(row["id"], "thread")
        if row["id"] in ids:
            raise InvalidSnapshot()
        ids.add(row["id"])
        if row["project"] is not None:
            _identity(row["project"], "project")
        _enum(row["model"], MODELS)
        _enum(row["status"], STATUSES)
        _enum(row["creator"], {"user", "agent", "system", "unknown"})
        for key in ("seconds", "runs"):
            _number(row[key])
        for key in ("first", "last"):
            _stamp(row[key])
        _object(row["phases"], PHASES)
        for phase in PHASES:
            _number(row["phases"][phase])
        previous = 0
        for bucket in _rows(row["buckets"], 128):
            _object(bucket, ("at", *PHASES, "calls"))
            _stamp(bucket["at"])
            if (
                not value["window_start"] <= bucket["at"] < value["window_end"]
                or bucket["at"] <= previous
            ):
                raise InvalidSnapshot()
            previous = bucket["at"]
            for key in (*PHASES, "calls"):
                _number(bucket[key])


def validate(value):
    """Validate both outbound and inbound data, rejecting even unknown nested keys."""
    _object(
        value,
        "schema collected_at window_start window_end timezone source metrics coverage daily models sinks threads handoffs spans".split(),
    )
    if type(value["schema"]) is not int or value["schema"] != VERSION:
        raise InvalidSnapshot()
    for key in ("collected_at", "window_start", "window_end"):
        _stamp(value[key])
    if not 0 < value["window_end"] - value["window_start"] <= 31 * 86400:
        raise InvalidSnapshot()
    if value["window_end"] > value["collected_at"] + 60:
        raise InvalidSnapshot()
    _enum(value["timezone"], {"Europe/Amsterdam"})
    _enum(value["source"], {"local"})
    _object(value["metrics"], METRICS)
    for metric in value["metrics"].values():
        _object(metric, ("value", "reason"))
        if metric["value"] is None:
            _enum(metric["reason"], REASONS)
        else:
            _number(metric["value"])
            if metric["reason"] is not None:
                raise InvalidSnapshot()
    _coverage(value["coverage"])
    _summaries(value)
    _threads(value)
    for row in _rows(value["handoffs"], len(MODELS) ** 2):
        _object(row, ("from", "to", "count"))
        _enum(row["from"], MODELS)
        _enum(row["to"], MODELS)
        _integer(row["count"])
    for row in _rows(value["spans"], 8):
        _object(row, ("category", "count", "seconds", "p95", "failed"))
        _enum(row["category"], {"sqlite", "transaction", "provider", "tool", "server", "other"})
        for key in ("count", "seconds", "failed"):
            _number(row[key])
        _nullable_number(row["p95"])
    if len(json.dumps(value, allow_nan=False).encode()) > MAX_BYTES:
        raise InvalidSnapshot()
    return value


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise InvalidSnapshot()
        result[key] = value
    return result


def loads(raw):
    if type(raw) is not bytes or len(raw) > MAX_BYTES:
        raise InvalidSnapshot()
    try:
        return validate(json.loads(raw, object_pairs_hook=_pairs))
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise InvalidSnapshot() from None

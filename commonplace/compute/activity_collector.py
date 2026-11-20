"""Home-side sparse collection. This module has no network or credential discovery."""

from __future__ import annotations

import hashlib
import json
import math
import resource
import shlex
import sqlite3
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, TypeGuard

from . import activity_math as timing, activity_schema as wire

PREFIX = "orchestration_v2_projection_"
ACTIVE = {"preparing", "starting", "running", "waiting"}
TOKEN_FIELDS = (
    "inputTokens",
    "cachedInputTokens",
    "outputTokens",
    "cacheCreationTokens",
    "reasoningTokens",
)


@dataclass(frozen=True)
class Limits:
    metadata: int = 50000
    runs: int = 2000
    items: int = 250000
    turns: int = 50000
    log_lines: int = 250000

    def __post_init__(self):
        if any(type(n) is not int or not 1 <= n <= 500000 for n in vars(self).values()):
            raise ValueError("invalid_activity_limits")


class SourceUnavailable(Exception):
    def __init__(self):
        super().__init__("activity_source_unavailable")


def identity(kind, value):
    return kind + "_" + hashlib.sha256((kind + ":" + str(value)).encode()).hexdigest()[:24]


def model(value):
    return value if type(value) is str and value in wire.MODELS else "other"


def _positive(value: Any) -> TypeGuard[int | float]:
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 10**15


def command_category(value):
    """Only a fixed category escapes this SQLite callback; arguments stay here."""
    if not isinstance(value, str) or len(value) > 16384:
        return "shell"
    try:
        if value.startswith("{"):
            data = json.loads(value)
            value = data.get("cmd", data.get("command", "")) if isinstance(data, dict) else ""
        words = shlex.split(value)
    except (ValueError, TypeError, RecursionError):
        return "shell"
    # Only executable positions are classified; echo arguments are not programs.
    if not words:
        return "shell"
    if words[:2] == ["uv", "run"]:
        words = words[2:]
    if not words:
        return "shell"
    executable = Path(words[0]).name
    if executable in {"python", "python3", "python3.12"} and len(words) > 2 and words[1] == "-m":
        executable = words[2]
    if executable in {"pytest", "vitest", "unittest"}:
        return "test"
    if executable in {"ruff", "mypy", "eslint"}:
        return "check"
    if executable in {"tsc", "make", "cmake"} or (
        executable == "cargo" and len(words) > 1 and words[1] == "build"
    ):
        return "build"
    return "shell"


def tool_category(value):
    if value in {"exec_command", "write_stdin", "functions.exec", "functions.exec_command"}:
        return "shell"
    if value in {"apply_patch", "read_file", "write_file", "Edit", "Read", "Write"}:
        return "file"
    if value in {"web.run", "web__run", "web_search", "WebFetch", "WebSearch"}:
        return "web"
    if value in {"delegate_task", "Agent", "Task", "spawn_agent"}:
        return "delegate"
    return "other"


def _diff_counts(value):
    if not isinstance(value, str):
        return None
    added = deleted = 0
    for line in value.splitlines():
        added += line.startswith("+") and not line.startswith("+++")
        deleted += line.startswith("-") and not line.startswith("---")
    return json.dumps([added, deleted])


def connect(path):
    """Live WAL is visible; no immutable mode, transaction, migration or copy."""
    db = sqlite3.connect(
        Path(path).absolute().as_uri() + "?mode=ro", uri=True, isolation_level=None
    )
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA cache_size=-4096")
    db.execute("PRAGMA mmap_size=0")
    db.create_function("activity_command", 1, command_category)
    db.create_function("activity_diff", 1, _diff_counts)
    db.create_function("activity_timestamp", 1, timing.timestamp, deterministic=True)
    return db


class Collector:
    def __init__(self, db, window, limits, progress):
        self.db, self.window, self.limits, self.progress = db, window, limits, progress
        self.runs: dict[str, dict[str, Any]] = {}
        self.thread_runs: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.agents: dict[str, dict[str, Any]] = {}
        self.thread_meta: dict[str, dict[str, Any]] = {}
        self.models: dict[str, dict[str, Any]] = {}
        self.sinks: dict[str, dict[str, Any]] = {}
        self.metrics: dict[str, dict[str, Any]] = {
            key: {"value": None, "reason": "not_recorded"} for key in wire.METRICS
        }
        self.coverage: dict[str, Any] = {
            key: 0
            for key in (
                "runs_seen runs_scanned runs_timed runs_untimed items_seen items_timed turns_seen turns_complete turns_inclusive active_threads active_native "
                "native_agents native_agents_timed unknown_models unknown_tools threads_total threads_kept "
                "logs_read logs_skipped log_spans pr_links pr_unique pr_states metadata_ms detail_ms peak_rss_kib source_bytes log_bytes"
            ).split()
        }
        self.coverage.update(
            partial=False,
            log_first=None,
            log_last=None,
            pr_first=None,
            pr_last=None,
            source_updated=None,
        )
        self.scans = {
            key: {"scanned": 0, "limit": limit, "truncated": False}
            for key, limit in (
                ("runs", limits.metadata),
                ("native", limits.metadata),
                ("run_details", limits.runs),
                ("items", limits.items),
                ("asks", limits.metadata),
                ("retries", limits.metadata),
                ("turns", limits.turns),
                ("transfers", limits.metadata),
                ("pr_links", limits.metadata),
                ("logs", limits.log_lines),
            )
        }
        self.coverage["scans"] = self.scans
        self.handoffs: Counter = Counter()
        self.calls: dict[str, list[float]] = defaultdict(list)
        self.daily = {day: {"day": day, "seconds": 0.0, "runs": 0} for day in timing.days(window)}
        self.bucket_seconds = max(3600, math.ceil((window[1] - window[0]) / 96 / 3600) * 3600)

    def put(self, key, value=None, reason="not_recorded"):
        self.metrics[key] = {"value": value, "reason": reason if value is None else None}

    def rows(self, sql, args=()):
        cursor = self.db.execute(sql, args)
        try:
            while batch := cursor.fetchmany(128):
                yield from batch
        finally:
            cursor.close()

    def truncate(self, source):
        self.scans[source]["truncated"] = True
        self.coverage["partial"] = True

    def bounded_rows(self, source, sql, args=(), *, per_query=None):
        """Budget eligible rows globally, then probe one omitted row without retaining it."""
        scan = self.scans[source]
        remaining = scan["limit"] - scan["scanned"]
        if per_query is not None:
            remaining = min(remaining, per_query)
        read = 0
        for row in self.rows(sql + " LIMIT ?", (*args, remaining)):
            scan["scanned"] += 1
            read += 1
            yield row
        if read == remaining:
            cursor = self.db.execute("SELECT 1 FROM (" + sql + ") LIMIT 1 OFFSET ?", (*args, read))
            try:
                if cursor.fetchone() is not None:
                    self.truncate(source)
            finally:
                cursor.close()

    def during(self, start, end, open_when="0", *, points=True):
        """The same parsed/clipped eligibility as the aggregate, before its SQL limit."""
        a, b = f"activity_timestamp({start})", f"activity_timestamp({end})"
        lo, hi = self.window
        overlap = f"({a} < {hi} AND {b} > {lo} AND {b} >= {a})"
        ongoing = f"({a} < {hi} AND {end} IS NULL AND ({open_when}))"
        point = f" OR ({lo} <= {a} AND {a} < {hi})" if points else ""
        return f"({overlap} OR {ongoing}{point})"

    def selected_ids(self, field, values):
        return f"{field} IN (SELECT value FROM json_each(?))", (json.dumps(list(values)),)

    def model_row(self, name):
        return self.models.setdefault(
            name,
            dict(
                model=name,
                runs=0,
                seconds=0.0,
                tool_seconds=0.0,
                input=None,
                cached=None,
                output=None,
                usage_turns=0,
                complete_turns=0,
                inclusive_turns=0,
            ),
        )

    def _agent(self, key, thread, name):
        return self.agents.setdefault(
            key,
            {"thread": thread, "model": name, "base": [], "wait": [], "tool": [], "compaction": []},
        )

    def metadata(self):
        begin = time.perf_counter()
        requested = started = 0
        start = "json_extract(r.payload_json,'$.startedAt')"
        eligible = self.during(
            start, "r.completed_at", "r.status IN ('preparing','starting','running','waiting')"
        )
        requested_in_window = f"activity_timestamp(r.requested_at) >= {self.window[0]} AND activity_timestamp(r.requested_at) < {self.window[1]}"
        sql = f"""SELECT r.run_id,r.thread_id,r.ordinal,r.status,r.requested_at,r.completed_at,
            json_extract(r.payload_json,'$.startedAt'),
            json_extract(r.payload_json,'$.modelSelection.model'),t.project_id,
            json_extract(t.payload_json,'$.createdBy'),t.updated_at
            FROM {PREFIX}runs r LEFT JOIN {PREFIX}threads t ON t.thread_id=r.thread_id
            WHERE {eligible} OR (r.status='queued' AND activity_timestamp(r.requested_at)<{self.window[1]})
            OR ({requested_in_window})"""
        for (
            rid,
            tid,
            ordinal,
            status,
            request,
            complete,
            start,
            raw_model,
            project,
            creator,
            updated,
        ) in self.bounded_rows("runs", sql):
            requested_at, start_at = timing.timestamp(request), timing.timestamp(start)
            if requested_at is not None and self.window[0] <= requested_at < self.window[1]:
                requested += 1
            interval = timing.clipped(
                start, complete, self.window, open_at=self.window[1] if status in ACTIVE else None
            )
            point = start_at is not None and self.window[0] <= start_at < self.window[1]
            queued = (
                status == "queued" and requested_at is not None and requested_at < self.window[1]
            )
            unknown_timing = (
                start_at is None
                and requested_at is not None
                and self.window[0] <= requested_at < self.window[1]
                and status != "queued"
            )
            if interval is None and not point and not queued and not unknown_timing:
                continue
            updated_at = timing.timestamp(updated)
            if updated_at is not None:
                self.coverage["source_updated"] = max(
                    self.coverage["source_updated"] or 0, int(updated_at)
                )
            anchor = start_at if start_at is not None else max(requested_at, self.window[0])
            if status != "queued":
                ended = timing.timestamp(complete)
                known = start_at is not None and (
                    ended is not None and ended >= start_at or status in ACTIVE and complete is None
                )
                self.coverage["runs_timed" if known else "runs_untimed"] += 1
            name = model(raw_model)
            self.coverage["unknown_models"] += name == "other"
            status = status if status in wire.STATUSES else "other"
            meta = self.thread_meta.setdefault(
                tid,
                dict(
                    id=identity("thread", tid),
                    project=identity("project", project)
                    if isinstance(project, str) and project
                    else None,
                    creator=creator if creator in {"user", "agent", "system"} else "unknown",
                    model=name,
                    status=status,
                    first=int(interval[0] if interval else anchor),
                    last=int(interval[1] if interval else anchor),
                    ordinal=-1,
                    runs=0,
                ),
            )
            if ordinal > meta["ordinal"]:
                meta.update(model=name, status=status, ordinal=ordinal)
            meta["runs"] += 1
            meta["first"] = min(meta["first"], int(interval[0] if interval else anchor))
            meta["last"] = max(meta["last"], int(interval[1] if interval else anchor))
            self.runs[rid] = dict(
                thread=tid, interval=interval, model=name, status=status, ordinal=ordinal
            )
            self.thread_runs[tid].append(self.runs[rid])
            agent = self._agent(tid, tid, name)
            if point:
                started += 1
                day = datetime.fromtimestamp(start_at, timing.ZONE).date().isoformat()
                self.daily[day]["runs"] += 1
                self.model_row(name)["runs"] += 1
            if interval:
                agent["base"].append(interval)
        self.coverage.update(
            runs_seen=len(self.runs),
            threads_total=len(self.thread_meta),
            active_threads=sum(
                m["status"] in ACTIVE | {"queued"} for m in self.thread_meta.values()
            ),
            metadata_ms=(time.perf_counter() - begin) * 1000,
        )
        self.put("runs_requested", requested)
        self.put("runs_started", started)
        self.put("threads", len(self.thread_meta))
        for key in (
            "tasks_finished",
            "tasks_failed",
            "tasks_stopped",
            "tasks_running",
            "tasks_from_person",
            "tasks_from_agents",
        ):
            self.put(key, reason="task_mapping_missing")
        for key in ("prs_open", "prs_closed", "prs_merged"):
            self.put(key, reason="pr_snapshot_missing")
        for key in ("chief_asks", "waiting_on_owner_hours"):
            self.put(key, reason="correlation_missing")
        self.put("thinking_hours", reason="thinking_not_measured")
        if self.progress:
            self.progress("metadata", len(self.runs))

    def subagents(self):
        selected, args = self.selected_ids("thread_id", self.thread_meta)
        runs, run_args = self.selected_ids("run_id", self.runs)
        eligible = self.during(
            "started_at",
            "completed_at",
            "status IN ('preparing','starting','running','waiting')",
            points=False,
        )
        sql = f"""SELECT subagent_id,thread_id,child_thread_id,status,started_at,completed_at,run_id,
            json_extract(payload_json,'$.modelSelection.model') FROM {PREFIX}subagents
            WHERE {selected} AND ({eligible} OR (activity_timestamp(started_at) IS NULL AND {runs}))"""
        for sid, tid, child, status, start, end, rid, raw_model in self.bounded_rows(
            "native", sql, (*args, *run_args)
        ):
            if tid not in self.thread_meta or (child in self.agents and self.agents[child]["base"]):
                continue
            interval = timing.clipped(
                start, end, self.window, open_at=self.window[1] if status in ACTIVE else None
            )
            if interval is None and rid not in self.runs:
                continue
            self.coverage["native_agents"] += 1
            self.coverage["active_native"] += status in ACTIVE and timing.timestamp(end) is None
            if interval:
                self._agent("native:" + sid, tid, model(raw_model))["base"].append(interval)
                self.coverage["native_agents_timed"] += 1

    def _sink(self, category):
        return self.sinks.setdefault(
            category,
            {
                "category": category,
                "seconds": 0.0,
                "share": None,
                "count": 0,
                "failed": 0,
                "samples": [],
                "attribution": Counter(),
            },
        )

    def _sample(self, category, interval, failed, tid):
        sink = self._sink(category)
        sink["count"] += 1
        sink["failed"] += int(failed)
        if interval:
            seconds = interval[1] - interval[0]
            sink["seconds"] += seconds
            sink["samples"].append(seconds)
            sink["attribution"][tid] += seconds

    def items(self):
        self.put("tool_calls", 0)
        self.put("failed_commands", 0)
        self.put("compactions", 0)
        lines = [0, 0]
        edit_known = True
        start = "json_extract(payload_json,'$.startedAt')"
        end = "json_extract(payload_json,'$.completedAt')"
        eligible = self.during(
            start, end, "status IN ('preparing','starting','running','waiting','in_progress')"
        )
        for rid, run in sorted(
            self.runs.items(), key=lambda item: (item[1]["interval"] or (0, 0))[0], reverse=True
        ):
            if self.scans["run_details"]["scanned"] >= self.limits.runs:
                self.truncate("run_details")
                break
            sql = f"""SELECT type,status,{start},{end},
                json_extract(payload_json,'$.toolName'),json_extract(payload_json,'$.exitCode'),
                CASE WHEN type='command_execution' AND length(json_extract(payload_json,'$.input'))<=16384
                     THEN activity_command(json_extract(payload_json,'$.input')) ELSE NULL END,
                CASE WHEN type='file_change' AND length(json_extract(payload_json,'$.diffStr'))<=2097152
                     THEN activity_diff(json_extract(payload_json,'$.diffStr')) ELSE NULL END
                FROM {PREFIX}turn_items WHERE run_id=? AND {eligible}
                ORDER BY ordinal,turn_item_id"""
            for kind, status, start_at, end_at, tool, exit_code, command, diff in self.bounded_rows(
                "items", sql, (rid,)
            ):
                self.coverage["items_seen"] += 1
                if kind == "file_change":
                    at = timing.timestamp(start_at)
                    if at is None:
                        edit_known = False
                        continue
                    if not self.window[0] <= at < self.window[1]:
                        continue
                    if diff:
                        additions = json.loads(diff)
                        lines = [a + b for a, b in zip(lines, additions)]
                    else:
                        edit_known = False
                    continue
                if kind not in {"dynamic_tool", "command_execution", "compaction"}:
                    continue
                interval = timing.clipped(
                    start_at,
                    end_at,
                    self.window,
                    open_at=self.window[1] if status in ACTIVE or status == "in_progress" else None,
                )
                at = timing.timestamp(start_at)
                if interval is None and not (
                    at is not None and self.window[0] <= at < self.window[1]
                ):
                    continue
                tid = run["thread"]
                phase = "compaction" if kind == "compaction" else "tool"
                category = "compaction" if phase == "compaction" else command or tool_category(tool)
                if interval:
                    self.coverage["items_timed"] += 1
                    self.agents[tid][phase].append(interval)
                self.coverage["unknown_tools"] += category == "other"
                failed = status == "failed" or (type(exit_code) is int and exit_code != 0)
                self._sample(category, interval, failed, tid)
                if at is not None and self.window[0] <= at < self.window[1]:
                    self.calls[tid].append(at)
                key = "compactions" if phase == "compaction" else "tool_calls"
                self.metrics[key]["value"] += 1
                if kind == "command_execution" and failed:
                    self.metrics["failed_commands"]["value"] += 1
            self.scans["run_details"]["scanned"] += 1
            if not self.scans["items"]["truncated"]:
                self.coverage["runs_scanned"] += 1
            if self.progress and self.coverage["runs_scanned"] % 20 == 0:
                self.progress("runs", self.coverage["runs_scanned"])
        incomplete = self.scans["items"]["truncated"] or self.scans["run_details"]["truncated"]
        self.put(
            "lines_added",
            lines[0] if edit_known and not incomplete else None,
            "partial_scan" if incomplete else "edit_lines_unavailable",
        )
        self.put(
            "lines_deleted",
            lines[1] if edit_known and not incomplete else None,
            "partial_scan" if incomplete else "edit_lines_unavailable",
        )

    def asks_and_retries(self):
        samples = []
        count = answered = cancelled = 0
        selected, args = self.selected_ids("thread_id", self.thread_meta)
        eligible = self.during("created_at", "resolved_at", "status='pending'", points=False)
        sql = f"""SELECT thread_id,kind,status,created_at,resolved_at,
            json_extract(payload_json,'$.decision'),json_type(payload_json,'$.answers')='object'
            FROM {PREFIX}runtime_requests WHERE {selected} AND kind='user_input' AND {eligible}"""
        for (
            tid,
            kind,
            status,
            start,
            end,
            decision,
            has_answers,
        ) in self.bounded_rows("asks", sql, args):
            if tid not in self.thread_meta or kind != "user_input":
                continue
            interval = timing.clipped(
                start, end, self.window, open_at=self.window[1] if status == "pending" else None
            )
            if not interval:
                continue
            count += 1
            is_cancelled = status == "cancelled" or decision in {"cancel", "cancelled", "deny"}
            is_answered = status == "resolved" and bool(has_answers) and not is_cancelled
            cancelled += is_cancelled
            answered += is_answered
            self.agents[tid]["wait"].append(interval)
            self._sample("wait", interval, False, tid)
            if is_answered:
                samples.append(interval[1] - interval[0])
        for key, value in (
            ("asks", count),
            ("asks_answered", answered),
            ("asks_cancelled", cancelled),
        ):
            self.put(key, value)
        self.put("ask_typical_seconds", timing.percentile(samples, 0.5), "no_samples")
        self.put("ask_longest_seconds", max(samples) if samples else None, "no_samples")
        retries = 0
        selected, args = self.selected_ids("run_id", self.runs)
        eligible = self.during(
            "json_extract(payload_json,'$.startedAt')",
            "json_extract(payload_json,'$.completedAt')",
            "status IN ('preparing','starting','running','waiting')",
            points=False,
        )
        sql = f"""SELECT run_id,json_extract(payload_json,'$.startedAt'),
            json_extract(payload_json,'$.completedAt'),json_extract(payload_json,'$.reason'),status
            FROM {PREFIX}run_attempts WHERE {selected} AND json_extract(payload_json,'$.reason')='retry'
            AND {eligible}"""
        for (
            rid,
            start,
            end,
            reason,
            status,
        ) in self.bounded_rows("retries", sql, args):
            if rid not in self.runs or reason != "retry":
                continue
            interval = timing.clipped(
                start, end, self.window, open_at=self.window[1] if status in ACTIVE else None
            )
            if interval:
                retries += 1
                self._sample(
                    "recorded_retry", interval, status == "failed", self.runs[rid]["thread"]
                )
        self.put("retries", retries)

    def tokens(self):
        totals: list[int | float] = [0] * len(TOKEN_FIELDS)
        known = [0] * len(TOKEN_FIELDS)
        context = high = 0
        fields = ",".join(
            f"json_extract(p.payload_json,'$.turnTokenUsage.{key}')" for key in TOKEN_FIELDS
        )
        for tid in self.thread_meta:
            sql = f"""SELECT p.started_at,p.completed_at,p.run_attempt_id,
                json_extract(p.payload_json,'$.turnTokenUsage.usageScope'),
                json_extract(p.payload_json,'$.turnTokenUsage.usageStatus'),
                json_extract(p.payload_json,'$.turnTokenUsage.hasSubagents'),{fields},
                json_extract(p.payload_json,'$.tokenUsage.usedTokens'),json_extract(p.payload_json,'$.tokenUsage.maxTokens'),
                a.run_id FROM {PREFIX}provider_turns p LEFT JOIN {PREFIX}run_attempts a ON a.attempt_id=p.run_attempt_id
                WHERE p.thread_id=? AND activity_timestamp(p.started_at)>={self.window[0]}
                AND activity_timestamp(p.started_at)<{self.window[1]} ORDER BY p.ordinal,p.provider_turn_id"""
            for row in self.bounded_rows("turns", sql, (tid,)):
                start, end, _attempt, scope, status, has_children, *tail = row
                values, used, maximum, rid = tail[:5], tail[5], tail[6], tail[7]
                a, b = timing.timestamp(start), timing.timestamp(end)
                if a is None or not self.window[0] <= a < self.window[1]:
                    continue
                self.coverage["turns_seen"] += 1
                name = self.runs.get(rid, {}).get("model", "other")
                mr = self.model_row(name)
                mr["usage_turns"] += 1
                if _positive(used) and _positive(maximum) and maximum > 0:
                    context += 1
                    high += used / maximum > 0.9
                # Main-only deltas can be summed once per unique provider turn. A
                # clipped turn has no attributable token fraction and is excluded.
                if scope != "main_agent" or status != "complete" or b is None or b > self.window[1]:
                    continue
                self.coverage["turns_complete"] += 1
                mr["complete_turns"] += 1
                self.coverage["turns_inclusive"] += has_children == 1
                mr["inclusive_turns"] += has_children == 1
                for i, value in enumerate(values):
                    if _positive(value):
                        totals[i] += value
                        known[i] += 1
                        if i < 3:
                            key = ("input", "cached", "output")[i]
                            mr[key] = (mr[key] or 0) + value
        for key, value, samples in zip(
            (
                "tokens_input",
                "tokens_cached",
                "tokens_output",
                "cache_creation",
                "reasoning_tokens",
            ),
            totals,
            known,
        ):
            self.put(key, value if samples else None, "usage_missing")
        self.put("turns_with_context", context)
        self.put("context_over_90", high if context else None, "context_missing")

    def transfers(self):
        selected, args = self.selected_ids("source_thread_id", self.thread_meta)
        targets, target_args = self.selected_ids("target_thread_id", self.thread_meta)
        sql = f"""SELECT source_thread_id,target_thread_id,
            target_run_id,json_extract(payload_json,'$.sourcePoint.runId'),updated_at,status
            FROM {PREFIX}context_transfers WHERE type='provider_handoff'
            AND status IN ('resolved_native','resolved_portable') AND ({selected} OR {targets})
            AND activity_timestamp(updated_at)>={self.window[0]} AND activity_timestamp(updated_at)<{self.window[1]}"""
        for (
            source,
            target,
            target_run,
            source_run,
            stamp,
            status,
        ) in self.bounded_rows("transfers", sql, (*args, *target_args)):
            at = timing.timestamp(stamp)
            if (
                at is None
                or not self.window[0] <= at < self.window[1]
                or status not in {"resolved_native", "resolved_portable"}
            ):
                continue
            if source not in self.thread_meta and target not in self.thread_meta:
                continue
            old = self.runs.get(source_run, {}).get("model", "other")
            new = self.runs.get(target_run, {}).get("model", "other")
            self.handoffs[(old, new)] += 1
        self.put("handoffs", sum(self.handoffs.values()))

    def pull_requests(self):
        states: dict[tuple, tuple] = {}
        for tid in self.thread_meta:
            sql = f"""SELECT json_extract(j.value,'$.host'),json_extract(j.value,'$.repository'),
                json_extract(j.value,'$.number'),json_extract(j.value,'$.snapshot.state'),
                json_extract(j.value,'$.snapshot.syncedAt') FROM {PREFIX}threads t,
                json_each(t.payload_json,'$.pullRequests') j WHERE t.thread_id=?"""
            for host, repo, number, state, stamp in self.bounded_rows(
                "pr_links", sql, (tid,), per_query=512
            ):
                self.coverage["pr_links"] += 1
                if (
                    not isinstance(host, str)
                    or not isinstance(repo, str)
                    or type(number) is not int
                    or number <= 0
                ):
                    continue
                pr_key = (host.lower(), repo.removesuffix(".git").strip("/").lower(), number)
                at = timing.timestamp(stamp)
                state = state if state in {"open", "closed", "merged"} and at is not None else None
                previous = states.get(pr_key)
                if previous is None or (
                    at is not None and (previous[1] is None or at > previous[1])
                ):
                    states[pr_key] = (state, at)
                elif at is not None and at == previous[1] and state != previous[0]:
                    states[pr_key] = (None, at)
        self.coverage["pr_unique"] = len(states)
        known = [(state, at) for state, at in states.values() if state is not None]
        self.coverage["pr_states"] = len(known)
        if known:
            self.coverage["pr_first"] = int(min(at for _, at in known))
            self.coverage["pr_last"] = int(max(at for _, at in known))
            for key, state in (
                ("prs_open", "open"),
                ("prs_closed", "closed"),
                ("prs_merged", "merged"),
            ):
                self.put(
                    key,
                    sum(value == state for value, _ in known)
                    if len(known) == len(states)
                    else None,
                    "pr_snapshot_missing",
                )
        if self.scans["pr_links"]["truncated"] or self.scans["runs"]["truncated"]:
            for key in ("prs_open", "prs_closed", "prs_merged"):
                self.put(key, reason="partial_scan")

    def finish(self):
        threads: dict[str, dict[str, Any]] = {}
        totals: Counter = Counter()
        failed = defaultdict(list)
        for run in self.runs.values():
            if run["status"] == "failed" and run["interval"]:
                failed[run["thread"]].append(run["interval"])
        for tid, intervals in failed.items():
            for interval in timing.union(intervals):
                self._sample("failed_run", interval, True, tid)
        for agent_key, agent in self.agents.items():
            tid = agent["thread"]
            row = threads.setdefault(
                tid,
                {
                    **self.thread_meta[tid],
                    "seconds": 0.0,
                    "phases": dict.fromkeys(wire.PHASES, 0.0),
                    "bucket_map": {},
                },
            )
            if not agent_key.startswith("native:"):
                intervals = timing.union(agent["base"])
                for left, right in zip(intervals, intervals[1:]):
                    self._sample("no_run_gap", (left[1], right[0]), False, tid)
            segments = timing.phases(agent["base"], {key: agent[key] for key in wire.PHASES[:-1]})
            runs = [] if agent_key.startswith("native:") else self.thread_runs[tid]
            for start, end, phase, name in timing.model_parts(segments, runs, agent["model"]):
                seconds = end - start
                totals[phase] += seconds
                row["seconds"] += seconds
                row["phases"][phase] += seconds
                # The phase is known per agent; a model switch within a thread is
                # assigned to the actual overlapping run, never the thread default.
                mr = self.model_row(name)
                mr["seconds"] += seconds
                if phase == "tool":
                    mr["tool_seconds"] += seconds
                for day, part in timing.civil_parts(start, end):
                    self.daily[day]["seconds"] += part
                at = start
                while at < end:
                    bucket_at = int(
                        self.window[0]
                        + ((at - self.window[0]) // self.bucket_seconds) * self.bucket_seconds
                    )
                    stop = min(end, bucket_at + self.bucket_seconds)
                    bucket = row["bucket_map"].setdefault(
                        bucket_at, {"at": bucket_at, **dict.fromkeys(wire.PHASES, 0.0), "calls": 0}
                    )
                    bucket[phase] += stop - at
                    at = stop
        agent_seconds = sum(totals.values())
        for key, value in (
            ("agent_hours", agent_seconds / 3600),
            ("tool_hours", totals["tool"] / 3600),
            ("waiting_hours", totals["wait"] / 3600),
            ("peak_agents", timing.peak([a["base"] for a in self.agents.values()])),
            ("failed_hours", self.sinks.get("failed_run", {}).get("seconds", 0) / 3600),
            ("no_run_gap_hours", self.sinks.get("no_run_gap", {}).get("seconds", 0) / 3600),
        ):
            self.put(key, value)
        if not agent_seconds and (
            self.coverage["runs_untimed"]
            or self.coverage["native_agents"] > self.coverage["native_agents_timed"]
        ):
            self.put("agent_hours", reason="no_timing")
        sink_rows = []
        for sink in self.sinks.values():
            category = sink["category"]
            # Only exclusive phases have wall shares; command categories are
            # summed call-seconds and can overlap, so their wall share is n/a.
            share = (
                totals["wait"] / agent_seconds
                if category == "wait" and agent_seconds
                else totals["compaction"] / agent_seconds
                if category == "compaction" and agent_seconds
                else None
            )
            best = sink["attribution"].most_common(1)
            meta = self.thread_meta[best[0][0]] if best else None
            sink_rows.append(
                {key: sink[key] for key in ("category", "seconds", "count", "failed")}
                | {
                    "p95": timing.percentile(sink["samples"]),
                    "share": share,
                    "timed": len(sink["samples"]),
                    "thread": meta["id"] if meta else None,
                    "project": meta["project"] if meta else None,
                }
            )
        result = []
        for tid, row in sorted(
            threads.items(),
            key=lambda pair: (pair[1]["status"] in ACTIVE, pair[1]["seconds"]),
            reverse=True,
        )[: wire.MAX_THREADS]:
            row.pop("ordinal")
            for at in self.calls.get(tid, []):
                bucket_at = int(
                    self.window[0]
                    + ((at - self.window[0]) // self.bucket_seconds) * self.bucket_seconds
                )
                bucket = row["bucket_map"].setdefault(
                    bucket_at, {"at": bucket_at, **dict.fromkeys(wire.PHASES, 0.0), "calls": 0}
                )
                bucket["calls"] += 1
            row["buckets"] = [bucket for _, bucket in sorted(row.pop("bucket_map").items())]
            result.append(row)
        self.coverage["threads_kept"] = len(result)
        if len(threads) > len(result):
            self.coverage["partial"] = True
        return result, sorted(sink_rows, key=lambda row: row["seconds"], reverse=True)


def _span_category(name):
    if name == "sqlite":
        return "sqlite"
    if name == "sql.transaction":
        return "transaction"
    if isinstance(name, str) and name.startswith("provider."):
        return "provider"
    if isinstance(name, str) and name.startswith("tool."):
        return "tool"
    if isinstance(name, str) and name.startswith("server."):
        return "server"
    return "other"


def trace_logs(paths, window, coverage, limit):
    groups: dict[str, dict[str, Any]] = {}
    identities: set[bytes] = set()
    seen = 0
    for path in paths:
        try:
            coverage["log_bytes"] += Path(path).stat().st_size
            with Path(path).open("rb") as source:
                coverage["logs_read"] += 1
                while raw := source.readline(1048577):
                    seen += 1
                    if seen > limit:
                        coverage["partial"] = True
                        coverage["scans"]["logs"]["truncated"] = True
                        break
                    coverage["scans"]["logs"]["scanned"] += 1
                    if len(raw) > 1048576:
                        coverage["logs_skipped"] += 1
                        coverage["partial"] = True
                        break
                    try:
                        data = json.loads(raw)
                        if not isinstance(data, dict):
                            raise ValueError
                        trace_id, span_id = data.get("traceId"), data.get("spanId")
                        if (
                            isinstance(trace_id, str)
                            and isinstance(span_id, str)
                            and len(trace_id) <= 128
                            and len(span_id) <= 128
                        ):
                            key = hashlib.sha256((trace_id + ":" + span_id).encode()).digest()[:16]
                            if key in identities:
                                coverage["logs_skipped"] += 1
                                continue
                            identities.add(key)
                        end = timing.timestamp(int(data.get("endTimeUnixNano", 0)) / 10**9)
                        duration = data.get("durationMs")
                        if end is None or not _positive(duration):
                            raise ValueError
                        coverage["log_first"] = min(coverage["log_first"] or int(end), int(end))
                        coverage["log_last"] = max(coverage["log_last"] or int(end), int(end))
                        start = end - duration / 1000
                        interval = timing.clipped(start, end, window)
                        if not interval:
                            continue
                        group = groups.setdefault(
                            _span_category(data.get("name")),
                            dict(count=0, seconds=0.0, failed=0, samples=[]),
                        )
                        group["count"] += 1
                        seconds = interval[1] - interval[0]
                        group["seconds"] += seconds
                        group["samples"].append(seconds)
                        exit_data, status = data.get("exit"), data.get("status")
                        group["failed"] += (
                            isinstance(exit_data, dict) and exit_data.get("_tag") == "Failure"
                        ) or (isinstance(status, dict) and str(status.get("code")) == "2")
                        coverage["log_spans"] += 1
                    except (ValueError, TypeError, OverflowError, UnicodeError, RecursionError):
                        coverage["logs_skipped"] += 1
        except OSError:
            coverage["partial"] = True
        if seen > limit:
            break
    return [
        {
            "category": category,
            **{k: group[k] for k in ("count", "seconds", "failed")},
            "p95": timing.percentile(group["samples"]),
        }
        for category, group in groups.items()
    ]


def collect(path, window_start, window_end, *, logs=(), limits=None, progress=None):
    """Return one bounded, validated aggregate. Never print or return source errors."""
    start, end = timing.timestamp(window_start), timing.timestamp(window_end)
    if start is None or end is None or not 0 < end - start <= 31 * 86400 or end > time.time() + 60:
        raise ValueError("invalid_activity_window")
    begin = time.perf_counter()
    try:
        from contextlib import closing

        with closing(connect(path)) as db:
            limits = limits or Limits()
            collector = Collector(db, (int(start), int(end)), limits, progress)
            collector.metadata()
            collector.subagents()
            collector.items()
            collector.asks_and_retries()
            collector.tokens()
            collector.transfers()
            collector.pull_requests()
            threads, sinks = collector.finish()
            spans = trace_logs(logs, (start, end), collector.coverage, limits.log_lines)
            collector.coverage.update(
                source_bytes=Path(path).stat().st_size,
                detail_ms=(time.perf_counter() - begin) * 1000 - collector.coverage["metadata_ms"],
                peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            )
            snapshot = dict(
                schema=wire.VERSION,
                collected_at=int(time.time()),
                window_start=int(start),
                window_end=int(end),
                timezone="Europe/Amsterdam",
                source="local",
                metrics=collector.metrics,
                coverage=collector.coverage,
                daily=list(collector.daily.values()),
                models=list(collector.models.values()),
                sinks=sinks,
                threads=threads,
                handoffs=[
                    {"from": a, "to": b, "count": n} for (a, b), n in collector.handoffs.items()
                ],
                spans=spans,
            )
            return wire.validate(snapshot)
    except (sqlite3.Error, OSError, KeyError, TypeError, wire.InvalidSnapshot):
        raise SourceUnavailable() from None


def collect_and_report(
    path, window_start, window_end, send, *, logs=(), limits=None, progress=None
):
    """One explicit report through the caller's authorized machine transport.

    ``send(path, body)`` supplies the existing machine authentication. Nothing
    discovers an endpoint, reads credentials or sends source records implicitly.
    """
    snapshot = collect(path, window_start, window_end, logs=logs, limits=limits, progress=progress)
    wire.validate(snapshot)
    try:
        return send("/api/runner/activity", snapshot)
    except Exception:
        raise SourceUnavailable() from None

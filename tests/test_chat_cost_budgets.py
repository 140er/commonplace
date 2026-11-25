"""Chat-relay jobs in the cost ledger: events per scope, the 80% warning, the hard stop."""

import json
import sys
import time
from pathlib import Path

import pytest

from commonplace.runner import chat_agent
from commonplace.runner.agent import _Hub, _HubError
from mempalace.sqlite_crypto import connect, row_factory
from tests._relay_helpers import relay_hub_fixture  # noqa: F401

BUDGETS = "/api/settings/budgets"
USAGE = dict(input_tokens=120, output_tokens=30, cost_micros=700, currency="USD")


def _rows(h, sql, *args):
    conn = connect(str(h.hub.gateway.workspace_dir(h.workspace)) + "/runner.sqlite3")
    conn.row_factory = row_factory
    try:
        return [dict(row) for row in conn.execute(sql, args)]
    finally:
        conn.close()


def _setup(h):
    m = h.machine()
    h.set_main_model("claude", "claude-haiku-5-5")
    assert h.poll(m)[0] == 204
    return m


def _answer(h, m, text="Fine.", **usage):
    h.post(h.owner, "Question " + text)
    status, lease = h.poll(m)
    assert status == 200, (status, lease)
    body = {
        "job": lease["job"],
        "lease_token": lease["lease_token"],
        "text": text,
        "cli": "claude",
        "model": "claude-haiku-5-5",
        **usage,
    }
    result = h.request(m["machine_token"], "POST", "/api/runner/chat/answer", body)
    return lease, result[0], result[1]


def _budget(h, kind, key, limit, auth=None, **extra):
    scope = {"kind": kind} if key is None else {"kind": kind, "key": key}
    body = {"scope": scope, "limit_micros": limit, "currency": "USD", **extra}
    status, answer, _ = h.request(auth or h.owner, "PUT", BUDGETS, body)
    assert status == 200, (status, answer)
    return answer


def _costs(h):
    status, body, _ = h.request(h.owner, "GET", "/api/costs")
    assert status == 200, (status, body)
    return body


def _needs_me(h, auth):
    status, body, _ = h.request(auth, "GET", "/api/needs-me")
    assert status == 200, (status, body)
    return body


def _replies(h, message_kind="answer"):
    return [r for r in h.chat(h.owner)[1]["replies"] if r["kind"] == message_kind]


def test_chat_job_writes_one_cost_event_per_workspace_agent_and_project(relay_hub):
    h = relay_hub
    m = _setup(h)
    lease, status, _ = _answer(h, m, **USAGE)
    assert status == 200
    [row] = _rows(h, "SELECT * FROM run_ledger WHERE source='chat'")
    assert row["job_id"] == lease["job"] and row["status"] == "completed"
    assert (row["project"], row["board_slug"], row["task_number"]) == ("work", "work", 0)
    assert (row["model_provider"], row["model"]) == ("claude", "claude-haiku-5-5")
    [event] = _rows(h, "SELECT * FROM cost_events WHERE job_id=?", lease["job"])
    assert (event["source"], event["cost_micros"], event["currency"]) == ("chat", 700, "USD")
    assert (event["input_tokens"], event["output_tokens"], event["cost_unknown"]) == (120, 30, 0)
    sums = _costs(h)
    assert sums["workspace"]["cost_micros"] == {"USD": 700}
    assert sums["workspace"]["runs"] == 1
    [by_agent] = sums["by_agent"]
    assert by_agent["agent"] == row["agent"] and by_agent["cost_micros"] == {"USD": 700}
    [by_project] = sums["by_project"]
    assert by_project["project"] == "work" and by_project["cost_micros"] == {"USD": 700}
    assert sums["by_card"] == []


def test_retried_answer_does_not_count_twice(relay_hub):
    h = relay_hub
    m = _setup(h)
    lease, status, _ = _answer(h, m, **USAGE)
    assert status == 200
    again = h.answer(m, lease["job"], lease["lease_token"], "Again")
    assert again[0] == 409
    assert len(_rows(h, "SELECT * FROM cost_events")) == 1
    assert _costs(h)["workspace"]["cost_micros"] == {"USD": 700}


def test_unknown_usage_is_counted_apart_never_as_zero(relay_hub):
    h = relay_hub
    m = _setup(h)
    lease, status, _ = _answer(h, m, "No usage reported.")
    assert status == 200
    [event] = _rows(h, "SELECT * FROM cost_events WHERE job_id=?", lease["job"])
    assert (event["cost_unknown"], event["cost_micros"], event["currency"]) == (1, None, None)
    sums = _costs(h)
    assert sums["workspace"]["unknown_cost_runs"] == 1 and sums["workspace"]["cost_micros"] == {}
    assert sums["unknown_cost"]["listed"][0]["source"] == "chat"
    # An unknown cost never counts against a limit, so it never blocks a start.
    _budget(h, "workspace", None, 1000, unknown_runs_warn=1)
    assert _answer(h, m, "Still allowed.")[1] == 200


def test_declared_unknown_and_invalid_usage(relay_hub):
    h = relay_hub
    m = _setup(h)
    h.post(h.owner, "Question")
    lease = h.poll(m)[1]
    base = {"job": lease["job"], "lease_token": lease["lease_token"], "text": "x", "cli": "claude"}
    base["model"] = "claude-haiku-5-5"

    def send(extra):
        return h.request(m["machine_token"], "POST", "/api/runner/chat/answer", {**base, **extra})

    assert send({"input_tokens": -1})[0] == 400
    assert send({"cost_micros": 5})[0] == 400  # currency goes with cost
    assert send({"cost_unknown": True, "cost_micros": 5, "currency": "USD"})[0] == 400
    assert send({"cost_unknown": True, "input_tokens": 9})[0] == 200
    [event] = _rows(h, "SELECT * FROM cost_events")
    assert (event["cost_unknown"], event["input_tokens"]) == (1, 9)


def test_failed_job_ends_its_run_with_unknown_cost(relay_hub):
    h = relay_hub
    m = _setup(h)
    h.post(h.owner, "Please answer.")
    lease = h.poll(m)[1]
    assert h.fail(m, lease["job"], lease["lease_token"], "cli_timeout")[0] == 200
    [row] = _rows(h, "SELECT * FROM run_ledger WHERE source='chat'")
    assert row["status"] == "errored" and row["ended_at"]
    [event] = _rows(h, "SELECT * FROM cost_events")
    assert event["cost_unknown"] == 1 and event["cost_micros"] is None


def test_expired_lease_ends_as_uncertain_unknown(relay_hub):
    h = relay_hub
    m = _setup(h)
    h.post(h.owner, "Please answer.")
    lease = h.poll(m)[1]
    relay = h.relay()
    later = time.time() + relay.CHAT_LEASE_S + 1
    relay.sweep(h.db, now=later)
    h.post(h.owner, "A second question.")  # a new enqueue settles the expired run
    [row] = _rows(h, "SELECT * FROM run_ledger WHERE job_id=?", lease["job"])
    assert row["status"] == "uncertain" and row["ended_at"]
    [event] = _rows(h, "SELECT * FROM cost_events WHERE job_id=?", lease["job"])
    assert event["cost_unknown"] == 1


def test_budget_at_hundred_percent_refuses_a_new_chat_start_in_the_chat(relay_hub):
    h = relay_hub
    m = _setup(h)
    assert _answer(h, m, **{**USAGE, "cost_micros": 1000})[1] == 200
    _budget(h, "workspace", None, 1000)
    message = h.post(h.owner, "One more question.")
    assert h.poll(m)[0] == 204
    job = h.jobs()[-1]
    assert (job["state"], job["code"], job["message_id"]) == ("skipped", "budget_exceeded", message)
    [reply] = [r for r in _replies(h) if r["message_id"] == message]
    assert reply["code"] == "budget_exceeded" and reply["model"] is None
    assert "workspace budget" in reply["text"] and "spent" in reply["text"]
    assert "1000" not in reply["text"]  # a refusal never carries amounts
    assert h.status(h.owner)[1]["front"]["runner"]["last_error"] == "budget_exceeded"
    # The receipt stays; the refused job never reached a runner or the ledger.
    assert len(_rows(h, "SELECT * FROM run_ledger WHERE source='chat'")) == 1


def test_project_and_agent_budgets_refuse_chat_starts(relay_hub):
    h = relay_hub
    m = _setup(h)
    assert _answer(h, m, **{**USAGE, "cost_micros": 500})[1] == 200
    agent = _rows(h, "SELECT agent FROM run_ledger")[0]["agent"]
    _budget(h, "project", "other-board", 100)  # another project's budget does not apply
    _budget(h, "agent", agent, 500)
    h.post(h.owner, "Blocked by the agent budget?")
    assert h.poll(m)[0] == 204 and h.jobs()[-1]["code"] == "budget_exceeded"
    assert h.request(h.owner, "DELETE", BUDGETS + "?kind=agent&key=" + agent)[0] in (200, 204)
    _budget(h, "project", "work", 500)
    h.post(h.owner, "Blocked by the project budget?")
    assert h.poll(m)[0] == 204 and h.jobs()[-1]["code"] == "budget_exceeded"


def test_queued_chat_start_is_rechecked_at_lease(relay_hub):
    h = relay_hub
    m = _setup(h)
    assert _answer(h, m, **{**USAGE, "cost_micros": 1000})[1] == 200
    message = h.post(h.owner, "Queued before the budget exists.")
    assert h.jobs()[-1]["state"] == "queued"
    _budget(h, "workspace", None, 1000)
    assert h.poll(m)[0] == 204
    job = h.jobs()[-1]
    assert (job["state"], job["code"]) == ("skipped", "budget_exceeded")
    assert [r["code"] for r in _replies(h) if r["message_id"] == message] == ["budget_exceeded"]


def test_running_chat_turn_finishes_at_the_hard_stop(relay_hub):
    h = relay_hub
    m = _setup(h)
    h.post(h.owner, "Leased before the budget.")
    lease = h.poll(m)[1]
    _budget(h, "workspace", None, 100)
    assert h.answer(m, lease["job"], lease["lease_token"], "Finished anyway.")[0] == 200
    [row] = _rows(h, "SELECT * FROM run_ledger")
    assert row["status"] == "completed"


def test_eighty_percent_warns_only_the_owner_of_that_workspace_once(relay_hub):
    h = relay_hub
    m = _setup(h)
    _budget(h, "project", "work", 1000)
    assert "budget_warnings" not in _needs_me(h, h.owner)
    assert _answer(h, m, **{**USAGE, "cost_micros": 700})[1] == 200
    assert "budget_warnings" not in _needs_me(h, h.owner)
    assert _answer(h, m, "Second.", **{**USAGE, "cost_micros": 100})[1] == 200
    [item] = _needs_me(h, h.owner)["budget_warnings"]
    assert item["scope"] == {"kind": "project", "key": "work"} and item["reasons"] == ["warning"]
    assert h.request(h.member, "GET", "/api/needs-me")[0] == 403
    assert "budget_warnings" not in _needs_me(h, h.other)
    assert _answer(h, m, "Third.", **{**USAGE, "cost_micros": 50})[1] == 200
    marks = _rows(h, "SELECT level FROM cost_budget_marks")
    assert [mark["level"] for mark in marks] == ["warning"]


def test_ledger_rows_hold_no_secrets_or_text(relay_hub):
    h = relay_hub
    m = _setup(h)
    lease, status, _ = _answer(h, m, "Private answer text.", **USAGE)
    assert status == 200
    dump = json.dumps(
        {
            table: _rows(h, "SELECT * FROM " + table)
            for table in ("run_ledger", "cost_events", "cost_budget_marks")
        }
    )
    for secret in (
        lease["lease_token"],
        m["machine_token"],
        lease["prompt"],
        "Private answer text.",
        "Question",
    ):
        assert secret not in dump


def test_no_budget_leaves_chat_untouched(relay_hub):
    h = relay_hub
    m = _setup(h)
    assert _answer(h, m, **USAGE)[1] == 200
    message = h.post(h.owner, "Next.")
    assert h.poll(m)[0] == 200
    assert [r for r in _replies(h) if r["message_id"] == message] == []


# The runner chat client end to end: a fake provider CLI prints its result, the real client
# reads it, leases and answers over HTTP, and the hub's ledger and budgets do the rest.
CLIENT_USAGE = ("--json-result", "--tokens", "100,20,10,30", "--cost", "0.0007")


def _client_turn(h, m, tmp_path, *flags, text="Question"):
    """One chat turn through the real client: post, lease, run the fake CLI, send the result."""
    config = {
        "hub_url": f"http://127.0.0.1:{h.hub.port}",
        "token_env": "COMMONPLACE_RUNNER_TOKEN",
        "chat": {
            "default_cli": "claude",
            "context": "minimal",
            "relay_always": False,
            "clis": {
                "claude": {
                    "argv": [
                        sys.executable,
                        str(Path(__file__).with_name("runner_fake_cli.py").resolve()),
                        "--record",
                        str(tmp_path / "record.json"),
                        "--model",
                        "{model}",
                        *flags,
                    ],
                    "default_model": "claude-haiku-5-5",
                    "tools": True,
                }
            },
        },
    }
    h.post(h.owner, text)
    hub = _Hub({"hub_url": config["hub_url"], "poll_seconds": 3}, m["machine_token"])
    job = hub.post("lease", {"clis": ["claude"]}, prefix="/api/runner/chat/")
    assert job is not None
    chat_agent._reply(hub, config, job)
    return job


def test_client_sends_provider_usage_so_chat_writes_a_known_cost(relay_hub, tmp_path):
    h = relay_hub
    m = _setup(h)
    job = _client_turn(h, m, tmp_path, *CLIENT_USAGE)
    [event] = _rows(h, "SELECT * FROM cost_events WHERE job_id=?", job["job"])
    assert (event["source"], event["cost_micros"], event["currency"]) == ("chat", 700, "USD")
    assert (event["input_tokens"], event["output_tokens"], event["cost_unknown"]) == (130, 30, 0)
    [row] = _rows(h, "SELECT * FROM run_ledger WHERE source='chat'")
    assert (row["model_provider"], row["model"]) == ("claude", "claude-haiku-5-5")
    sums = _costs(h)
    assert sums["workspace"]["cost_micros"] == {"USD": 700}
    assert sums["workspace"]["unknown_cost_runs"] == 0
    assert [a["cost_micros"] for a in sums["by_agent"]] == [{"USD": 700}]
    assert [p["project"] for p in sums["by_project"]] == ["work"]
    # Only the whitelisted numbers left the machine: none of the other result fields did.
    dump = json.dumps({t: _rows(h, "SELECT * FROM " + t) for t in ("run_ledger", "cost_events")})
    for canary in ("session-canary", "/canary/path", "canary-model", "canary-tier", "Question"):
        assert canary not in dump


def test_client_without_a_reported_cost_stays_unknown_never_zero(relay_hub, tmp_path):
    h = relay_hub
    m = _setup(h)
    plain = _client_turn(h, m, tmp_path)  # a CLI that prints plain text reports nothing
    no_cost = _client_turn(h, m, tmp_path, "--json-result", "--tokens", "5,0,0,7")
    zero = _client_turn(h, m, tmp_path, "--json-result", "--cost", "0")
    events = {e["job_id"]: e for e in _rows(h, "SELECT * FROM cost_events")}
    for job in (plain, no_cost, zero):
        event = events[job["job"]]
        assert (event["cost_unknown"], event["cost_micros"], event["currency"]) == (1, None, None)
    assert (events[no_cost["job"]]["input_tokens"], events[no_cost["job"]]["output_tokens"]) == (
        5,
        7,
    )
    assert events[plain["job"]]["input_tokens"] is None
    sums = _costs(h)
    assert sums["workspace"]["unknown_cost_runs"] == 3 and sums["workspace"]["cost_micros"] == {}


def test_client_sends_usage_of_a_failed_turn(relay_hub, tmp_path):
    h = relay_hub
    m = _setup(h)
    job = _client_turn(h, m, tmp_path, *CLIENT_USAGE, "--exit", "1")
    assert h.jobs()[-1]["code"] == "cli_failed"
    [event] = _rows(h, "SELECT * FROM cost_events WHERE job_id=?", job["job"])
    assert (event["cost_micros"], event["output_tokens"], event["cost_unknown"]) == (700, 30, 0)


def test_chat_spend_from_the_client_reaches_the_warning_and_the_hard_stop(relay_hub, tmp_path):
    h = relay_hub
    m = _setup(h)
    _budget(h, "project", "work", 1000)
    _client_turn(h, m, tmp_path, "--json-result", "--cost", "0.0007")
    assert "budget_warnings" not in _needs_me(h, h.owner)
    _client_turn(h, m, tmp_path, "--json-result", "--cost", "0.0001")  # 800 of 1000: 80%
    [item] = _needs_me(h, h.owner)["budget_warnings"]
    assert item["scope"] == {"kind": "project", "key": "work"} and item["reasons"] == ["warning"]
    assert h.request(h.member, "GET", "/api/needs-me")[0] == 403
    assert "budget_warnings" not in _needs_me(h, h.other)
    # Still below the limit: the next chat start is admitted and its answer reaches 100%.
    _client_turn(h, m, tmp_path, "--json-result", "--cost", "0.0002")
    levels = {mark["level"] for mark in _rows(h, "SELECT level FROM cost_budget_marks")}
    assert levels == {"warning", "limit"}
    assert len(_needs_me(h, h.owner)["budget_warnings"]) == 1  # one item per scope and window
    message = h.post(h.owner, "One more question.")
    assert h.poll(m)[0] == 204
    job = h.jobs()[-1]
    assert (job["state"], job["code"], job["message_id"]) == ("skipped", "budget_exceeded", message)
    [reply] = [r for r in _replies(h) if r["message_id"] == message]
    assert reply["code"] == "budget_exceeded" and "project work budget" in reply["text"]
    assert h.status(h.owner)[1]["front"]["runner"]["last_error"] == "budget_exceeded"


def test_client_resending_the_same_answer_counts_once(relay_hub, tmp_path, monkeypatch):
    h = relay_hub
    m = _setup(h)
    send = chat_agent._post_result
    fenced = []

    def twice(hub, action, body, deadline):
        result = send(hub, action, body, deadline)
        with pytest.raises(_HubError) as exc:  # the retry of an answer the hub already holds
            send(hub, action, body, deadline)
        fenced.append(exc.value.kind)
        return result

    monkeypatch.setattr(chat_agent, "_post_result", twice)
    _client_turn(h, m, tmp_path, *CLIENT_USAGE)
    assert fenced == ["fenced"]
    assert len(_rows(h, "SELECT * FROM cost_events")) == 1
    assert _costs(h)["workspace"]["cost_micros"] == {"USD": 700}

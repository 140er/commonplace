"""Merging a reported PR with the caller's own GitHub grant, using synthetic transports only."""

import importlib
import json
import logging
from urllib.parse import parse_qs, urlsplit

import pytest

from commonplace import sessions, urls
from commonplace.boards.service import BoardService
from commonplace.extensions import ToolContext
from commonplace.social import providers
from tests.test_commonplace_social import request, rows
from tests.test_github_grants import (
    Remote,
    begin,
    connect,
    finish,
    make_surface,  # noqa: F401
    setup,  # noqa: F401
    status,
    write,
)

HEAD = "a" * 40
MOVED = "b" * 40
API = "https://api.github.com/repos/example/project"


class MergeRemote(Remote):
    """A GitHub double that issues a distinct token per authorization."""

    def __init__(self):
        super().__init__()
        self.issued = []
        self.private = False
        self.repo_status = 200
        self.head = HEAD
        self.state = "open"
        self.draft = False
        self.merged = False
        self.mergeable_state = "clean"
        self.merge_status = 200
        self.methods = {"squash": True, "merge": True, "rebase": True}
        self.pull_status = 200
        self.on_revoke = None

    def request(self, method, url, **kwargs):
        if url.endswith("access_token"):
            self.calls.append((method, url, kwargs))
            token = f"synthetic-token-{len(self.issued) + 1}"
            self.issued.append(token)
            return 200, {}, {"access_token": token, "scope": self.scope, "expires_in": 3600}
        if url == API and method == "GET":
            self.calls.append((method, url, kwargs))
            if self.repo_status != 200:
                return self.repo_status, {}, {}
            return (
                200,
                {},
                {
                    "private": self.private,
                    "allow_squash_merge": self.methods["squash"],
                    "allow_merge_commit": self.methods["merge"],
                    "allow_rebase_merge": self.methods["rebase"],
                },
            )
        if url == API + "/pulls/7" and method == "GET":
            self.calls.append((method, url, kwargs))
            if self.pull_status != 200:
                return self.pull_status, {}, {}
            return (
                200,
                {},
                {
                    "state": self.state,
                    "draft": self.draft,
                    "merged": self.merged,
                    "mergeable_state": self.mergeable_state,
                    "head": {"sha": self.head},
                },
            )
        if url == API + "/pulls/7/merge" and method == "PUT":
            self.calls.append((method, url, kwargs))
            if self.merge_status != 200:
                return self.merge_status, {}, {}
            return 200, {}, {"merged": True, "sha": "c" * 40}
        if method == "DELETE" and self.on_revoke:
            self.calls.append((method, url, kwargs))
            self.on_revoke()
            return self.status, {}, {}
        return super().request(method, url, **kwargs)

    def merges(self):
        return [call for call in self.calls if call[0] == "PUT"]


@pytest.fixture
def merging(setup, monkeypatch):  # noqa: F811
    s = setup
    remote = MergeRemote()
    s.remote = remote
    monkeypatch.setattr(
        importlib.import_module("commonplace.github.client"), "request", remote.request
    )
    principal = sessions.session_principal(s.db, s.session)
    service = BoardService(ToolContext(principal=principal, gateway=s.gateway))
    s.service = service
    service.task_update(
        s.card,
        status="doing",
        repo="example/project",
        pr=7,
        pr_state="open",
        pr_head=HEAD,
        checks="passing",
    )
    return s


def begin_consent(s):
    code, headers, raw = write(s, "/api/github/merge-consent")
    assert code == 200, (code, raw)
    params = parse_qs(urlsplit(json.loads(raw)["authorize_url"]).query, keep_blank_values=True)
    assert params["scope"] == ["public_repo"] and params["code_challenge_method"] == ["S256"]
    return params["state"][0], headers["Set-Cookie"].split(";", 1)[0]


def consent(s):
    """Run the separate opt-in to completion for the signed-in person."""
    s.remote.scope = "public_repo"
    assert finish(s, begin_consent(s))[0] == 303
    assert status(s)["state"] == "merge_confirm"
    assert write(s, "/api/github/confirm", payload={"accept": True})[0] == 200
    s.remote.scope = ""
    assert status(s)["state"] == "connected" and status(s).get("merge") is True


def merge(s, **payload):
    return write(s, "/api/github/merge", payload={"head": HEAD, **payload})


def tick(worker, monkeypatch):
    """One real worker heartbeat iteration, without sleeps or background scheduling."""
    worker._stopped.set()
    worker._heartbeat_thread.join()
    worker._stopped.clear()
    turns = iter([False, True])
    with monkeypatch.context() as patch:
        patch.setattr(worker._stopped, "wait", lambda timeout: next(turns))
        worker._heartbeat()


def revoked_tokens(s):
    return [c[2]["json_body"]["access_token"] for c in s.remote.calls if c[0] == "DELETE"]


def stored_token(s):
    grant = rows(s, "github_grants")[0]
    return s.gateway.github.store.decrypt(s.db._conn, s.uid, "grant", grant["secret"])["token"]


def pending_merge_consent(s):
    """Connected, then stopped at the Allow merging confirmation; returns the candidate token."""
    connect(s)
    s.remote.scope = "public_repo"
    finish(s, begin_consent(s))
    assert status(s)["state"] == "merge_confirm"
    return s.remote.issued[-1]


def reason(response):
    code, _, raw = response
    assert code == 409, (code, raw)
    return json.loads(raw)["error"]


def other_person(s, subject):
    other = s.db.create_user(f"person{subject}@example.com")
    s.db.add_member(s.workspace, other, "member", actor=s.uid)
    s.remote.subject = subject
    return other


def test_connect_alone_keeps_empty_scope_and_no_merge_consent(merging):
    s = merging
    connect(s)
    assert status(s).get("merge") is False
    grant = rows(s, "github_grants")[0]
    stored = s.gateway.github.store.decrypt(s.db._conn, s.uid, "grant", grant["secret"])
    assert stored == {"token": s.remote.issued[0]}
    assert reason(merge(s)) == "github_merge_consent"
    assert not [c for c in s.remote.calls if c[1].startswith(API)]


def test_merge_consent_needs_connection_and_a_visible_card(merging):
    s = merging
    assert reason(write(s, "/api/github/merge-consent")) == "github_not_connected"
    connect(s)
    assert reason(write(s, "/api/github/merge-consent", payload={"card": 99999})) == "github_card"
    assert not rows(s, "github_connect_flows")


def test_merge_consent_requests_public_repo_and_replaces_the_token_after_confirmation(merging):
    s = merging
    connect(s)
    first = s.remote.issued[0]
    s.remote.scope = "public_repo"
    flow = begin_consent(s)
    assert rows(s, "github_connect_flows")[0]["purpose"] == "merge"
    finish(s, flow)
    pending = status(s)
    assert pending["state"] == "merge_confirm" and pending["scopes"] == ["public_repo"]
    # Nothing is granted until the person accepts.
    stored = s.gateway.github.store.decrypt(
        s.db._conn, s.uid, "grant", rows(s, "github_grants")[0]["secret"]
    )
    assert stored == {"token": first}
    assert reason(merge(s)) == "github_merge_consent"
    assert write(s, "/api/github/confirm", payload={"accept": True})[0] == 200
    after = status(s)
    assert after["state"] == "connected" and after.get("merge") is True
    grant = rows(s, "github_grants")[0]
    stored = s.gateway.github.store.decrypt(s.db._conn, s.uid, "grant", grant["secret"])
    assert stored["scopes"] == ["public_repo"] and stored["token"] == s.remote.issued[-1]
    revoked = [c[2]["json_body"]["access_token"] for c in s.remote.calls if c[0] == "DELETE"]
    assert revoked == [first]


def test_declined_merge_consent_leaves_the_empty_scope_grant(merging):
    s = merging
    connect(s)
    s.remote.scope = "public_repo"
    finish(s, begin_consent(s))
    assert write(s, "/api/github/confirm", payload={"accept": False})[0] == 200
    assert status(s)["state"] == "connected" and status(s).get("merge") is False
    new = s.remote.issued[-1]
    assert [
        c for c in s.remote.calls if c[0] == "DELETE" and c[2]["json_body"]["access_token"] == new
    ]
    assert reason(merge(s)) == "github_merge_consent"


@pytest.mark.parametrize("scope", ["", "repo", "public_repo repo", "public_repo admin:org"])
def test_merge_consent_callback_accepts_only_public_repo_and_revokes_the_rest(merging, scope):
    s = merging
    connect(s)
    s.remote.scope = scope
    finish(s, begin_consent(s))
    assert status(s)["state"] == "connected" and status(s).get("merge") is False
    new = s.remote.issued[-1]
    assert [
        c for c in s.remote.calls if c[0] == "DELETE" and c[2]["json_body"]["access_token"] == new
    ]
    assert not rows(s, "github_confirmations")


def test_merge_consent_for_a_different_github_account_is_refused(merging):
    s = merging
    connect(s)
    s.remote.scope, s.remote.subject = "public_repo", 999
    finish(s, begin_consent(s))
    assert status(s)["state"] == "connected" and status(s).get("merge") is False
    assert not rows(s, "github_confirmations")


def test_connect_flow_still_refuses_public_repo_scope(merging):
    s = merging
    s.remote.scope = "public_repo"
    finish(s, begin(s))
    assert status(s)["state"] == "absent"
    assert not rows(s, "github_grants")


def test_merge_uses_the_callers_own_grant_and_pins_the_head(merging):
    s = merging
    connect(s)
    consent(s)
    own = s.remote.issued[-1]
    code, _, raw = merge(s)
    assert code == 200, raw
    assert json.loads(raw) == {"merged": True, "sha": HEAD}
    ((method, url, kwargs),) = s.remote.merges()
    assert url == API + "/pulls/7/merge" and kwargs["token"] == own
    assert kwargs["json_body"]["sha"] == HEAD
    assert kwargs["json_body"]["merge_method"] == "squash"
    for call in s.remote.calls:
        if call[1].startswith(API):
            assert call[2]["token"] == own


@pytest.mark.parametrize(
    "methods,expected",
    [
        ({"squash": False, "merge": True, "rebase": True}, "merge"),
        ({"squash": False, "merge": False, "rebase": True}, "rebase"),
    ],
)
def test_merge_method_follows_what_the_repository_allows(merging, methods, expected):
    s = merging
    connect(s)
    consent(s)
    s.remote.methods = methods
    assert merge(s)[0] == 200
    assert s.remote.merges()[0][2]["json_body"]["merge_method"] == expected


def test_merge_refuses_when_no_method_is_allowed(merging):
    s = merging
    connect(s)
    consent(s)
    s.remote.methods = {"squash": False, "merge": False, "rebase": False}
    assert reason(merge(s)) == "github_merge_refused"
    assert not s.remote.merges()


def test_merge_without_a_connection_is_refused_before_any_github_call(merging):
    s = merging
    assert reason(merge(s)) == "github_not_connected"
    assert not [c for c in s.remote.calls if c[1].startswith(API)]


def test_another_persons_grant_is_never_used(merging):
    s = merging
    other = other_person(s, 456)
    owner_cookie = s.cookie
    s.cookie = sessions.COOKIE + "=" + sessions.start_session(s.db, other, s.workspace)
    s.remote.subject = 456
    connect(s)
    consent(s)
    assert len(rows(s, "github_grants")) == 1
    # The card owner has no grant of their own: the other person's token stays unused.
    s.cookie = owner_cookie
    s.remote.subject = 123
    assert reason(merge(s)) == "github_not_connected"
    assert not s.remote.merges()
    assert not [c for c in s.remote.calls if c[1].startswith(API)]


def test_each_person_merges_with_their_own_token(merging):
    s = merging
    owner_cookie = s.cookie
    other = other_person(s, 456)
    s.cookie = sessions.COOKIE + "=" + sessions.start_session(s.db, other, s.workspace)
    connect(s)
    consent(s)
    theirs = s.remote.issued[-1]
    s.cookie = owner_cookie
    s.remote.subject = 123
    connect(s)
    consent(s)
    mine = s.remote.issued[-1]
    assert mine != theirs
    assert merge(s)[0] == 200
    assert s.remote.merges()[0][2]["token"] == mine


def test_a_grant_connected_in_another_workspace_is_never_used(merging):
    s = merging
    connect(s)
    consent(s)
    wid = s.db.create_workspace("elsewhere", s.uid)
    s.db._conn.execute(
        "UPDATE sessions SET workspace_id=? WHERE id_hash=?", (wid, sessions._hash(s.session))
    )
    # A merge-ready card is visible here, so only the grant's own workspace can refuse.
    principal = sessions.session_principal(s.db, s.session)
    service = BoardService(ToolContext(principal=principal, gateway=s.gateway))
    service.board_add(slug="work", name="Work")
    card = service.task_add(board="work", title="Ready")["number"]
    service.task_update(
        card,
        status="doing",
        repo="example/project",
        pr=7,
        pr_state="open",
        pr_head=HEAD,
        checks="passing",
    )
    assert status(s)["state"] == "other_workspace"
    assert reason(write(s, "/api/github/merge", payload={"card": card, "head": HEAD})) == (
        "github_not_connected"
    )
    assert not [c for c in s.remote.calls if c[1].startswith(API)]


@pytest.mark.parametrize("problem", ["disconnected", "expired", "disabled", "pending", "key"])
def test_unusable_grants_fail_closed(merging, problem, monkeypatch):
    s = merging
    connect(s)
    consent(s)
    csrf = status(s)["csrf"]
    if problem == "disconnected":
        assert write(s, "/api/github", "DELETE")[0] == 200
    elif problem == "expired":
        s.db._conn.execute("UPDATE github_grants SET expires_at=1")
    elif problem == "disabled":
        s.db._conn.execute("UPDATE github_grants SET disabled=1")
    elif problem == "pending":
        s.db._conn.execute("UPDATE github_lifecycle SET disconnect_pending=1")
    else:
        monkeypatch.setenv("COMMONPLACE_GITHUB_KEY_FILE", "/nonexistent/key")
    code, _, raw = request(
        s,
        "/api/github/merge",
        "POST",
        cookie=s.cookie,
        raw=json.dumps({"csrf": csrf, "card": s.card, "head": HEAD}).encode(),
        headers={"Content-Type": "application/json", "Origin": urls.APP_URL},
    )
    assert code == 409, raw
    assert json.loads(raw)["error"] in ("github_not_connected", "github_key", "github_pending")
    assert not s.remote.merges()
    assert not [c for c in s.remote.calls if c[1].startswith(API)]


def test_a_moved_head_is_refused_before_merging(merging):
    s = merging
    connect(s)
    consent(s)
    s.remote.head = MOVED
    assert reason(merge(s)) == "github_head_moved"
    assert not s.remote.merges()


def test_github_rejecting_the_pinned_sha_is_reported_as_a_moved_head(merging):
    s = merging
    connect(s)
    consent(s)
    s.remote.merge_status = 409
    assert reason(merge(s)) == "github_head_moved"
    assert s.remote.merges()[0][2]["json_body"]["sha"] == HEAD


@pytest.mark.parametrize(
    "status_code,error",
    [
        (405, "github_not_mergeable"),
        (403, "github_merge_refused"),
        (404, "github_merge_refused"),
        (401, "github_not_connected"),
        (500, "github_merge_failed"),
    ],
)
def test_github_refusals_stay_with_github_and_are_generic(merging, status_code, error):
    s = merging
    connect(s)
    consent(s)
    s.remote.merge_status = status_code
    assert reason(merge(s)) == error


@pytest.mark.parametrize("repo_status", [200, 404])
def test_private_repositories_fall_back_to_opening_the_pr(merging, repo_status):
    s = merging
    connect(s)
    consent(s)
    s.remote.private = repo_status == 200
    s.remote.repo_status = repo_status
    assert reason(merge(s)) == "github_private_repo"
    assert not s.remote.merges()


@pytest.mark.parametrize(
    "field,value",
    [
        ("mergeable_state", "blocked"),
        ("mergeable_state", "behind"),
        ("mergeable_state", "dirty"),
        ("mergeable_state", "unstable"),
        ("mergeable_state", "unknown"),
        ("mergeable_state", "draft"),
        ("mergeable_state", None),
        ("draft", True),
        ("state", "closed"),
        ("merged", True),
    ],
)
def test_only_a_clean_open_pr_is_merged(merging, field, value):
    s = merging
    connect(s)
    consent(s)
    setattr(s.remote, field, value)
    assert reason(merge(s)) == "github_not_mergeable"
    assert not s.remote.merges()


def test_a_card_that_is_not_merge_ready_is_refused(merging):
    s = merging
    connect(s)
    consent(s)
    s.service.task_update(s.card, checks="pending")
    assert reason(merge(s)) == "github_not_merge_ready"
    assert not [c for c in s.remote.calls if c[1].startswith(API)]


def test_the_pr_comes_from_the_card_never_the_request(merging):
    s = merging
    connect(s)
    consent(s)
    hostile = {
        "pr_url": "https://github.com/evil/project/pull/9",
        "repo": "evil/project",
        "owner": "evil",
        "number": 9,
        "sha": MOVED,
        "token": "attacker-token",
    }
    assert merge(s, **hostile)[0] == 200
    assert all(
        call[1].startswith(API) for call in s.remote.calls if "api.github.com/repos" in call[1]
    )
    assert s.remote.merges()[0][2]["json_body"]["sha"] == HEAD


@pytest.mark.parametrize("repo", ["gitlab.com/example/project", "evil.example/example/project"])
def test_a_pr_on_another_host_is_never_sent_to_github(merging, repo):
    s = merging
    connect(s)
    consent(s)
    s.service.task_update(s.card, repo=repo, pr=7, pr_state="open", pr_head=HEAD, checks="passing")
    assert reason(merge(s)) == "github_not_merge_ready"
    assert not [c for c in s.remote.calls if c[1].startswith("https://api.github.com/repos")]


@pytest.mark.parametrize("route", ["/api/github/merge", "/api/github/merge-consent"])
@pytest.mark.parametrize("guard", ["bearer", "origin", "csrf", "method"])
def test_merge_routes_keep_the_browser_session_guards(merging, route, guard):
    s = merging
    connect(s)
    if route.endswith("merge"):
        consent(s)
    payload = {"csrf": status(s)["csrf"], "card": s.card}
    headers = {"Content-Type": "application/json", "Origin": urls.APP_URL}
    if guard == "bearer":
        from tests.test_commonplace_public_surface import token

        auth = token(s)[2]
        assert request(
            s, route, "POST", raw=json.dumps(payload).encode(), headers={**headers, **auth}
        )[0] in (401, 403)
    elif guard == "origin":
        headers["Origin"] = "https://evil.example"
        assert (
            request(
                s, route, "POST", cookie=s.cookie, raw=json.dumps(payload).encode(), headers=headers
            )[0]
            == 403
        )
    elif guard == "csrf":
        payload["csrf"] = "wrong"
        assert (
            request(
                s, route, "POST", cookie=s.cookie, raw=json.dumps(payload).encode(), headers=headers
            )[0]
            == 403
        )
    else:
        assert request(s, route, "GET", cookie=s.cookie)[0] in (404, 405)
    assert not s.remote.merges()


def test_default_off_blocks_merge(merging, monkeypatch):
    s = merging
    connect(s)
    consent(s)
    monkeypatch.setenv("COMMONPLACE_GITHUB_CONNECT", "off")
    assert reason(merge(s)) == "github_unconfigured"
    assert not s.remote.merges()


def test_tokens_never_reach_responses_logs_or_storage(merging, caplog):
    s = merging
    caplog.set_level(logging.DEBUG)
    connect(s)
    consent(s)
    responses = [merge(s), write(s, "/api/github/merge-consent")]
    s.remote.head = MOVED
    responses.append(merge(s))
    raw = (
        "".join(repr(r) for r in responses)
        + json.dumps(status(s))
        + caplog.text
        + repr(s.db.audit_entries())
        + repr(rows(s, "github_grants"))
    )
    for token in (*s.remote.issued, "synthetic-app-secret"):
        assert token not in raw


def test_a_lapsed_unanswered_consent_token_is_revoked_by_the_worker(merging, monkeypatch):
    s = merging
    candidate = pending_merge_consent(s)
    first = s.remote.issued[0]
    s.db._conn.execute("UPDATE github_confirmations SET expires_at=1")
    assert status(s)["state"] == "connected"
    assert revoked_tokens(s) == []
    tick(s.gateway.github, monkeypatch)
    assert revoked_tokens(s) == [candidate]
    assert not rows(s, "github_confirmations")
    assert stored_token(s) == first
    assert status(s).get("merge") is False


def test_a_restart_cannot_forget_a_pending_consent_token(merging, monkeypatch):
    s = merging
    candidate = pending_merge_consent(s)
    worker = s.gateway.github
    worker.recover()
    assert status(s)["state"] == "connected"
    tick(worker, monkeypatch)
    assert revoked_tokens(s) == [candidate]
    assert not rows(s, "github_confirmations")
    assert status(s).get("merge") is False


def test_an_unreadable_lapsed_consent_is_purged_and_needs_acknowledgement(merging, monkeypatch):
    s = merging
    pending_merge_consent(s)
    s.db._conn.execute("UPDATE github_confirmations SET expires_at=1, secret=x'00'")
    tick(s.gateway.github, monkeypatch)
    assert not rows(s, "github_confirmations")
    assert rows(s, "github_lifecycle")[0]["unconfirmed"] == 1


def test_a_failed_commit_revokes_the_candidate_and_keeps_the_stored_token(merging, monkeypatch):
    import threading

    s = merging
    candidate = pending_merge_consent(s)
    first = s.remote.issued[0]
    worker = s.gateway.github
    real, entered = s.db._tx, {"n": 0}

    class Failing:
        def __init__(self):
            self.inner = real()

        def __enter__(self):
            return self.inner.__enter__()

        def __exit__(self, kind, exc, tb):
            if threading.current_thread() is not worker._heartbeat_thread:
                entered["n"] += 1
                if entered["n"] == 2 and exc is None:
                    failure = RuntimeError("commit failed")
                    self.inner.__exit__(RuntimeError, failure, None)
                    raise failure
            return self.inner.__exit__(kind, exc, tb)

    csrf = status(s)["csrf"]
    monkeypatch.setattr(s.db, "_tx", Failing)
    try:
        request(
            s,
            "/api/github/confirm",
            "POST",
            cookie=s.cookie,
            raw=json.dumps({"csrf": csrf, "card": s.card, "accept": True}).encode(),
            headers={"Content-Type": "application/json", "Origin": urls.APP_URL},
        )
    except Exception:  # noqa: BLE001 - the synthetic failure may close the connection
        pass
    monkeypatch.setattr(s.db, "_tx", real)
    assert entered["n"] >= 2
    assert stored_token(s) == first
    assert revoked_tokens(s) == [candidate]
    assert status(s).get("merge") is False


def test_a_head_the_person_did_not_confirm_is_refused_before_github(merging):
    s = merging
    connect(s)
    consent(s)
    assert reason(merge(s, head=MOVED)) == "github_head_moved"
    assert reason(write(s, "/api/github/merge")) == "github_head_moved"
    assert not [c for c in s.remote.calls if c[1].startswith(API)]


def test_a_card_with_an_open_question_is_not_merged_even_if_merge_ready(merging):
    s = merging
    connect(s)
    consent(s)
    s.service.task_handoff(
        s.card,
        to="owner",
        note="Ship this change to the main branch now?",
        context="Everything is ready and checks passed on the head.",
        options=["yes", "no"],
    )
    # Still a fresh passing report, but Needs shows a question, not Merge.
    assert reason(merge(s)) == "github_not_merge_ready"
    assert not [c for c in s.remote.calls if c[1].startswith(API)]


def test_a_token_rejected_at_github_asks_to_connect_again(merging):
    s = merging
    connect(s)
    consent(s)
    s.remote.repo_status = 401
    assert reason(merge(s)) == "github_not_connected"
    assert not s.remote.merges()


def test_an_unexpected_failure_is_audited_as_an_error_not_an_allow(merging, monkeypatch):
    s = merging
    connect(s)
    consent(s)

    def explode(*args, **kwargs):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(importlib.import_module("commonplace.github.merge"), "merge", explode)
    try:
        merge(s)
    except Exception:  # noqa: BLE001 - the unhandled failure may close the connection
        pass
    entry = [e for e in s.db.audit_entries() if e["action"] == "github.merge"][0]
    assert entry["decision"] == "error" and entry["reason"]


def sweep_ticks(worker, monkeypatch, turns):
    """Run heartbeat iterations; returns how many times the loop asked to wait again."""
    worker._stopped.set()
    worker._heartbeat_thread.join()
    worker._stopped.clear()
    waits = iter(turns)
    asked = []

    def wait(timeout):
        asked.append(timeout)
        return next(waits)

    with monkeypatch.context() as patch:
        patch.setattr(worker._stopped, "wait", wait)
        worker._heartbeat()
    return len(asked)


def test_a_sweep_that_hits_pending_is_retried_and_does_not_stop_the_worker(
    merging, monkeypatch, caplog
):
    s = merging
    candidate = pending_merge_consent(s)
    s.db._conn.execute("UPDATE github_confirmations SET expires_at=1")
    worker = s.gateway.github
    real, calls = worker.expire, []

    def flaky(uid):
        calls.append(uid)
        if len(calls) == 1:
            raise providers.SignInError("github_pending")
        return real(uid)

    monkeypatch.setattr(worker, "expire", flaky)
    caplog.set_level(logging.WARNING)
    asked = sweep_ticks(worker, monkeypatch, [False, False, True])
    # The first tick's sweep failed with a pending code; the loop must still reach the next ticks.
    assert asked == 3 and len(calls) == 2
    assert revoked_tokens(s) == [candidate]
    assert worker.ready and "lost its lease" not in caplog.text
    assert any(
        s.uid in r.getMessage() and "github_pending" in r.getMessage() for r in caplog.records
    )
    # Writes stay unfenced: a later state write is accepted by the same worker.
    assert write(s, "/api/github", "DELETE")[0] == 200


def test_a_failing_sweep_is_logged_with_user_and_code_only(merging, monkeypatch, caplog):
    s = merging
    candidate = pending_merge_consent(s)
    s.db._conn.execute("UPDATE github_confirmations SET expires_at=1")
    worker = s.gateway.github

    def failing(uid):
        raise providers.SignInError("github_key")

    monkeypatch.setattr(worker, "expire", failing)
    caplog.set_level(logging.WARNING)
    sweep_ticks(worker, monkeypatch, [False, True])
    messages = " ".join(r.getMessage() for r in caplog.records if r.name.startswith("commonplace"))
    assert s.uid in messages and "github_key" in messages
    assert candidate not in caplog.text and "synthetic-app-secret" not in caplog.text


def test_real_lease_loss_still_stops_the_heartbeat(merging, monkeypatch, caplog):
    s = merging
    worker = s.gateway.github
    s.db._conn.execute("UPDATE github_worker_lease SET expires_at=0")
    caplog.set_level(logging.WARNING)
    assert sweep_ticks(worker, monkeypatch, [False, False, True]) == 1
    assert "lost its lease" in caplog.text


class Crash(BaseException):
    """A synthetic process death: unwinds without any handler treating it as an error."""


def test_a_pull_request_token_rejection_asks_to_connect_again(merging):
    s = merging
    connect(s)
    consent(s)
    s.remote.pull_status = 401
    assert reason(merge(s)) == "github_not_connected"
    assert not s.remote.merges()


def test_an_accepted_candidate_is_never_revoked_by_the_sweep(merging, monkeypatch):
    s = merging
    connect(s)
    first = s.remote.issued[0]
    consent(s)
    accepted = s.remote.issued[-1]
    assert not rows(s, "github_confirmations")
    tick(s.gateway.github, monkeypatch)
    tick(s.gateway.github, monkeypatch)
    assert revoked_tokens(s) == [first]
    assert stored_token(s) == accepted
    assert merge(s)[0] == 200


def test_the_lapsed_record_outlives_the_revoke_it_guards(merging, monkeypatch):
    s = merging
    candidate = pending_merge_consent(s)
    s.db._conn.execute("UPDATE github_confirmations SET expires_at=1")
    during = []
    s.remote.on_revoke = lambda: during.append(len(rows(s, "github_confirmations")))
    tick(s.gateway.github, monkeypatch)
    assert during == [1] and revoked_tokens(s) == [candidate]
    assert not rows(s, "github_confirmations")


def test_a_crash_during_the_sweep_revoke_leaves_the_record_for_the_next_sweep(merging, monkeypatch):
    s = merging
    candidate = pending_merge_consent(s)
    s.db._conn.execute("UPDATE github_confirmations SET expires_at=1")

    def die():
        raise Crash

    s.remote.on_revoke = die
    with pytest.raises(Crash):
        tick(s.gateway.github, monkeypatch)
    assert len(rows(s, "github_confirmations")) == 1
    s.remote.on_revoke = None
    s.gateway.github.recover()
    tick(s.gateway.github, monkeypatch)
    assert revoked_tokens(s) == [candidate, candidate]
    assert not rows(s, "github_confirmations")


def test_a_declined_candidate_record_outlives_its_revoke(merging):
    s = merging
    candidate = pending_merge_consent(s)
    during = []
    s.remote.on_revoke = lambda: during.append(len(rows(s, "github_confirmations")))
    assert write(s, "/api/github/confirm", payload={"accept": False})[0] == 200
    assert during == [1] and revoked_tokens(s) == [candidate]
    assert not rows(s, "github_confirmations")


def test_a_lease_lost_while_accepting_leaves_the_candidate_recorded(merging, monkeypatch):
    import threading

    s = merging
    candidate = pending_merge_consent(s)
    worker = s.gateway.github
    real, fences = worker._fence, {"n": 0}

    def fence(conn):
        if threading.current_thread() is worker._heartbeat_thread:
            return real(conn)
        fences["n"] += 1
        if fences["n"] >= 2:
            raise providers.SignInError("github_pending")
        return real(conn)

    csrf = status(s)["csrf"]
    monkeypatch.setattr(worker, "_fence", fence)
    try:
        request(
            s,
            "/api/github/confirm",
            "POST",
            cookie=s.cookie,
            raw=json.dumps({"csrf": csrf, "card": s.card, "accept": True}).encode(),
            headers={"Content-Type": "application/json", "Origin": urls.APP_URL},
        )
    except Exception:  # noqa: BLE001 - the fenced worker may close the connection
        pass
    monkeypatch.setattr(worker, "_fence", real)
    assert revoked_tokens(s) == []
    # No accepted swap and no revoke happened, yet the live token is still recorded.
    assert len(rows(s, "github_confirmations")) == 1
    s.gateway.github.recover()
    tick(s.gateway.github, monkeypatch)
    assert revoked_tokens(s) == [candidate]
    assert not rows(s, "github_confirmations")


def test_a_recovered_pending_candidate_needs_no_acknowledgement_once_revoked(merging, monkeypatch):
    s = merging
    candidate = pending_merge_consent(s)
    s.gateway.github.recover()
    assert rows(s, "github_lifecycle")[0]["unconfirmed"] == 0
    tick(s.gateway.github, monkeypatch)
    assert revoked_tokens(s) == [candidate]
    assert status(s).get("acknowledge_required") is False


def test_a_failed_sweep_revoke_still_requires_acknowledgement(merging, monkeypatch):
    s = merging
    pending_merge_consent(s)
    s.remote.status = 500
    s.db._conn.execute("UPDATE github_confirmations SET expires_at=1")
    tick(s.gateway.github, monkeypatch)
    assert not rows(s, "github_confirmations")
    assert rows(s, "github_lifecycle")[0]["unconfirmed"] == 1
    assert status(s).get("acknowledge_required") is True


def test_a_lapsed_candidate_that_is_the_live_grant_is_neither_revoked_nor_flagged(
    merging, monkeypatch
):
    s = merging
    pending_merge_consent(s)
    live = stored_token(s)
    with s.db._lock, s.db._tx() as conn:
        row = conn.execute("SELECT * FROM github_confirmations").fetchone()
        value = s.gateway.github.store.decrypt(conn, s.uid, "confirmation", row["secret"])
        value["token"] = live
        conn.execute(
            "UPDATE github_confirmations SET expires_at=1, secret=?",
            (s.gateway.github.store.encrypt(conn, s.uid, "confirmation", value),),
        )
    tick(s.gateway.github, monkeypatch)
    assert revoked_tokens(s) == []
    assert not rows(s, "github_confirmations")
    assert stored_token(s) == live
    assert rows(s, "github_lifecycle")[0]["unconfirmed"] == 0

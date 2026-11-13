"""The consented card queue uses the same action projection as the web."""

import json

import pytest

from commonplace import events, sessions
from commonplace.boards.display import with_urls
from tests.test_commonplace_boards import api, service
from tests.test_commonplace_oauth import Client
from tests.test_commonplace_person_actions import (
    APPROVE,
    CHAT,
    DECIDE,
    ask,
    call,
    grant,
    hub_fixture as hub_fixture,
    make_proposal,
    member_client,
    phone as phone,
)


def queue(phone, pair):
    return api(phone.hub, pair["access_token"], "GET", "/api/needs-me")


def items(report):
    return {
        card["number"]: card
        for bucket in (
            "asks",
            "done_review",
            "backlog_from_agents",
            "ready_to_merge",
            "stale",
            "plans",
            "approvals",
        )
        for card in report[bucket]
    }


def proposal_for_owner(phone):
    card = make_proposal(phone)
    with phone.owner.store.transaction() as conn:
        conn.execute(
            "UPDATE tasks SET proposed_to=? WHERE id=?",
            (phone.owner.principal.actor, card["number"]),
        )
    return card


@pytest.mark.parametrize("capabilities", [(DECIDE,), (APPROVE,), (DECIDE, APPROVE)])
def test_consented_phone_gets_web_actions_per_capability(phone, capabilities):
    pair = grant(phone, capabilities=capabilities)
    question = ask(phone)
    proposal = proposal_for_owner(phone)
    completion = phone.owner.seed_card(
        board="work", title="Completed change", body="Done?", status="review"
    )
    status, report = queue(phone, pair)
    assert status == 200, report
    web_status, web = api(
        phone.hub,
        {"Cookie": f"{sessions.COOKIE}={phone.cookies[sessions.COOKIE]}"},
        "GET",
        "/api/needs-me",
    )
    assert web_status == 200, web
    allowed = set()
    if DECIDE in capabilities:
        allowed.update(("accept", "answer"))
    if APPROVE in capabilities:
        allowed.update(("approve", "reject"))
    projected, original = items(report), items(web)
    for card in (question, proposal, completion):
        number = card["number"]
        expected = [action for action in original[number]["actions"] if action in allowed]
        assert projected[number]["actions"] == expected
        assert projected[number]["version"] == original[number]["version"]
        for field in ("ask_options", "review_kind", "proposal_status", "approval_kind"):
            assert projected[number].get(field) == original[number].get(field)
        assert projected[number] == {**original[number], "actions": expected}
    app = phone.hub.gateway.authenticate("Bearer " + pair["access_token"])
    assert not app.person and set(app.policy.scopes) == {"memory:read"}


@pytest.mark.parametrize("kind", ["unchecked", "chat-only", "ordinary-oauth", "off"])
def test_without_card_consent_keeps_exact_existing_response(phone, monkeypatch, kind):
    ask(phone)
    if kind == "ordinary-oauth":
        client = Client(phone.hub)
        pair = client.grant(client.register(client_name="Ordinary client"))
    else:
        capabilities = [CHAT] if kind == "chat-only" else [DECIDE, APPROVE]
        pair = grant(phone, capabilities=capabilities, checked=[] if kind == "unchecked" else None)
    if kind == "off":
        monkeypatch.delenv("COMMONPLACE_PHONE_ACTIONS")
    app = phone.hub.gateway.authenticate("Bearer " + pair["access_token"])
    existing = service(phone.hub, app)
    expected = with_urls(existing.ctx, existing.needs_me())
    assert queue(phone, pair) == (200, expected)
    assert all(card["actions"] == [] for card in items(expected).values())


@pytest.mark.parametrize(
    "kind", ["agent", "child", "seat", "coordinator", "machine", "worker", "pat"]
)
def test_non_person_credentials_gain_no_projection(phone, kind):
    pair = grant(phone)
    card = ask(phone)
    app = phone.hub.gateway.authenticate("Bearer " + pair["access_token"])
    db = phone.hub.db
    if kind == "pat":
        pair = {"access_token": phone.hub.owner_token("ordinary")}
    elif kind == "coordinator":
        with phone.owner.store.transaction() as conn:
            conn.execute("UPDATE boards SET coordinator=? WHERE slug='work'", (app.actor,))
    else:
        with db._lock:
            if kind == "agent":
                db._conn.execute(
                    "UPDATE connections SET started_by='agent' WHERE id=?", (app.connection_id,)
                )
            elif kind == "child":
                from commonplace.policy import Policy

                parent = db.authenticate_token(phone.hub.owner_token("parent"))
                _, token = db.create_token(
                    workspace_ref=phone.hub.workspace,
                    user_ref=phone.hub.admin,
                    actor=phone.hub.admin,
                    name="child",
                    client="test",
                    parent=parent.connection_id,
                    policy=Policy.build(scopes=["memory:read"], trust="trusted"),
                )
                pair = {"access_token": token}
            elif kind == "seat":
                from commonplace.decide_grant import SEAT_CLIENT

                db._conn.execute(
                    "UPDATE connections SET client=? WHERE id=?", (SEAT_CLIENT, app.connection_id)
                )
            else:
                db._conn.execute(
                    "INSERT INTO runner_connections(connection_id,machine_id,kind) VALUES (?,?,?)",
                    (app.connection_id, "synthetic", kind),
                )
    current = phone.hub.gateway.authenticate("Bearer " + pair["access_token"])
    existing = service(phone.hub, current)
    assert queue(phone, pair) == (200, with_urls(existing.ctx, existing.needs_me()))
    assert call(phone, pair, card)[0] == 404


@pytest.mark.parametrize("capability", [DECIDE, APPROVE])
def test_revoking_one_capability_removes_only_its_actions(phone, capability):
    pair = grant(phone, capabilities=[DECIDE, APPROVE])
    question, proposal = ask(phone), proposal_for_owner(phone)
    assert queue(phone, pair)[0] == 200
    app = phone.hub.gateway.authenticate("Bearer " + pair["access_token"])
    with phone.hub.db._lock:
        phone.hub.db._conn.execute(
            "UPDATE oauth_person_grants SET revoked_at=1 WHERE connection_id=? AND capability=?",
            (app.connection_id, capability),
        )
    status, report = queue(phone, pair)
    assert status == 200, report
    projected = items(report)
    assert projected[question["number"]]["actions"] == ([] if capability == DECIDE else ["answer"])
    assert projected[proposal["number"]]["actions"] == (
        [] if capability == APPROVE else ["approve", "reject"]
    )
    card = question if capability == DECIDE else proposal
    action = "decide" if capability == DECIDE else "approve"
    body = {
        "version": card["version"],
        **({"reply": "Proceed"} if action == "decide" else {"approve": True}),
    }
    assert call(phone, pair, card, action, body)[0] == 403


def completion_for(phone, worker, reviewer, *, board="work", title="Completion review"):
    card = phone.owner.task_add(
        board=board,
        title=title,
        reviewer="user:" + reviewer,
        body="The completed change is ready for review.",
    )
    worker.task_take_next(task=card["number"])
    card = worker.task_update(card["number"], status="done")
    assert card["review_kind"] == "completion"
    return card


def test_member_sees_only_chosen_readable_reviews_without_title_leaks(phone):
    question, proposal = ask(phone), proposal_for_owner(phone)
    member = member_client(phone)
    worker = service(phone.hub, phone.hub.db.authenticate_token(phone.hub.owner_token("worker")))
    own = completion_for(phone, worker, member)
    other = completion_for(phone, worker, phone.hub.admin, title="Other person's review")
    phone.owner.board_add(slug="private-review", wing="private_notes")
    hidden = completion_for(
        phone, worker, member, board="private-review", title="Unreadable review title"
    )
    pair = grant(phone)
    status, report = queue(phone, pair)
    assert status == 200, report
    projected = items(report)
    assert set(projected) == {own["number"]}
    assert projected[own["number"]]["actions"] == ["accept", "answer"]
    assert hidden["title"] not in json.dumps(report)
    assert report["count"] == 1
    for card in (question, other):
        assert call(phone, pair, card)[0] == 403
    assert call(phone, pair, hidden)[0] == 404
    assert (
        call(phone, pair, proposal, "approve", {"version": proposal["version"], "approve": True})[0]
        == 403
    )
    assert (
        call(
            phone,
            pair,
            own,
            body={
                "version": projected[own["number"]]["version"],
                "reply": "Accepted",
                "then": "done",
            },
        )[0]
        == 200
    )


def test_member_without_web_queue_keeps_web_refusal(phone):
    ask(phone)
    member_client(phone)
    pair = grant(phone)
    assert queue(phone, pair)[0] == 403


@pytest.mark.parametrize("binding", ["workspace", "user", "client"])
def test_projection_requires_current_capability_binding(phone, binding):
    pair = grant(phone)
    ask(phone)
    app = phone.hub.gateway.authenticate("Bearer " + pair["access_token"])
    db = phone.hub.db
    other_client = phone.register(client_name="Other client") if binding == "client" else None
    with db._lock:
        if binding == "workspace":
            workspace = db.create_workspace("other", phone.hub.admin)
            db._conn.execute(
                "UPDATE oauth_person_grants SET workspace_id=? WHERE connection_id=?",
                (workspace, app.connection_id),
            )
        elif binding == "user":
            user = db.create_user("other@example.test")
            db._conn.execute(
                "UPDATE oauth_person_grants SET user_id=? WHERE connection_id=?",
                (user, app.connection_id),
            )
        else:
            db._conn.execute(
                "UPDATE oauth_person_grants SET client_id=? WHERE connection_id=?",
                (other_client, app.connection_id),
            )
    existing = service(phone.hub, app)
    assert queue(phone, pair) == (200, with_urls(existing.ctx, existing.needs_me()))


def test_plan_and_origin_release_offer_only_the_web_actions(phone):
    pair = grant(phone, capabilities=[APPROVE])
    plan = phone.owner.task_add(board="work", title="Plan awaiting review", labels=["plan"])
    unreleased = make_proposal(phone)
    with phone.owner.store.transaction() as conn:
        conn.execute(
            "UPDATE tasks SET restricted_origin=1,approved_by=NULL WHERE id=?",
            (unreleased["number"],),
        )
    status, report = queue(phone, pair)
    assert status == 200, report
    projected = items(report)
    original = items(phone.owner.needs_me(cards_only=True))
    for card in (plan, unreleased):
        number = card["number"]
        assert projected[number]["actions"] == original[number]["actions"]
        assert projected[number]["version"] == original[number]["version"]
    assert projected[plan["number"]]["actions"] == ["approve", "reject"]
    assert projected[unreleased["number"]]["actions"] == ["approve"]
    assert projected[unreleased["number"]]["approval_kind"] == "origin"


@pytest.mark.parametrize("action", ["decide", "approve"])
def test_projected_version_is_required_by_final_write_authority(phone, action):
    pair = grant(phone)
    card = ask(phone) if action == "decide" else proposal_for_owner(phone)
    status, report = queue(phone, pair)
    assert status == 200, report
    item = items(report)[card["number"]]
    assert ("answer" if action == "decide" else "approve") in item["actions"]
    body = {
        "version": item["version"],
        **(
            {"choice": item["ask_options"][0]}
            if action == "decide"
            else {"approve": True, "reason": "Reviewed"}
        ),
    }
    phone.owner.task_update(card["number"], status_line="Changed since listing")
    assert call(phone, pair, card, action, body)[0] == 409
    body["version"] = items(queue(phone, pair)[1])[card["number"]]["version"]
    first = call(phone, pair, card, action, body)
    assert first[0] == 200, first
    assert call(phone, pair, card, action, body) == first
    assert call(phone, pair, card, action, {**body, "version": body["version"] + 1})[0] == 409


def test_same_builder_and_no_other_person_queue_or_route_authority(phone, monkeypatch):
    from commonplace.boards import service as board_service

    ordinary = grant(phone, checked=[])
    pair = grant(phone, capabilities=[DECIDE, APPROVE])
    card = ask(phone)
    called = []
    build = board_service.needs_me

    def checked(store, principal, **kwargs):
        called.append(principal.person)
        return build(store, principal, **kwargs)

    monkeypatch.setattr(board_service, "needs_me", checked)
    before = events.after(phone.hub.db, phone.hub.workspace)
    status, report = queue(phone, pair)
    assert status == 200 and called == [True]
    assert items(report)[card["number"]]["actions"] == ["answer"]
    assert set(report) == {
        "asks",
        "done_review",
        "backlog_from_agents",
        "ready_to_merge",
        "stale",
        "plans",
        "approvals",
        "count",
        "approvals_truncated",
    }
    assert events.after(phone.hub.db, phone.hub.workspace) == before
    with phone.owner.store.connection() as conn:
        assert not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='person_card_receipts'"
        ).fetchone()
    for path in ("/api/events", "/api/account", "/api/chat", "/api/settings/workspace"):
        expected = api(phone.hub, ordinary["access_token"], "GET", path)
        assert expected[0] in (403, 404)
        assert api(phone.hub, pair["access_token"], "GET", path) == expected
    existing = service(phone.hub, phone.hub.gateway.authenticate("Bearer " + pair["access_token"]))
    assert api(phone.hub, pair["access_token"], "GET", "/api/needs-me/digest")[
        1
    ] == existing.needs_me(format="digest")

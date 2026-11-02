"""First signup lands on an owned space through ordinary public auth routes."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from commonplace.boards.store import BoardStore
from tests.test_commonplace_email_auth import fresh as fresh
from tests.test_commonplace_social import enable, finish, remote as remote, start

LANDING = "/app/#/board/commonplace"


def signin(
    client, mailbox, remote, channel, *, email="new@example.test", subject=123, next_path=""
):
    if channel == "email":
        assert client.ask(email, next=next_path)[0] == 202
        return client.verify(mailbox.link())
    remote.github.update(id=subject, name="New Person")
    remote.emails = [{"email": email, "verified": True, "primary": True}]
    return finish(client, start(client, remote, "github", next_path=next_path))


@pytest.fixture
def signup(fresh, remote, monkeypatch):
    monkeypatch.setenv("COMMONPLACE_SIGNUP", "open")
    enable(monkeypatch, "github")
    client, mailbox = fresh()

    def run(channel="email", **kwargs):
        status, headers, body = signin(client, mailbox, remote, channel, **kwargs)
        assert status == 303 and not body and headers["Set-Cookie"], headers["Location"]
        return headers["Set-Cookie"].split(";", 1)[0], headers["Location"]

    return client, mailbox, run


def api(client, cookie, path="/api/boards", method="GET", data=None, *, token=None):
    headers = {"Authorization": "Bearer " + token} if token else {}
    status, _, body = client.request(path, method, cookie=cookie, data=data, headers=headers)
    return status, json.loads(body) if body else None


@pytest.mark.parametrize("channel", ["github", "email"])
def test_fresh_signup_creates_one_owned_space_and_lands_in_board(signup, channel):
    client, _, run = signup
    assert client.db.users() == client.db.workspaces() == []
    cookie, landing = run(channel)
    assert landing == LANDING
    me = client.me(cookie)
    assert client.db.user(me["user"])["is_admin"] == 0
    assert client.db.workspace(me["workspace"])["personal_of"] == me["user"]
    assert client.db.role(me["workspace"], me["user"]) == "owner"
    status, data = api(client, cookie)
    assert status == 200
    [board] = data["boards"]
    assert (board["slug"], board["name"], board["wing"]) == (
        "commonplace",
        "Commonplace",
        "shared",
    )
    assert board["can_configure"] and not any(board["counts"].values())
    store = BoardStore(client.gateway.workspace_dir(me["workspace"]))
    with store.connection() as conn:
        [row] = conn.execute("SELECT * FROM boards").fetchall()
        assert row["stamp_user"] == me["user"]
        assert row["stamp_connection"] is None and row["stamp_trust"] == "trusted"
    assert client.request(landing.split("#")[0], cookie=cookie)[0] == 200
    assert api(client, cookie, "/api/boards/commonplace/cards") == (
        200,
        {"cards": [], "linked": []},
    )


@pytest.mark.parametrize("channel", ["github", "email"])
def test_returning_signup_preserves_spaces_and_home_or_explicit_route(signup, channel):
    client, _, run = signup
    cookie, _ = run(channel)
    me = client.me(cookie)
    assert (
        api(client, cookie, method="POST", data={"slug": "field-notes", "name": "Field notes"})[0]
        == 201
    )
    before = api(client, cookie)[1]
    assert api(client, cookie, "/app/logout", "POST", {})[0] == 204
    for next_path, expected in [
        ("", "/app/"),
        ("/app/#/board/field-notes", "/app/#/board/field-notes"),
    ]:
        cookie, landing = run(channel, next_path=next_path)
        assert landing == expected
        assert client.me(cookie)["workspace"] == me["workspace"]
        assert api(client, cookie)[1] == before
    assert len(client.db.users()) == len(client.db.workspaces()) == 1


@pytest.mark.parametrize("channel", ["github", "email"])
@pytest.mark.parametrize("destination", ["/app/#/settings", "/oauth/authorize?client_id=test"])
def test_new_signup_provisions_space_without_overriding_destination(signup, channel, destination):
    client, _, run = signup
    cookie, landing = run(channel, next_path=destination)
    assert landing.endswith(destination)
    assert len(api(client, cookie)[1]["boards"]) == 1


@pytest.mark.parametrize("channel", ["github", "email"])
def test_two_signup_workspaces_keep_boards_and_cards_private(signup, channel):
    client, _, run = signup
    first, _ = run(channel)
    assert (
        api(
            client,
            first,
            method="POST",
            data={"slug": "private-project", "name": "Private project"},
        )[0]
        == 201
    )
    status, card = api(
        client, first, "/api/boards/private-project/cards", "POST", {"title": "PRIVATE_CARD"}
    )
    assert status == 201
    second, landing = run(channel, email="second@example.test", subject=456)
    assert landing == LANDING
    assert client.me(first)["workspace"] != client.me(second)["workspace"]
    status, data = api(client, second)
    assert status == 200 and [b["slug"] for b in data["boards"]] == ["commonplace"]
    assert api(client, second, "/api/boards/private-project/cards")[0] == 404
    assert api(client, second, f"/api/cards/{card['number']}")[0] == 404
    assert "PRIVATE_CARD" not in str(api(client, second, "/api/cards"))
    assert api(client, first, f"/api/cards/{card['number']}")[1]["title"] == "PRIVATE_CARD"


def test_linking_another_provider_preserves_existing_spaces(signup):
    client, _, run = signup
    cookie, _ = run("email")
    user = client.me(cookie)["user"]
    before = api(client, cookie)[1]
    cookie, landing = run("github")
    assert landing == "/app/" and client.me(cookie)["user"] == user
    assert api(client, cookie)[1] == before
    assert len(client.db.users()) == len(client.db.workspaces()) == 1


def test_concurrent_first_challenge_is_consumed_once_and_creates_one_space(signup):
    client, mailbox, _ = signup
    assert client.ask("new@example.test")[0] == 202
    link = mailbox.link()
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.verify(link), range(2)))
    assert sorted(response[0] for response in responses) == [303, 400]
    success = next(response for response in responses if response[0] == 303)
    assert success[1]["Location"] == LANDING
    cookie = success[1]["Set-Cookie"].split(";", 1)[0]
    assert len(client.db.users()) == len(client.db.workspaces()) == 1
    assert len(api(client, cookie)[1]["boards"]) == 1


def test_agents_keep_space_creation_authority_and_child_approval(signup):
    client, _, run = signup
    cookie, _ = run()
    tokens = []
    for data in [{"name": "parent"}, {"name": "child", "parent": "parent"}]:
        status, created = api(client, cookie, "/api/connections", "POST", data)
        assert status == 201
        tokens.append(created["token"])
    parent, child = tokens
    assert api(client, None, method="POST", data={"slug": "agent-space"}, token=parent)[0] == 403
    assert (
        api(client, cookie, method="POST", data={"slug": "owner-space", "name": "Work"})[0] == 201
    )
    status, proposal = api(
        client,
        None,
        "/api/boards/owner-space/cards",
        "POST",
        {"title": "Child proposal"},
        token=child,
    )
    assert status == 201 and proposal["proposal_status"] == "pending"
    path = f"/api/cards/{proposal['number']}"
    patch = {"status": "doing", "version": proposal["version"]}
    assert api(client, None, path, "PATCH", patch, token=child)[0] == 403
    assert api(client, None, path + "/approve", "POST", {}, token=child)[0] == 403
    status, approved = api(client, None, path + "/approve", "POST", {}, token=parent)
    assert status == 200 and approved["approved_by"] == "conn:parent"
    status, claimed = api(
        client,
        None,
        path,
        "PATCH",
        {"status": "doing", "version": approved["version"]},
        token=child,
    )
    assert status == 200 and claimed["assignee"] == "conn:child"

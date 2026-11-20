"""Model modes (card #617, slice 1): the precedence matrix, private machines, the owner-only
setting, a person's own connections, the missing-connection rule and what a chat answer records."""

import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

import pytest

from commonplace.projects import model_modes
from mempalace.sqlite_crypto import connect, row_factory
from tests._relay_helpers import relay_hub_fixture  # noqa: F401

SETTING = "/api/settings/model-mode"
CONNECTIONS = "/api/me/model-connections"


def _machine_row(h, machine):
    return h.db._conn.execute(
        "SELECT * FROM runner_machines WHERE id=?", (machine["machine_id"],)
    ).fetchone()


def _record(h, scope, mode, allow=False):
    with h.db._lock, h.db._tx() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO model_mode_settings VALUES (?,?,?,?,?,?)",
            (h.workspace, scope, mode, int(allow), h.admin, "2026-11-20T00:00:00Z"),
        )


def _connection(h, user, machine, provider="claude", label="my subscription"):
    with h.db._lock, h.db._tx() as conn:
        taken = conn.execute("SELECT COUNT(*) FROM model_connections").fetchone()[0]
        conn.execute(
            "INSERT INTO model_connections VALUES (?,?,?,?,?,?,?,NULL)",
            (
                f"mc_{taken + 1:016x}",
                h.workspace,
                user,
                provider,
                label,
                machine["machine_id"],
                "2026-11-20T00:00:00Z",
            ),
        )


def _pref(h, user, on):
    with h.db._lock, h.db._tx() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO model_person_prefs VALUES (?,?,?,?)",
            (h.workspace, user, int(on), "2026-11-20T00:00:00Z"),
        )


def _decide(h, user, **kwargs):
    return model_modes.decide(h.db, h.workspace, "work", person=user, provider="claude", **kwargs)


# ---------------------------------------------------------------- the precedence matrix

# (workspace record, project record, person switch, person has a connection, expected)
# a record is (mode, allow_person_override); expected is (mode, source, account kind) or a refusal code
MATRIX = [
    (None, None, False, False, ("legacy", "legacy", None)),
    (None, None, True, True, ("legacy", "legacy", None)),
    (("org", False), None, False, True, ("org", "workspace", "org")),
    (("personal", False), None, False, True, ("personal", "workspace", "person")),
    (("personal", False), None, False, False, "connection_missing"),
    (("hybrid", True), None, True, True, ("hybrid", "person", "person")),
    (("hybrid", True), None, True, False, ("hybrid", "workspace", "org")),
    (("hybrid", True), None, False, True, ("hybrid", "workspace", "org")),
    (("hybrid", False), None, True, True, ("hybrid", "workspace", "org")),
    # the project's record wins as a whole
    (("personal", False), ("org", False), False, False, ("org", "project", "org")),
    (("org", False), ("personal", False), False, True, ("personal", "project", "person")),
    (("org", False), ("personal", False), False, False, "connection_missing"),
    (("org", False), ("hybrid", True), True, True, ("hybrid", "person", "person")),
    (("org", False), ("hybrid", False), True, True, ("hybrid", "project", "org")),
    (("hybrid", True), ("hybrid", False), True, True, ("hybrid", "project", "org")),
    (("personal", False), None, True, True, ("personal", "workspace", "person")),
]


@pytest.mark.parametrize("workspace,project,use_own,has_connection,expected", MATRIX)
def test_precedence_matrix(relay_hub, workspace, project, use_own, has_connection, expected):
    h = relay_hub
    machine = h.machine(label="own-1")
    if workspace:
        _record(h, "*", *workspace)
    if project:
        _record(h, "work", *project)
    if has_connection:
        _connection(h, h.admin, machine)
    _pref(h, h.admin, use_own)
    decision = _decide(h, h.admin)
    if isinstance(expected, str):
        assert decision.refusal is not None and decision.refusal.code == expected
        assert decision.kind is None
    else:
        assert decision.refusal is None
        assert (decision.mode, decision.mode_source, decision.kind) == expected


def test_a_start_with_no_person_is_never_billed_to_the_org_in_personal_mode(relay_hub):
    h = relay_hub
    _record(h, "*", "personal")
    decision = _decide(h, None)
    assert decision.refusal.code == "no_requesting_person"
    _record(h, "*", "org")
    assert _decide(h, None).kind == "org"


def test_a_connection_for_another_provider_does_not_count(relay_hub):
    h = relay_hub
    _record(h, "*", "personal")
    _connection(h, h.admin, h.machine(label="own-1"), provider="other")
    assert _decide(h, h.admin).refusal.code == "connection_missing"


def test_a_revoked_machine_ends_the_connection(relay_hub):
    h = relay_hub
    machine = h.machine(label="own-1")
    _record(h, "*", "personal")
    _connection(h, h.admin, machine)
    assert _decide(h, h.admin).kind == "person"
    with h.db._lock, h.db._tx() as conn:
        conn.execute(
            "UPDATE runner_machines SET revoked_at='2026-11-20T00:00:00Z' WHERE id=?",
            (machine["machine_id"],),
        )
    assert _decide(h, h.admin).refusal.code == "connection_missing"


# ---------------------------------------------------------------- private machines, every mode


@pytest.mark.parametrize("mode", [None, "org", "personal", "hybrid"])
def test_a_private_machine_does_not_exist_for_anyone_else(relay_hub, mode):
    h = relay_hub
    machine = h.machine(label="own-1")
    _connection(h, h.admin, machine)
    if mode:
        _record(h, "*", mode, allow=mode == "hybrid")
    refusal = _decide(h, h.member_user, machine=_machine_row(h, machine)).refusal
    assert refusal is not None and refusal.code == "machine_not_found"
    assert "own-1" not in refusal.message and "subscription" not in refusal.message


def test_a_private_machine_serves_its_owner_only_where_the_mode_says(relay_hub):
    h = relay_hub
    machine = h.machine(label="own-1")
    row = _machine_row(h, machine)
    _connection(h, h.admin, machine)
    unset = _decide(h, h.admin, machine=row)
    assert (unset.kind, unset.label, unset.mode) == ("person", "my subscription", "legacy")
    _record(h, "*", "org")
    assert _decide(h, h.admin, machine=row).refusal.code == "machine_not_org_account"
    _record(h, "*", "personal")
    assert _decide(h, h.admin, machine=row).kind == "person"


def test_personal_mode_swaps_in_the_persons_own_machine_only_where_allowed(relay_hub):
    h = relay_hub
    org = h.machine(label="org-1")
    own = h.machine(label="own-1")
    _connection(h, h.admin, own)
    _record(h, "*", "personal")
    row = _machine_row(h, org)
    assert _decide(h, h.admin, machine=row).refusal.code == "machine_not_your_account"
    swapped = _decide(h, h.admin, machine=row, substitute=True)
    assert swapped.machine_id == own["machine_id"] and swapped.kind == "person"


def test_org_mode_keeps_private_machines_out_of_the_pool(relay_hub):
    h = relay_hub
    org, own = h.machine(label="org-1"), h.machine(label="own-1")
    _connection(h, h.admin, own)
    assert model_modes.private_machine_ids(h.db, h.workspace) == {own["machine_id"]}
    _record(h, "*", "org")
    assert _decide(h, h.admin, machine=_machine_row(h, org)).kind == "org"


def test_an_unreadable_store_refuses_instead_of_guessing(relay_hub):
    h = relay_hub
    with h.db._lock, h.db._tx() as conn:
        conn.execute("DROP TABLE model_mode_settings")
    with pytest.raises(model_modes.ModeUnavailable):
        _decide(h, h.admin)


# ---------------------------------------------------------------- the owner-only setting


def test_the_owner_sets_reads_and_clears_the_mode(relay_hub):
    h = relay_hub
    assert h.request(h.owner, "GET", SETTING)[:2] == (200, {"workspace": None, "projects": {}})
    status, body, _ = h.request(h.owner, "PUT", SETTING, {"mode": "personal"})
    assert status == 200 and body["workspace"] == {
        "mode": "personal",
        "allow_person_override": False,
        "scope": "workspace",
    }
    status, body, _ = h.request(
        h.owner, "PUT", SETTING, {"board": "work", "mode": "hybrid", "allow_person_override": True}
    )
    assert status == 200 and body["projects"]["work"]["allow_person_override"] is True
    assert model_modes.effective_mode(h.db, h.workspace, "work")["source"] == "project"
    status, body, _ = h.request(h.owner, "DELETE", SETTING + "?board=work")
    assert status == 200 and body["projects"] == {}
    assert model_modes.effective_mode(h.db, h.workspace, "work")["source"] == "workspace"
    assert h.request(h.owner, "DELETE", SETTING + "?board=work")[0] == 404
    assert h.request(h.owner, "DELETE", SETTING)[0] == 200
    assert model_modes.effective_mode(h.db, h.workspace, "work")["mode"] == "unset"
    audited = h.db._conn.execute(
        "SELECT action FROM audit_log WHERE action LIKE 'models.mode%' ORDER BY rowid"
    ).fetchall()
    assert [r[0] for r in audited] == [
        "models.mode",
        "models.mode",
        "models.mode_clear",
        "models.mode_clear",
    ]


@pytest.mark.parametrize("who", ["member", "agent", "machine"])
@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
def test_only_the_owner_can_touch_the_setting_and_everyone_else_gets_the_unknown_route_answer(
    relay_hub, who, method
):
    h = relay_hub
    h.request(h.owner, "PUT", SETTING, {"mode": "org"})
    auth = {
        "member": h.member,
        "agent": h.a1,
        "machine": h.machine().get("machine_token", ""),
    }[who]
    body = {"mode": "personal"} if method == "PUT" else None
    status, answer, _ = h.request(auth, method, SETTING, body)
    assert status in (404, 401, 403)
    if status == 404:
        assert answer == {"error": "not found"}
    assert model_modes.effective_mode(h.db, h.workspace, None)["mode"] == "org"


def test_another_workspaces_owner_only_ever_touches_their_own_records(relay_hub):
    h = relay_hub
    h.request(h.owner, "PUT", SETTING, {"mode": "org"})
    assert h.request(h.other, "PUT", SETTING, {"mode": "personal"})[0] == 200
    assert model_modes.effective_mode(h.db, h.workspace, None)["mode"] == "org"
    assert model_modes.effective_mode(h.db, h.other_workspace, None)["mode"] == "personal"
    assert h.request(h.other, "PUT", SETTING, {"board": "work", "mode": "org"})[0] == 404


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"mode": "everyone"},
        {"mode": "org", "allow_person_override": True},
        {"mode": "hybrid", "allow_person_override": "yes"},
        {"mode": "org", "extra": 1},
        [],
    ],
)
def test_an_invalid_record_is_refused_and_nothing_is_written(relay_hub, body):
    h = relay_hub
    status, _, _ = h.request(h.owner, "PUT", SETTING, body)
    assert status == 400
    assert h.request(h.owner, "GET", SETTING)[1] == {"workspace": None, "projects": {}}


def test_a_project_that_does_not_exist_is_not_found(relay_hub):
    h = relay_hub
    assert h.request(h.owner, "PUT", SETTING, {"board": "nope", "mode": "org"})[0] == 404


# ---------------------------------------------------------------- a person's own connections


def test_a_person_registers_lists_and_revokes_their_own_connection(relay_hub):
    h = relay_hub
    machine = h.machine(label="own-1")
    status, body, _ = h.request(
        h.owner,
        "POST",
        CONNECTIONS,
        {"provider": "claude", "label": "my subscription", "machine": "own-1"},
    )
    assert status == 201 and body["machine"] == machine["machine_id"]
    assert "token" not in json.dumps(body) and "key" not in body
    listed = h.request(h.owner, "GET", CONNECTIONS)[1]
    assert [c["label"] for c in listed["connections"]] == ["my subscription"]
    assert listed["use_own"] is False
    assert (
        h.request(
            h.owner,
            "POST",
            CONNECTIONS,
            {"provider": "claude", "label": "again", "machine": "own-1"},
        )[0]
        == 400
    )
    assert h.request(h.owner, "DELETE", CONNECTIONS + "?id=" + body["id"])[0] == 200
    assert h.request(h.owner, "GET", CONNECTIONS)[1]["connections"] == []


def test_nobody_else_sees_or_removes_a_persons_connection(relay_hub):
    h = relay_hub
    machine = h.machine(label="own-1")
    created = h.request(
        h.owner,
        "POST",
        CONNECTIONS,
        {"provider": "claude", "label": "my subscription", "machine": "own-1"},
    )[1]
    # A member lists only their own, and a foreign id answers like a missing one.
    assert h.request(h.member, "GET", CONNECTIONS)[1]["connections"] == []
    foreign = h.request(h.member, "DELETE", CONNECTIONS + "?id=" + created["id"])
    missing = h.request(h.member, "DELETE", CONNECTIONS + "?id=mc_0000000000000000")
    assert foreign[:2] == missing[:2] == (404, {"error": "not found"})
    # A member cannot register someone else's machine: it looks like no such machine.
    own = h.request(
        h.member, "POST", CONNECTIONS, {"provider": "claude", "label": "mine", "machine": "own-1"}
    )
    nope = h.request(
        h.member, "POST", CONNECTIONS, {"provider": "claude", "label": "mine", "machine": "nope"}
    )
    assert own[:2] == nope[:2] == (404, {"error": "not found"})
    # Agents and machines have no connections at all.
    assert h.request(h.a1, "GET", CONNECTIONS)[0] in (401, 403, 404)
    assert h.request(machine["machine_token"], "GET", CONNECTIONS)[0] in (401, 403, 404)
    # The owner's reads of the workspace show no label either.
    for path in (SETTING, "/api/me/model-mode", "/api/projects/work", "/api/runners"):
        shown = h.request(h.owner, "GET", path)[2].decode()
        assert "my subscription" not in shown, path


@pytest.mark.parametrize(
    "label",
    [
        "",
        " leading",
        "x" * 61,
        "sk-ant-api03-" + "a" * 40,
        "ghp_" + "a" * 36,
        "tok=abcdefgh1234",
        "a@b.test",
    ],
)
def test_a_label_that_could_hold_a_credential_is_refused(relay_hub, label):
    h = relay_hub
    h.machine(label="own-1")
    status, _, _ = h.request(
        h.owner, "POST", CONNECTIONS, {"provider": "claude", "label": label, "machine": "own-1"}
    )
    assert status == 400
    assert h.request(h.owner, "GET", CONNECTIONS)[1]["connections"] == []


def test_a_person_holds_at_most_five_connections(relay_hub):
    h = relay_hub
    h.machine(label="own-1")
    with h.db._lock, h.db._tx() as conn:
        for n in range(5):
            conn.execute(
                "INSERT INTO model_connections VALUES (?,?,?,?,?,?,?,NULL)",
                (f"mc_{n:016x}", h.workspace, h.admin, "claude", f"l{n}", f"mach_{n:016x}", "2026"),
            )
    status, body, _ = h.request(
        h.owner, "POST", CONNECTIONS, {"provider": "claude", "label": "sixth", "machine": "own-1"}
    )
    assert (status, body) == (400, {"error": "too many connections"})


def test_the_use_own_switch_is_a_boolean_per_person(relay_hub):
    h = relay_hub
    assert (
        h.request(h.member, "PUT", "/api/me/model-use-own", {"use_own": True})[1]["use_own"] is True
    )
    assert h.request(h.owner, "GET", CONNECTIONS)[1]["use_own"] is False
    for body in ({"use_own": "yes"}, {}, {"use_own": True, "x": 1}):
        assert h.request(h.member, "PUT", "/api/me/model-use-own", body)[0] == 400


# ---------------------------------------------------------------- what a person is shown


def test_the_tool_and_route_show_the_callers_own_account_per_role(relay_hub):
    h = relay_hub
    h.machine(label="own-1")
    h.set_main_model("claude", "claude-haiku-5-5")
    h.request(h.owner, "PUT", SETTING, {"mode": "personal"})
    h.request(
        h.owner,
        "POST",
        CONNECTIONS,
        {"provider": "claude", "label": "my subscription", "machine": "own-1"},
    )
    mine = h.request(h.owner, "GET", "/api/me/model-mode?board=work")[1]["project"]["roles"]["main"]
    assert (mine["mode"], mine["kind"], mine["label"]) == ("personal", "person", "my subscription")
    theirs = h.request(h.member, "GET", "/api/me/model-mode?board=work")[1]["project"]["roles"][
        "main"
    ]
    assert theirs["kind"] is None and "connect" in theirs["reason"].lower()
    assert "my subscription" not in json.dumps(theirs)
    status, said = h.mcp(h.a1, "model_mode", board="work")
    assert status == 200 and said["ok"], said
    assert "my subscription" not in said["text"]
    assert h.request(h.owner, "GET", "/api/me/model-mode?board=nope")[0] == 404
    project = h.project(h.owner, "work")[1]["models_effective"]["main"]
    assert project["account"]["label"] == "my subscription"


# ---------------------------------------------------------------- chat: never silent, never the org account


def _chat_rows(h):
    conn = connect(str(h.hub.gateway.workspace_dir(h.workspace)) + "/runner.sqlite3")
    conn.row_factory = row_factory
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM run_ledger WHERE source='chat'")]
    finally:
        conn.close()


def _replies(h, who, code=None):
    answers = [r for r in h.chat(who)[1]["replies"] if r["kind"] == "answer"]
    return [r for r in answers if code is None or r["code"] == code]


def test_personal_mode_without_a_connection_tells_the_person_and_runs_nothing(relay_hub):
    h = relay_hub
    machine = h.machine()
    h.set_main_model("claude", "claude-haiku-5-5")
    assert h.poll(machine)[0] == 204
    h.request(h.owner, "PUT", SETTING, {"mode": "personal"})
    h.post(h.owner, "Question one")
    assert h.poll(machine)[0] == 204  # the runner is online, yet nothing was queued for it
    [reply] = _replies(h, h.owner, "connection_missing")
    assert "your own claude account" in reply["text"] and "Settings" in reply["text"]
    assert not any(j["state"] == "queued" for j in h.jobs())
    # One Needs me item for that person, and nobody else's list names it.
    needs = h.request(h.owner, "GET", "/api/needs-me")[1]
    [item] = needs["model_connections"]
    assert item["board"] == "work" and item["action"]["label"] == "Connect your account"
    assert "model_connections" not in h.request(h.member, "GET", "/api/needs-me")[1]
    again = (h.post(h.owner, "Question two"), h.request(h.owner, "GET", "/api/needs-me")[1])
    assert len(again[1]["model_connections"]) == 1


def test_personal_mode_runs_on_the_persons_own_machine_and_records_it(relay_hub):
    h = relay_hub
    machine = h.machine(label="own-1")
    h.set_main_model("claude", "claude-haiku-5-5")
    h.request(h.owner, "PUT", SETTING, {"mode": "personal"})
    h.request(
        h.owner,
        "POST",
        CONNECTIONS,
        {"provider": "claude", "label": "my subscription", "machine": "own-1"},
    )
    assert h.poll(machine)[0] == 204
    h.post(h.owner, "Question")
    status, lease = h.poll(machine)
    assert status == 200, lease
    [row] = _chat_rows(h)
    assert (row["mode"], row["mode_source"], row["account_kind"]) == (
        "personal",
        "workspace",
        "person",
    )
    assert (row["account_label"], row["account_person"]) == ("my subscription", h.admin)
    # The connection ends the Needs me item by itself.
    assert "model_connections" not in h.request(h.owner, "GET", "/api/needs-me")[1]


def test_org_mode_keeps_a_persons_own_machine_out_of_chat(relay_hub):
    h = relay_hub
    machine = h.machine(label="own-1")
    h.set_main_model("claude", "claude-haiku-5-5")
    h.request(
        h.owner,
        "POST",
        CONNECTIONS,
        {"provider": "claude", "label": "my subscription", "machine": "own-1"},
    )
    h.request(h.owner, "PUT", SETTING, {"mode": "org"})
    assert h.poll(machine)[0] == 204
    h.post(h.owner, "Question")
    assert h.poll(machine)[0] == 204  # the only machine is a personal one: no org runner
    assert not any(j["state"] == "queued" for j in h.jobs())


def test_a_mode_change_after_queueing_never_moves_the_message_to_another_account(relay_hub):
    h = relay_hub
    machine = h.machine(label="own-1")
    h.set_main_model("claude", "claude-haiku-5-5")
    assert h.poll(machine)[0] == 204
    h.post(h.owner, "Question")
    assert any(j["state"] == "queued" for j in h.jobs())
    # The owner switches to personal and this person has no connection: the lease refuses.
    h.request(h.owner, "PUT", SETTING, {"mode": "personal"})
    assert h.poll(machine)[0] == 204
    [job] = h.jobs()
    assert (job["state"], job["code"]) == ("skipped", "connection_missing")
    assert _replies(h, h.owner, "connection_missing")


def test_unset_chat_is_recorded_as_the_legacy_setup(relay_hub):
    h = relay_hub
    machine = h.machine()
    h.set_main_model("claude", "claude-haiku-5-5")
    assert h.poll(machine)[0] == 204
    h.post(h.owner, "Question")
    assert h.poll(machine)[0] == 200
    [row] = _chat_rows(h)
    assert (row["mode"], row["mode_source"], row["account_kind"], row["account_person"]) == (
        "legacy",
        "legacy",
        None,
        None,
    )


@contextmanager
def _unreadable_mode_store(h, failure, monkeypatch):
    if failure != "locked_settings":
        table = {
            "dropped_settings": "model_mode_settings",
            "dropped_needs": "model_connection_needs",
        }[failure]
        with h.db._lock, h.db._tx() as conn:
            conn.execute(f"DROP TABLE {table}")
        yield
        return
    original_decide = model_modes.decide
    causes = []

    def locked_decide(*args, **kwargs):
        # Hold a real table lock only during resolution; later URL writes need the store too.
        with _locked_mode_settings(h):
            try:
                return original_decide(*args, **kwargs)
            except model_modes.ModeUnavailable as exc:
                causes.append(exc.__cause__)
                raise

    with monkeypatch.context() as patch:
        patch.setattr(model_modes, "decide", locked_decide)
        yield
    assert len(causes) == 1 and isinstance(causes[0], sqlite3.OperationalError)
    assert "locked" in str(causes[0])


@contextmanager
def _locked_mode_settings(h):
    # Shared-cache connections let a real table lock affect modes without locking auth reads.
    uri = Path(h.db.path).as_uri() + "?cache=shared"
    with (
        closing(connect(uri, uri=True, check_same_thread=False, isolation_level=None)) as reader,
        closing(connect(uri, uri=True, isolation_level=None)) as writer,
    ):
        reader.row_factory = row_factory
        reader.execute("PRAGMA foreign_keys = ON")
        with h.db._lock:
            original, h.db._conn = h.db._conn, reader
        try:
            writer.execute("BEGIN IMMEDIATE")
            writer.execute("UPDATE model_mode_settings SET mode=mode")
            yield
        finally:
            with h.db._lock:
                writer.rollback()
                h.db._conn = original


@pytest.mark.parametrize("failure", ["dropped_settings", "dropped_needs", "locked_settings"])
def test_needs_me_still_answers_when_the_mode_store_cannot_be_read(relay_hub, failure, monkeypatch):
    h = relay_hub
    h.machine()
    assert h.propose_titled("Review this synthetic change")[0] == 201
    assert h.request(h.owner, "PUT", SETTING, {"mode": "personal"})[0] == 200
    model_modes.note_missing(h.db, h.workspace, h.admin, "work", "claude")
    status, before, _ = h.request(h.owner, "GET", "/api/needs-me")
    assert status == 200 and len(before["model_connections"]) == 1
    assert before["approvals"]
    expected = {key: value for key, value in before.items() if key != "model_connections"}
    expected["count"] -= 1
    with _unreadable_mode_store(h, failure, monkeypatch):
        status, body, _ = h.request(h.owner, "GET", "/api/needs-me")
        assert status == 200 and body == expected
    if failure == "locked_settings":
        # A temporary outage must not lose the person's pending connection item.
        assert h.request(h.owner, "GET", "/api/needs-me")[:2] == (200, before)


def test_an_unreadable_mode_store_refuses_chat_in_words(relay_hub):
    h = relay_hub
    machine = h.machine()
    h.set_main_model("claude", "claude-haiku-5-5")
    assert h.poll(machine)[0] == 204
    with h.db._lock, h.db._tx() as conn:
        conn.execute("DROP TABLE model_mode_settings")
    h.post(h.owner, "Question")
    assert h.poll(machine)[0] == 204
    assert _replies(h, h.owner, "mode_unavailable")


def test_the_visible_label_is_only_ever_the_persons_own():
    own = {
        "mode": "personal",
        "mode_source": "workspace",
        "account_kind": "person",
        "account_label": "my subscription",
        "account_person": "u1",
    }
    assert model_modes.run_view(own, "u1")["label"] == "my subscription"
    for viewer in ("u2", None):
        assert model_modes.run_view(own, viewer) == {
            "mode": "personal",
            "mode_source": "workspace",
            "kind": "person",
            "label": None,
        }
    org = {**own, "account_kind": "org", "account_label": "relay-1", "account_person": None}
    assert model_modes.run_view(org, "u2")["label"] == "relay-1"
    assert model_modes.run_view({**own, "mode": None}, "u1") is None

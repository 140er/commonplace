"""Memory organisation routes: read facts, sources and links; end or replace a fact; add or remove a link.

Every change is checked against the stored state: nothing is erased, no stored text changes,
a cancelled confirm changes nothing, and a caller without access changes and learns nothing.
"""

import json

from tests._chat_helpers import chat_environment, chat_hub_fixture  # noqa: F401

CANARY = "Exact words, kept as typed.\nSecond line with  two  spaces and ünïcode."


def seed(h):
    token = h.owner_token()
    for room in ("alpha", "beta"):
        result, _ = h.call(
            token,
            "mempalace_add_drawer",
            wing="shared",
            room=room,
            content=CANARY if room == "alpha" else "Beta room words.",
        )
        assert result and result.get("success"), result
    result, _ = h.call(
        token,
        "mempalace_kg_add",
        subject="Avery",
        predicate="works_at",
        object="Acme",
        valid_from="2024-01-01",
        wing="shared",
    )
    assert result and result.get("success"), result
    return token


def drawers(h, token):
    result, _ = h.call(token, "mempalace_list_drawers", wing="shared", limit=100)
    rows = result["drawers"]
    out = {}
    for row in rows:
        got, _ = h.call(token, "mempalace_get_drawer", drawer_id=row["drawer_id"])
        out[row["drawer_id"]] = (got["content"], got["room"], got["wing"])
    return out


def timeline(h, token):
    result, _ = h.call(token, "mempalace_kg_timeline", entity="Avery")
    return result["timeline"]


def readonly(h):
    return h.token("reader", profile="full", scopes=["memory:read"], read_wings=["shared"])


def post(h, auth, path, body):
    return h.request(auth, "POST", path, body)


FACT = {"subject": "Avery", "predicate": "works_at", "object": "Acme", "wing": "shared"}


def test_facts_list_shows_history_and_empty_states(chat_hub):
    h = chat_hub
    seed(h)
    status, body, _ = h.get(h.owner, "/api/organise/facts?entity=Avery")
    assert status == 200, body
    assert [(f["subject"], f["predicate"], f["object"]) for f in body["facts"]] == [
        ("Avery", "works_at", "Acme")
    ]
    fact = body["facts"][0]
    assert (
        fact["valid_from"] == "2024-01-01" and fact["valid_to"] is None and fact["wing"] == "shared"
    )
    status, body, _ = h.get(h.owner, "/api/organise/facts?entity=Nobody")
    assert status == 200 and body["facts"] == [] and body["entity"] == "Nobody"


def test_invalidate_previews_then_keeps_history_and_all_text(chat_hub):
    h = chat_hub
    token = seed(h)
    before_drawers = drawers(h, token)
    before = timeline(h, token)
    status, preview, _ = post(h, h.owner, "/api/organise/facts/invalidate", FACT)
    assert status == 200 and preview["effect_id"] and preview["preview"]["lines"]
    assert any("stay stored" in line for line in preview["preview"]["lines"])
    assert timeline(h, token) == before  # a preview changes nothing
    # A cancelled confirm is no request at all; a confirm with the wrong effect changes nothing.
    bad = {**FACT, "confirm": True, "effect_id": "0" * 64, "ended": preview["preview"]["ended"]}
    status, body, _ = post(h, h.owner, "/api/organise/facts/invalidate", bad)
    assert status == 409 and "Nothing was changed" in body["error"]
    assert timeline(h, token) == before
    ok = {
        **FACT,
        "confirm": True,
        "effect_id": preview["effect_id"],
        "ended": preview["preview"]["ended"],
    }
    status, body, _ = post(h, h.owner, "/api/organise/facts/invalidate", ok)
    assert status == 200 and body["done"], body
    after = timeline(h, token)
    assert len(after) == len(before) == 1  # nothing erased
    assert (
        after[0]["valid_to"] == preview["preview"]["ended"]
        and after[0]["valid_from"] == "2024-01-01"
    )
    assert drawers(h, token) == before_drawers  # no stored text altered
    status, body, _ = post(h, h.owner, "/api/organise/facts/invalidate", FACT)
    assert status == 404 and "Nothing was changed" in body["error"]  # already ended


def test_supersede_keeps_the_old_fact_in_history(chat_hub):
    h = chat_hub
    token = seed(h)
    before_drawers = drawers(h, token)
    body = {**FACT, "new_object": "Initech"}
    status, preview, _ = post(h, h.owner, "/api/organise/facts/supersede", body)
    assert status == 200, preview
    assert len(timeline(h, token)) == 1
    confirm = {
        **body,
        "confirm": True,
        "effect_id": preview["effect_id"],
        "at": preview["preview"]["at"],
    }
    status, done, _ = post(h, h.owner, "/api/organise/facts/supersede", confirm)
    assert status == 200 and done["done"], done
    rows = {r["object"]: r for r in timeline(h, token)}
    assert set(rows) == {"Acme", "Initech"}
    assert rows["Acme"]["valid_to"] and rows["Initech"]["valid_to"] is None
    assert rows["Initech"]["valid_from"] == rows["Acme"]["valid_to"]
    assert drawers(h, token) == before_drawers
    status, same, _ = post(
        h,
        h.owner,
        "/api/organise/facts/supersede",
        {**FACT, "object": "Initech", "new_object": "Initech"},
    )
    assert status == 400


def test_fact_changes_refuse_without_access_and_change_nothing(chat_hub):
    h = chat_hub
    token = seed(h)
    before = timeline(h, token)
    reader = readonly(h)
    for path, body in (
        ("/api/organise/facts/invalidate", {**FACT, "confirm": True, "effect_id": "x"}),
        (
            "/api/organise/facts/supersede",
            {**FACT, "new_object": "Z", "confirm": True, "effect_id": "x"},
        ),
        ("/api/organise/facts/invalidate", FACT),
    ):
        status, response, _ = post(h, reader, path, body)
        assert status == 403, (path, response)
        assert "Acme" not in json.dumps(response)
    assert h.get(reader, "/api/organise/facts?entity=Avery")[0] == 403
    assert h.get(None, "/api/organise/facts")[0] == 401
    assert post(h, None, "/api/organise/facts/invalidate", FACT)[0] == 401
    assert timeline(h, token) == before


def test_sources_show_stored_words_exactly(chat_hub):
    h = chat_hub
    token = seed(h)
    status, body, _ = h.get(h.owner, "/api/organise/places")
    assert status == 200
    rooms = {r["room"] for w in body["wings"] if w["wing"] == "shared" for r in w["rooms"]}
    assert {"alpha", "beta"} <= rooms
    status, body, _ = h.get(h.owner, "/api/organise/sources?wing=shared&room=alpha")
    assert status == 200 and len(body["drawers"]) == 1
    identifier = body["drawers"][0]["id"]
    status, one, _ = h.get(h.owner, f"/api/organise/source?id={identifier}")
    assert status == 200 and one["text"] == CANARY
    assert h.get(h.owner, "/api/organise/source?id=missing")[0] == 404
    assert h.get(readonly(h), f"/api/organise/source?id={identifier}")[0] in (200, 403)
    assert drawers(h, token)[identifier][0] == CANARY


def test_sources_empty_room_is_an_empty_list_not_an_error(chat_hub):
    h = chat_hub
    seed(h)
    status, body, _ = h.get(h.owner, "/api/organise/sources?wing=shared&room=nothing-here")
    assert status == 200 and body["drawers"] == [] and body["room"] == "nothing-here"


def test_links_add_and_remove_leave_every_text_alone(chat_hub):
    h = chat_hub
    token = seed(h)
    before = drawers(h, token)
    link = {
        "source_wing": "shared",
        "source_room": "alpha",
        "target_wing": "shared",
        "target_room": "beta",
        "label": "same topic",
    }
    status, listed, _ = h.get(h.owner, "/api/organise/links")
    assert status == 200 and listed["tunnels"] == []
    status, preview, _ = post(h, h.owner, "/api/organise/links/add", link)
    assert status == 200 and preview["effect_id"], preview
    assert h.get(h.owner, "/api/organise/links")[1]["tunnels"] == []  # preview is not a change
    assert (
        post(h, h.owner, "/api/organise/links/add", {**link, "confirm": True, "effect_id": "bad"})[
            0
        ]
        == 409
    )
    assert h.get(h.owner, "/api/organise/links")[1]["tunnels"] == []
    status, done, _ = post(
        h,
        h.owner,
        "/api/organise/links/add",
        {**link, "confirm": True, "effect_id": preview["effect_id"]},
    )
    assert status == 201 and done["done"], done
    tunnels = h.get(h.owner, "/api/organise/links")[1]["tunnels"]
    assert len(tunnels) == 1 and tunnels[0]["label"] == "same topic"
    # Linking the same two places again would overwrite the label: refused.
    status, again, _ = post(h, h.owner, "/api/organise/links/add", {**link, "label": "other"})
    assert status == 409 and "Nothing was changed" in again["error"]
    assert h.get(h.owner, "/api/organise/links")[1]["tunnels"][0]["label"] == "same topic"
    status, missing, _ = post(
        h, h.owner, "/api/organise/links/add", {**link, "target_room": "ghost"}
    )
    assert status == 404
    identifier = tunnels[0]["id"]
    status, preview, _ = post(h, h.owner, "/api/organise/links/remove", {"id": identifier})
    assert status == 200 and any(
        "stay exactly as they are" in line for line in preview["preview"]["lines"]
    )
    assert len(h.get(h.owner, "/api/organise/links")[1]["tunnels"]) == 1  # cancelled: still there
    status, done, _ = post(
        h,
        h.owner,
        "/api/organise/links/remove",
        {"id": identifier, "confirm": True, "effect_id": preview["effect_id"]},
    )
    assert status == 200 and done["done"], done
    assert h.get(h.owner, "/api/organise/links")[1]["tunnels"] == []
    assert drawers(h, token) == before
    assert post(h, h.owner, "/api/organise/links/remove", {"id": identifier})[0] == 404


def test_link_changes_refuse_without_access(chat_hub):
    h = chat_hub
    seed(h)
    reader = readonly(h)
    link = {
        "source_wing": "shared",
        "source_room": "alpha",
        "target_wing": "shared",
        "target_room": "beta",
    }
    for path, body in (
        ("/api/organise/links/add", {**link, "confirm": True, "effect_id": "x"}),
        ("/api/organise/links/remove", {"id": "x", "confirm": True, "effect_id": "x"}),
    ):
        status, response, _ = post(h, reader, path, body)
        assert status in (403, 404), (path, response)
    assert h.get(h.owner, "/api/organise/links")[1]["tunnels"] == []
    assert h.get(None, "/api/organise/links")[0] == 401


def test_no_route_deletes_stored_text():
    from commonplace import memory_organise

    paths = set(memory_organise.READS) | set(memory_organise.WRITES)
    assert not [p for p in paths if "delete" in p or "bulk" in p or "by-source" in p]
    source = open(memory_organise.__file__).read()
    for forbidden in ("delete_drawer", "delete_by_source", "update_drawer", "delete_hallway"):
        assert forbidden not in source

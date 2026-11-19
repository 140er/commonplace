"""Cloud workspace records on the owner Compute REST: validation, status, access and edits."""

import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from commonplace import sessions
from commonplace.compute import workspaces
from tests.test_compute_api import API
from tests.test_compute_control import (  # noqa: F401 -- shared real-store fixture
    compute_env,
    gateway_payload,
    node_payload,
)

BASE = "/workspaces"
# One list of accepted and refused input, read by this suite and by the browser-rule suite.
INPUTS = json.loads(
    Path(__file__).with_name("fixtures").joinpath("compute_workspace_inputs.json").read_text()
)


@pytest.fixture
def api(compute_env):  # noqa: F811
    return API(compute_env)


def body(**changes):
    return {
        "name": "Build box",
        "image_source": "custom",
        "image": "ghcr.io/acme/tools:1.2",
        **changes,
    }


def create(api, **changes):
    status, result = api.request("POST", BASE, body(**changes))
    assert status == 201, result
    return result["workspace"]


def set_state(api, workspace_id, state, reason=None, **kwargs):
    with api.env.store._journal() as conn:
        api.env.store._set_workspace_state(conn, workspace_id, state, reason, **kwargs)


def member_api(api):
    cookie = sessions.start_session(api.env.db, api.env.member, api.env.workspace)
    return lambda *args, **kw: api.request(*args, cookie=cookie, **kw)


def test_options_serve_curated_images_ranges_and_defaults(api):
    status, result = api.request(path=BASE + "/images")
    assert status == 200
    assert [item["id"] for item in result["images"]][:1] == ["ubuntu-24-04"]
    for item in result["images"]:
        assert workspaces.parse_image(item["reference"])["reference"] == item["reference"]
    assert result["limits"]["cpu_cores"] == {"min": 0.5, "max": 32, "step": 0.5, "default": 2}
    assert result["limits"]["memory_mib"]["default"] == 4096
    assert result["limits"]["storage_gib"]["default"] == 20
    assert result["networks"] == ["outbound", "none"] and result["default_network"] == "outbound"


def test_create_with_defaults_is_pending_without_a_machine_and_not_started(api):
    assert api.request(path=BASE) == (200, {"workspaces": [], "total": 0, "can_manage": True})
    item = create(api)
    assert item["cpu_cores"] == 2 and item["memory_mib"] == 4096 and item["storage_gib"] == 20
    assert item["network"] == "outbound"
    assert item["status"] == "no_machine" and item["status_reason"] is None
    assert item["started"] is False and item["restart_needed"] is False
    assert item["node_id"] is None and item["machine_name"] is None
    assert item["image"] == "ghcr.io/acme/tools:1.2" and item["image_pinned"] is False
    assert item["image_registry"] == "ghcr.io" and item["image_tag"] == "1.2"
    assert api.request(path=BASE) == (200, {"workspaces": [item], "total": 1, "can_manage": True})
    assert api.request(path=f"{BASE}/{item['id']}") == (
        200,
        {"workspace": item, "can_manage": True},
    )


def test_acceptance_custom_image_two_cpu_four_gib_on_a_machine(api):
    _, node = api.enrolled()
    item = create(
        api, cpu_cores=2, memory_mib=4096, storage_gib=20, node_id=node["id"], name="Acceptance"
    )
    assert (item["cpu_cores"], item["memory_mib"], item["storage_gib"]) == (2, 4096, 20)
    assert item["status"] == "pending" and item["machine_name"] == node["name"]
    assert item["started"] is False


def test_curated_image_is_known_and_labelled(api):
    curated = workspaces.CURATED_IMAGES[0]
    item = create(api, image_source="curated", image=curated["reference"])
    assert item["curated_label"] == curated["label"]
    status, result = api.request(
        "POST", BASE, body(name="Other", image_source="curated", image="docker.io/library/nope:1")
    )
    assert (status, result["field"], result["reason"]) == (400, "image", "not_curated")


DIGEST = "sha256:" + "a" * 64


@pytest.mark.parametrize("reference", INPUTS["images"]["accepted"])
def test_valid_image_references_are_accepted_and_pinning_is_reported(api, reference):
    item = create(api, image=reference)
    assert item["image"] == reference
    assert item["image_pinned"] is ("@" in reference)
    assert item["image_digest"] == (DIGEST if "@" in reference else None)


@pytest.mark.parametrize("reference,reason", sorted(INPUTS["images"]["refused"].items()))
def test_malformed_and_injected_image_references_are_refused_and_never_stored(
    api, reference, reason
):
    status, result = api.request("POST", BASE, body(image=reference))
    assert status == 400
    assert result == {"error": "invalid_request", "field": "image", "reason": reason}
    assert api.request(path=BASE) == (200, {"workspaces": [], "total": 0, "can_manage": True})


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("cpu_cores", 0, "range"),
        ("cpu_cores", 0.25, "range"),
        ("cpu_cores", 32.5, "range"),
        ("cpu_cores", 1.3, "step"),
        ("cpu_cores", "2", "not_a_number"),
        ("cpu_cores", True, "not_a_number"),
        ("cpu_cores", None, "not_a_number"),
        ("memory_mib", 511, "range"),
        ("memory_mib", 131073, "range"),
        ("memory_mib", 1000, "step"),
        ("memory_mib", 4096.0, "not_a_number"),
        ("memory_mib", "4096", "not_a_number"),
        ("memory_mib", True, "not_a_number"),
        ("storage_gib", 0, "range"),
        ("storage_gib", 2049, "range"),
        ("storage_gib", -5, "range"),
        ("storage_gib", 1.5, "not_a_number"),
        ("network", "host", "choice"),
        ("private_registry", "yes", "choice"),
    ],
)
def test_limits_out_of_range_are_refused_with_the_field_and_reason(api, field, value, reason):
    status, result = api.request("POST", BASE, body(**{field: value}))
    assert status == 400
    assert result == {"error": "invalid_request", "field": field, "reason": reason}
    assert api.request(path=BASE)[1]["workspaces"] == []


def test_range_edges_are_accepted(api):
    low = create(api, name="Low", cpu_cores=0.5, memory_mib=512, storage_gib=1)
    high = create(api, name="High", cpu_cores=32, memory_mib=131072, storage_gib=2048)
    assert (low["cpu_cores"], low["memory_mib"], low["storage_gib"]) == (0.5, 512, 1)
    assert (high["cpu_cores"], high["memory_mib"], high["storage_gib"]) == (32, 131072, 2048)


@pytest.mark.parametrize(
    "payload,reason,field",
    [
        ({}, "required", "image"),
        ({"name": "x"}, "required", "image"),
        ({"name": "x", "image_source": "custom"}, "required", "image"),
        ({**body(), "secret": "x"}, "unknown_field", None),
        ({**body(), "registry_password": "hunter2"}, "unknown_field", None),
        ({**body(), "registry_credential_id": "x"}, "unknown_field", None),
        ({**body(), "state": "ready"}, "unknown_field", None),
        ({**body(), "applied_revision": 1}, "unknown_field", None),
        ({**body(), "id": "chosen"}, "unknown_field", None),
        ({**body(), "session_hash": "x"}, "unknown_field", None),
        ({**body(), "request_id": "bad id!"}, "invalid", "request_id"),
    ],
)
def test_unknown_missing_and_server_owned_fields_are_refused(api, payload, reason, field):
    status, result = api.request("POST", BASE, payload)
    expected = {"error": "invalid_request", "reason": reason}
    assert (status, result) == (400, expected | ({"field": field} if field else {}))
    assert api.request(path=BASE)[1]["workspaces"] == []


@pytest.mark.parametrize(
    "name,field_reason",
    [
        ("", "length"),
        ("  ", "length"),
        ("x" * 61, "length"),
        (" padded", "characters"),
        ("tab\there", "characters"),
        ("new\nline", "characters"),
        (7, "length"),
        # Invisible, blank-looking and direction-changing names.
        ("\u200b", "characters"),
        ("\u200b\u200b\u200b", "characters"),
        ("a\u200bb", "characters"),
        ("\u2800\u2800", "characters"),
        ("\u202eevil", "characters"),
        ("abc\u202c", "characters"),
        ("\u2066x\u2069", "characters"),
        ("x\u200ey", "characters"),
        ("\ufeffname", "characters"),
        ("a\u2028b", "characters"),
        ("\ud800broken", "characters"),
        ("---", "no_letter"),
        ("!!! ???", "no_letter"),
        ("\U000e0041tag", "characters"),
        # One length rule: code points after NFC, not UTF-16 units and not bytes.
        ("é" * 61, "length"),
    ],
)
def test_names_are_validated(api, name, field_reason):
    status, result = api.request("POST", BASE, body(name=name))
    assert (status, result) == (
        400,
        {"error": "invalid_request", "field": "name", "reason": field_reason},
    )


@pytest.mark.parametrize("name", INPUTS["names"]["accepted"])
def test_the_shared_accepted_names_are_stored(api, name):
    assert create(api, name=name)["name"]


@pytest.mark.parametrize("name,reason", sorted(INPUTS["names"]["refused"].items()))
def test_the_shared_refused_names_are_refused_and_never_stored(api, name, reason):
    status, result = api.request("POST", BASE, body(name=name))
    assert (status, result) == (
        400,
        {"error": "invalid_request", "field": "name", "reason": reason},
    )
    assert api.request(path=BASE)[1]["total"] == 0


def test_names_are_unique_ignoring_case(api):
    create(api, name="Build box")
    status, result = api.request("POST", BASE, body(name="BUILD BOX"))
    assert (status, result) == (
        409,
        {"error": "conflict", "field": "name", "reason": "name_taken"},
    )


@pytest.mark.parametrize(
    "first,second",
    [
        ("Café", "Cafe\u0301"),  # precomposed and combining: one name after NFC
        ("Straße", "STRASSE"),  # case folding beyond ASCII
        ("ÉCOLE", "école"),
        ("Σίσυφος", "ΣΊΣΥΦΟΣ"),
    ],
)
def test_names_are_unique_by_nfc_and_casefold_not_just_ascii(api, first, second):
    created = create(api, name=first)
    status, result = api.request("POST", BASE, body(name=second))
    assert (status, result["reason"]) == (409, "name_taken")
    other = create(api, name="Other")
    status, result = api.request(
        "POST", f"{BASE}/{other['id']}", {"expected_revision": 1, "name": second}
    )
    assert (status, result["reason"]) == (409, "name_taken")
    # Renaming a record to a different casing of its own name is not a clash.
    status, result = api.request(
        "POST", f"{BASE}/{created['id']}", {"expected_revision": 1, "name": second}
    )
    assert status == 200 and result["workspace"]["name"]


def test_a_name_is_stored_normalised_and_counted_in_code_points(api):
    item = create(api, name="Cafe\u0301")
    assert item["name"] == "Caf\u00e9"
    assert len(create(api, name="é" * 60, image="ghcr.io/acme/b:1")["name"]) == 60
    # 31 code points are 61 UTF-16 units: the browser counts code points too, so this fits.
    assert create(api, name="a" + "\U0001f600" * 30, image="ghcr.io/acme/e:1")["name"]
    assert create(api, name="Ünï", image="ghcr.io/acme/c:1")["name"] == "Ünï"
    # Names in scripts other than Latin are fine as long as they have a letter or digit.
    assert create(api, name="東京 7", image="ghcr.io/acme/d:1")["name"] == "東京 7"


def test_unknown_machine_is_not_found_and_nothing_is_stored(api):
    status, result = api.request("POST", BASE, body(node_id="0" * 32))
    assert (status, result["error"], result["field"]) == (404, "not_found", "node_id")
    assert api.request(path=BASE)[1]["workspaces"] == []
    assert api.request("POST", BASE, body(node_id="not valid!"))[0] == 400


def test_status_codes_follow_the_record(api):
    _, node = api.enrolled()
    item = create(api, node_id=node["id"])
    assert item["status"] == "pending" and item["credential_bound"] is False
    set_state(api, item["id"], "ready", applied_revision=1)
    ready = api.request(path=f"{BASE}/{item['id']}")[1]["workspace"]
    assert ready["status"] == "ready" and ready["started"] is True
    set_state(api, item["id"], "failed", "out_of_memory")
    failed = api.request(path=f"{BASE}/{item['id']}")[1]["workspace"]
    assert (failed["status"], failed["status_reason"]) == ("failed", "out_of_memory")
    assert create(api, name="No machine")["status"] == "no_machine"


def test_lifecycle_seam_refuses_unknown_states_and_reasons(api):
    item = create(api)
    for args in (("running", None), ("failed", None), ("failed", "because"), ("ready", "x")):
        with pytest.raises(ValueError):
            set_state(api, item["id"], *args)


def test_a_private_registry_cannot_be_claimed_until_logins_can_be_stored(api):
    status, result = api.request("POST", BASE, body(private_registry=True))
    assert (status, result) == (
        400,
        {"error": "invalid_request", "field": "private_registry", "reason": "unavailable"},
    )
    assert api.request(path=BASE)[1]["workspaces"] == []
    plain = create(api)
    url = f"{BASE}/{plain['id']}"
    status, result = api.request("POST", url, {"expected_revision": 1, "private_registry": True})
    assert (status, result["field"], result["reason"]) == (400, "private_registry", "unavailable")
    assert api.request(path=url)[1]["workspace"]["private_registry"] is False
    assert api.request(path=BASE + "/images")[1]["registry_logins"] is False


def test_binding_a_registry_login_is_refused_and_writes_nothing(api):
    # A gateway or target credential reference must never be filed as a registry login.
    plain = create(api, name="Plain")
    with api.env.store._journal() as conn:
        conn.execute(
            "UPDATE cloud_workspaces SET private_registry=1 WHERE id=?", (plain["id"],)
        )  # a record stored before registry logins were hidden
    url = f"{BASE}/{plain['id']}/credential"
    for payload in (
        {"credential_id": "registry-login", "credential_version": 1},
        {"credential_id": "gateway-credential", "credential_version": 2},
        {},
        {"credential_id": "x", "credential_version": 1, "password": "hunter2"},
    ):
        status, result = api.request("POST", url, payload)
        assert (status, result) == (
            409,
            {"error": "conflict", "reason": "registry_logins_unavailable"},
        )
    assert api.request("POST", url, raw=b"not json")[0] == 409
    shown = api.request(path=f"{BASE}/{plain['id']}")[1]["workspace"]
    assert shown["credential_bound"] is False and shown["status"] == "no_machine"
    assert "registry_credential_id" not in shown and "registry_credential_version" not in shown
    with api.env.store._journal() as conn:
        row = conn.execute(
            "SELECT registry_credential_id,registry_credential_version FROM cloud_workspaces"
        ).fetchone()
    assert (row[0], row[1]) == (None, None)
    assert api.request("POST", f"{BASE}/{'0' * 32}/credential", {})[0] == 404


def test_a_record_that_already_says_private_can_be_edited_and_can_be_made_public(api):
    _, node = api.enrolled()
    item = create(api, node_id=node["id"])
    with api.env.store._journal() as conn:
        conn.execute("UPDATE cloud_workspaces SET private_registry=1 WHERE id=?", (item["id"],))
    url = f"{BASE}/{item['id']}"
    shown = api.request(path=url)[1]["workspace"]
    assert shown["status"] == "needs_credential" and shown["private_registry"] is True
    kept = api.request("POST", url, {"expected_revision": 1, "cpu_cores": 4})[1]["workspace"]
    assert kept["private_registry"] is True and kept["cpu_cores"] == 4
    same = api.request("POST", url, {"expected_revision": 2, "private_registry": True})
    assert same[0] == 200
    public = api.request("POST", url, {"expected_revision": 2, "private_registry": False})[1]
    assert public["workspace"]["private_registry"] is False
    assert public["workspace"]["status"] == "pending"


def test_stored_references_from_before_the_stricter_rules_still_list(api):
    item = create(api)
    with api.env.store._journal() as conn:
        conn.execute(
            "UPDATE cloud_workspaces SET image_reference='localhost:5000/app:1' WHERE id=?",
            (item["id"],),
        )
    status, result = api.request(path=BASE)
    assert status == 200 and result["workspaces"][0]["image_registry"] == "localhost:5000"
    assert api.request(path=f"{BASE}/{item['id']}")[0] == 200


def test_edit_bumps_the_revision_only_for_what_needs_a_restart_and_the_version_for_every_edit(api):
    item = create(api)
    url = f"{BASE}/{item['id']}"
    assert (item["version"], item["revision"]) == (1, 1)
    renamed = api.request("POST", url, {"expected_revision": 1, "name": "Renamed"})[1]["workspace"]
    # A rename moves the version (stale writes are caught) but not the revision (no restart).
    assert renamed["name"] == "Renamed" and (renamed["version"], renamed["revision"]) == (2, 1)
    resized = api.request(
        "POST", url, {"expected_revision": 2, "cpu_cores": 4, "memory_mib": 8192}
    )[1]["workspace"]
    assert (resized["cpu_cores"], resized["memory_mib"]) == (4, 8192)
    assert (resized["version"], resized["revision"]) == (3, 2)
    assert resized["restart_needed"] is False  # never started: applies when it starts
    same = api.request("POST", url, {"expected_revision": 3, "cpu_cores": 4})[1]["workspace"]
    assert (same["version"], same["revision"]) == (3, 2) and same["updated_at"] == resized[
        "updated_at"
    ]


def test_a_stale_rename_is_refused_and_changes_nothing(api):
    item = create(api)
    url = f"{BASE}/{item['id']}"
    # Two people open version 1; the first renames, the second must not overwrite that.
    assert api.request("POST", url, {"expected_revision": 1, "name": "First"})[0] == 200
    status, result = api.request("POST", url, {"expected_revision": 1, "name": "Second"})
    assert (status, result["reason"]) == (409, "stale_revision")
    assert api.request(path=url)[1]["workspace"]["name"] == "First"
    # A stale resize after a rename is refused too, and so is a stale discard.
    assert api.request("POST", url, {"expected_revision": 1, "cpu_cores": 8})[0] == 409
    assert api.request("POST", url + "/discard", {"expected_revision": 1})[1]["reason"] == (
        "stale_revision"
    )
    assert api.request("POST", url + "/discard", {"expected_revision": 2})[0] == 200


def test_a_failed_start_counts_as_started_everywhere(api):
    _, first = api.enrolled()
    second_gateway = api.request("POST", "/gateways", gateway_payload(name="Second"))[1]["gateway"]
    second = api.request("POST", "/nodes", node_payload(second_gateway["id"], name="Second"))[1][
        "node"
    ]
    item = create(api, node_id=first["id"])
    # The start failed before anything was applied: it may have left a container or a volume behind.
    set_state(api, item["id"], "failed", "image_pull_failed")
    shown = api.request(path=f"{BASE}/{item['id']}")[1]["workspace"]
    assert shown["applied_revision"] is None
    assert (shown["started"], shown["discardable"], shown["restart_needed"]) == (True, False, False)
    assert shown["status"] == "failed"
    # One meaning: the machine is locked, the discard is refused, and the projection agrees.
    url = f"{BASE}/{item['id']}"
    locked = api.request("POST", url, {"expected_revision": 1, "node_id": second["id"]})
    assert locked == (409, {"error": "conflict", "field": "node_id", "reason": "machine_locked"})
    assert api.request("POST", url + "/discard", {"expected_revision": 1})[1]["reason"] == (
        "already_started"
    )
    # A record that never started is the opposite on all three counts.
    fresh = create(api, name="Fresh", image="ghcr.io/acme/b:1", node_id=first["id"])
    assert (fresh["started"], fresh["discardable"]) == (False, True)
    assert (
        api.request(
            "POST", f"{BASE}/{fresh['id']}", {"expected_revision": 1, "node_id": second["id"]}
        )[0]
        == 200
    )


def test_a_started_workspace_says_restart_needed_after_an_edit(api):
    _, node = api.enrolled()
    item = create(api, node_id=node["id"])
    set_state(api, item["id"], "ready", applied_revision=1)
    url = f"{BASE}/{item['id']}"
    edited = api.request("POST", url, {"expected_revision": 1, "memory_mib": 8192})[1]["workspace"]
    # Its own status: "ready" would claim the container matches settings it has not applied.
    assert edited["restart_needed"] is True and edited["status"] == "restart_needed"
    set_state(api, item["id"], "ready", applied_revision=2)
    current = api.request(path=url)[1]["workspace"]
    assert current["restart_needed"] is False and current["status"] == "ready"
    renamed = api.request("POST", url, {"expected_revision": 2, "name": "New name"})[1]
    assert renamed["workspace"]["restart_needed"] is False


def test_stale_and_malformed_edits_are_refused_and_change_nothing(api):
    item = create(api)
    url = f"{BASE}/{item['id']}"
    api.request("POST", url, {"expected_revision": 1, "storage_gib": 40})
    status, result = api.request("POST", url, {"expected_revision": 1, "storage_gib": 80})
    assert (status, result["error"], result["reason"]) == (409, "conflict", "stale_revision")
    for bad in (
        {},
        {"expected_revision": 2},
        {"storage_gib": 80},
        {"expected_revision": "2", "storage_gib": 80},
        {"expected_revision": 2, "id": "x"},
        {"expected_revision": 2, "state": "ready"},
        {"expected_revision": 2, "image": "bad image"},
        {"expected_revision": 2, "memory_mib": 100},
    ):
        assert api.request("POST", url, bad)[0] == 400
    assert api.request(path=url)[1]["workspace"]["storage_gib"] == 40


def test_a_started_workspace_cannot_be_moved_to_another_machine(api):
    _, first = api.enrolled()
    second_gateway = api.request("POST", "/gateways", gateway_payload(name="Second"))[1]["gateway"]
    second = api.request("POST", "/nodes", node_payload(second_gateway["id"], name="Second"))[1][
        "node"
    ]
    item = create(api, node_id=first["id"])
    url = f"{BASE}/{item['id']}"
    moved = api.request("POST", url, {"expected_revision": 1, "node_id": second["id"]})
    assert moved[0] == 200 and moved[1]["workspace"]["machine_name"] == second["name"]
    set_state(api, item["id"], "ready", applied_revision=2)
    locked = api.request("POST", url, {"expected_revision": 2, "node_id": first["id"]})
    assert locked == (409, {"error": "conflict", "field": "node_id", "reason": "machine_locked"})
    cleared = api.request("POST", url, {"expected_revision": 2, "node_id": None})
    assert cleared[0] == 409


def test_member_reads_but_never_writes_and_sees_no_machine_or_registry_reference(api):
    _, node = api.enrolled()
    item = create(api, node_id=node["id"])
    ask = member_api(api)
    status, result = ask(path=BASE)
    assert status == 200 and result["can_manage"] is False
    seen = result["workspaces"][0]
    assert seen["name"] == item["name"] and seen["image"] == item["image"]
    # No machine name, no machine id, no address, no registry reference: only "a machine is assigned".
    assert seen["machine_name"] is None and seen["node_id"] is None
    assert seen["machine_assigned"] is True and seen["discardable"] is False
    assert "registry_credential_id" not in seen and "registry_credential_version" not in seen
    assert "address" not in seen and "host_key" not in seen
    status, detail = ask(path=f"{BASE}/{item['id']}")
    assert status == 200 and detail["can_manage"] is False
    assert detail["workspace"]["machine_name"] is None and detail["workspace"]["node_id"] is None
    assert node["name"] not in str(result) + str(detail)
    assert ask(path=BASE + "/images")[0] == 200
    denied = (403, {"error": "owner_required", "reason": "member"})
    assert ask("POST", BASE, body(name="Mine")) == denied
    assert ask("POST", f"{BASE}/{item['id']}", {"expected_revision": 1, "name": "Mine"}) == denied
    assert ask("POST", f"{BASE}/{item['id']}/credential", {}) == denied
    assert ask("POST", f"{BASE}/{item['id']}/discard", {"expected_revision": 1}) == denied
    # The owner check comes before the body is read: a bad body is still a 403 for a member.
    for path in (BASE, f"{BASE}/{item['id']}", f"{BASE}/{item['id']}/discard"):
        assert ask("POST", path, raw=b"not json") == denied
        assert ask("POST", path, {"unknown": 1}) == denied
    # Everything else in Compute stays owner-only for a member.
    # (These are not cloud workspace routes, so they keep their fixed bare shape.)
    for path in ("", "/nodes", "/credentials", "/workspaces-x"):
        assert ask(path=path) == (403, {"error": "owner_required"})
    assert api.request(path=f"{BASE}/{item['id']}")[1]["workspace"]["name"] == item["name"]
    owner_view = api.request(path=f"{BASE}/{item['id']}")[1]["workspace"]
    assert owner_view["machine_name"] == node["name"] and owner_view["node_id"] == node["id"]


def pat_headers(api):
    env = api.env
    _, token = env.db.create_token(
        workspace_ref=env.workspace,
        user_ref=env.owner,
        name="owner-agent",
        client="tests",
        policy=sessions.person_policy(),
        actor=env.owner,
    )
    return {"Authorization": "Bearer " + token}


def test_a_personal_access_token_never_reaches_workspaces_even_with_the_owner_cookie(api):
    item = create(api)
    url = f"{BASE}/{item['id']}"
    before = api.request(path=BASE)[1]
    headers = pat_headers(api)
    calls = (
        ("GET", BASE, None),
        ("GET", url, None),
        ("GET", BASE + "/images", None),
        ("POST", BASE, body(name="By token")),
        ("POST", url, {"expected_revision": 1, "name": "By token"}),
        ("POST", url + "/credential", {}),
        ("POST", url + "/discard", {"expected_revision": 1}),
    )
    for method, path, payload in calls:
        assert api.request(method, path, payload, headers=headers) == (
            403,
            {"error": "owner_required", "reason": "member"},
        ), (method, path)
        # Without the cookie the token alone is just as unable to act.
        status, result = api.request(method, path, payload, headers=headers, cookie=False)
        assert status in (401, 403) and "workspace" not in result and "workspaces" not in result
    assert api.request(path=BASE)[1] == before


def test_signed_out_and_foreign_people_are_refused_consistently(api):
    item = create(api)
    assert api.request(path=BASE, cookie=False) == (
        401,
        {"error": "sign_in", "reason": "session"},
    )
    assert api.request("POST", BASE, body(name="Z"), cookie=False)[0] == 401
    foreign = sessions.start_session(api.env.db, api.env.foreign, api.env.other_workspace)
    # A person of another workspace reads their own (empty) list and cannot reach this one's ids.
    assert api.request(path=BASE, cookie=foreign) == (
        200,
        {"workspaces": [], "total": 0, "can_manage": True},
    )
    gone = (404, {"error": "not_found", "reason": "record"})
    assert api.request(path=f"{BASE}/{item['id']}", cookie=foreign) == gone
    assert (
        api.request(
            "POST", f"{BASE}/{item['id']}", {"expected_revision": 1, "name": "X"}, cookie=foreign
        )
        == gone
    )
    assert api.request("POST", f"{BASE}/{item['id']}", raw=b"[]", cookie=foreign) == gone
    assert api.request(path=f"{BASE}/{'0' * 32}") == gone
    # A malformed id is not "not found": it names the identifier as the problem.
    assert api.request(path=f"{BASE}/bad%20id") == (
        400,
        {"error": "invalid_request", "reason": "identifier"},
    )


def test_routes_are_exact(api):
    item = create(api)
    for method, path in (
        ("GET", BASE + "/"),
        ("GET", f"{BASE}/{item['id']}/extra"),
        ("POST", f"{BASE}/{item['id']}/nope"),
        ("GET", f"{BASE}/{item['id']}?x=1"),
    ):
        assert api.request(method, path)[0] in (400, 404)
    assert api.request("POST", BASE + "/images", {})[0] == 404


def test_create_and_edit_are_audited_and_the_list_is_bounded(api):
    item = create(api)
    api.request("POST", f"{BASE}/{item['id']}", {"expected_revision": 1, "cpu_cores": 3})
    with api.env.store._journal() as conn:
        actions = [
            row["action"]
            for row in conn.execute(
                "SELECT action FROM audit WHERE object_id=? ORDER BY id", (item["id"],)
            )
        ]
    assert actions == ["workspace.created", "workspace.updated"]


def test_the_creation_cap_equals_the_list_limit_so_nothing_is_created_unlisted(api):
    assert workspaces.MAX_WORKSPACES == workspaces.MAX_LIST == 100
    assert api.request(path=BASE + "/images")[1]["max_workspaces"] == 100
    with api.env.store._journal() as conn:
        for number in range(100):
            conn.execute(
                "INSERT INTO cloud_workspaces (id,name,name_key,image_source,image_reference,"
                "cpu_millicores,memory_mib,storage_gib,network,created_by,created_at,updated_at) "
                "VALUES (?,?,?,'custom','ghcr.io/acme/tools:1',2000,4096,20,'outbound','u',?,?)",
                (f"{number:032x}", f"Box {number}", f"box {number}", float(number), float(number)),
            )
    status, result = api.request(path=BASE)
    assert status == 200 and len(result["workspaces"]) == 100 and result["total"] == 100
    assert result["workspaces"][0]["name"] == "Box 99"  # newest first, and the oldest is listed
    assert result["workspaces"][-1]["name"] == "Box 0"
    status, result = api.request("POST", BASE, body(name="One too many"))
    assert (status, result) == (409, {"error": "conflict", "reason": "limit_reached"})
    # Discarding one frees a place.
    gone = api.request(path=BASE)[1]["workspaces"][0]
    assert api.request("POST", f"{BASE}/{gone['id']}/discard", {"expected_revision": 1})[0] == 200
    assert api.request("POST", BASE, body(name="Fits now"))[0] == 201
    assert len(api.request(path=BASE)[1]["workspaces"]) == 100


def test_create_is_idempotent_by_request_id(api):
    first = api.request("POST", BASE, body(request_id="req-one"))
    assert first[0] == 201
    replay = api.request("POST", BASE, body(request_id="req-one"))
    assert replay[0] == 200 and replay[1] == {"workspace": first[1]["workspace"]}
    assert len(api.request(path=BASE)[1]["workspaces"]) == 1
    # The same id with different values is a conflict, never a silent second record.
    status, result = api.request("POST", BASE, body(request_id="req-one", cpu_cores=4))
    assert (status, result) == (
        409,
        {"error": "conflict", "field": "request_id", "reason": "request_reused"},
    )
    # A different id with the same name is the ordinary name clash.
    status, result = api.request("POST", BASE, body(request_id="req-two"))
    assert (status, result["reason"]) == (409, "name_taken")
    assert api.request("POST", BASE, body(name="Other", request_id="req-two"))[0] == 201
    # Without an id every call is its own attempt.
    assert api.request("POST", BASE, body(name="Third"))[0] == 201
    assert api.request("POST", BASE, body(name="Third"))[0] == 409
    for bad in ("", "x" * 65, "a b", 7):
        status, result = api.request("POST", BASE, body(name="Bad id", request_id=bad))
        assert (status, result["field"], result["reason"]) == (400, "request_id", "invalid")


def test_discard_removes_only_a_workspace_that_never_started(api):
    _, node = api.enrolled()
    never = create(api, name="Never started", node_id=node["id"])
    started = create(api, name="Started", node_id=node["id"], image="ghcr.io/acme/b:1")
    set_state(api, started["id"], "ready", applied_revision=1)
    shown = {item["name"]: item for item in api.request(path=BASE)[1]["workspaces"]}
    assert (
        shown["Never started"]["discardable"] is True and shown["Started"]["discardable"] is False
    )
    url = f"{BASE}/{started['id']}/discard"
    assert api.request("POST", url, {"expected_revision": 1}) == (
        409,
        {"error": "conflict", "reason": "already_started"},
    )
    failed = create(api, name="Failed once", image="ghcr.io/acme/c:1")
    set_state(api, failed["id"], "failed", "start_failed")
    assert (
        api.request("POST", f"{BASE}/{failed['id']}/discard", {"expected_revision": 1})[1]["reason"]
        == "already_started"
    )
    url = f"{BASE}/{never['id']}/discard"
    assert api.request("POST", url, {})[1] == {
        "error": "invalid_request",
        "field": "expected_revision",
        "reason": "required",
    }
    api.request("POST", f"{BASE}/{never['id']}", {"expected_revision": 1, "cpu_cores": 3})
    status, result = api.request("POST", url, {"expected_revision": 1})
    assert (status, result["reason"]) == (409, "stale_revision")
    status, result = api.request("POST", url, {"expected_revision": 2})
    assert (status, result) == (200, {"discarded": {"id": never["id"], "name": "Never started"}})
    assert api.request(path=f"{BASE}/{never['id']}")[0] == 404
    assert api.request("POST", url, {"expected_revision": 2})[0] == 404
    assert [item["name"] for item in api.request(path=BASE)[1]["workspaces"]] == [
        "Failed once",
        "Started",
    ]
    with api.env.store._journal() as conn:
        actions = [
            row["action"]
            for row in conn.execute(
                "SELECT action FROM audit WHERE object_id=? ORDER BY id", (never["id"],)
            )
        ]
    assert actions == ["workspace.created", "workspace.updated", "workspace.discarded"]
    # The name is free again.
    assert api.request("POST", BASE, body(name="Never started"))[0] == 201


@pytest.mark.parametrize(
    "suffix,payload,status,expected",
    [
        ("", None, 400, {"reason": "body"}),
        ("", {"name": 1, "image_source": "custom", "image": "x"}, 400, {"field": "name"}),
        ("", {**body(), "extra": 1}, 400, {"reason": "unknown_field"}),
        ("{id}", {"expected_revision": 1}, 400, {"reason": "no_changes"}),
        ("{id}", {"expected_revision": "1", "name": "x"}, 400, {"field": "expected_revision"}),
        ("{id}", {"expected_revision": 1, "id": "x"}, 400, {"reason": "unknown_field"}),
        ("{id}", {"name": "x"}, 400, {"field": "expected_revision", "reason": "required"}),
        (
            "{id}",
            {"expected_revision": 1, "node_id": "0" * 32},
            404,
            {"field": "node_id", "reason": "unknown_machine"},
        ),
        ("{id}/discard", {"expected_revision": 0}, 400, {"field": "expected_revision"}),
        ("{id}/credential", {}, 409, {"reason": "registry_logins_unavailable"}),
    ],
)
def test_every_refusal_carries_a_reason_and_names_the_field_when_there_is_one(
    api, suffix, payload, status, expected
):
    item = create(api)
    url = BASE + (f"/{suffix.format(id=item['id'])}" if suffix else "")
    got_status, result = api.request("POST", url, payload, raw=b"{" if payload is None else None)
    assert got_status == status, result
    assert result["reason"] and set(expected.items()) <= set(result.items()), result


def test_the_stored_name_key_is_backfilled_for_records_from_before_it_existed(api):
    first, second = create(api, name="Straße"), create(api, name="Zed", image="ghcr.io/a/b:1")
    with api.env.store._journal() as conn:
        # What slice 1 left behind: no key. A second record whose key would collide is kept apart.
        conn.execute("DROP INDEX cloud_workspaces_name_key")
        conn.execute("UPDATE cloud_workspaces SET name_key=NULL")
        conn.execute("UPDATE cloud_workspaces SET name='STRASSE' WHERE id=?", (second["id"],))
        conn.execute("PRAGMA user_version=0")  # as a database from before the migration is
        workspaces.schema(conn)
        keys = dict(conn.execute("SELECT id,name_key FROM cloud_workspaces").fetchall())
    assert keys[first["id"]] == "strasse"
    assert keys[second["id"]].startswith("strasse\x00")
    assert len(api.request(path=BASE)[1]["workspaces"]) == 2


def test_restart_needed_never_reads_as_ready(api):
    _, node = api.enrolled()
    item = create(api, node_id=node["id"])
    set_state(api, item["id"], "ready", applied_revision=1)
    api.request("POST", f"{BASE}/{item['id']}", {"expected_revision": 1, "cpu_cores": 4})
    shown = api.request(path=f"{BASE}/{item['id']}")[1]["workspace"]
    assert (shown["status"], shown["restart_needed"]) == ("restart_needed", True)
    assert shown["status"] != "ready"
    set_state(api, item["id"], "failed", "out_of_memory")
    again = api.request(path=f"{BASE}/{item['id']}")[1]["workspace"]
    assert (again["status"], again["restart_needed"]) == ("failed", True)


def test_records_beyond_the_list_limit_are_counted_not_silently_dropped(api):
    # Slice 1 allowed 200 records; a store that already holds more than 100 says so.
    with api.env.store._journal() as conn:
        for number in range(103):
            conn.execute(
                "INSERT INTO cloud_workspaces (id,name,name_key,image_source,image_reference,"
                "cpu_millicores,memory_mib,storage_gib,network,created_by,created_at,updated_at) "
                "VALUES (?,?,?,'custom','ghcr.io/acme/tools:1',2000,4096,20,'outbound','u',?,?)",
                (f"{number:032x}", f"Old {number}", f"old {number}", float(number), float(number)),
            )
    status, result = api.request(path=BASE)
    assert status == 200 and len(result["workspaces"]) == 100 and result["total"] == 103
    assert api.request("POST", BASE, body(name="New"))[1]["reason"] == "limit_reached"
    assert api.request(path=f"{BASE}/{3:032x}")[0] == 200  # a record outside the list still opens


def test_replaying_a_create_after_its_discard_answers_409_and_never_brings_the_record_back(api):
    first = api.request("POST", BASE, body(request_id="req-gone"))
    assert first[0] == 201
    item = first[1]["workspace"]
    # Before the discard a replay still returns the same record.
    assert api.request("POST", BASE, body(request_id="req-gone"))[0] == 200
    url = f"{BASE}/{item['id']}/discard"
    assert api.request("POST", url, {"expected_revision": item["version"]})[0] == 200
    status, result = api.request("POST", BASE, body(request_id="req-gone"))
    assert (status, result) == (
        409,
        {"error": "conflict", "field": "request_id", "reason": "request_discarded"},
    )
    assert api.request(path=BASE)[1]["total"] == 0
    # Another id with the same values is a fresh, deliberate create.
    assert api.request("POST", BASE, body(request_id="req-new"))[0] == 201
    # The tombstone is kept for good: the table refuses deletes and edits.
    with api.env.store._journal() as conn:
        assert conn.execute("SELECT count(*) FROM cloud_workspace_tombstones").fetchone()[0] == 1
        for statement in (
            "DELETE FROM cloud_workspace_tombstones",
            "UPDATE cloud_workspace_tombstones SET request_id='x'",
        ):
            with pytest.raises(Exception, match="tombstones"):
                conn.execute(statement)


def test_a_record_without_a_request_id_leaves_no_tombstone(api):
    item = create(api)
    assert api.request("POST", f"{BASE}/{item['id']}/discard", {"expected_revision": 1})[0] == 200
    with api.env.store._journal() as conn:
        assert conn.execute("SELECT count(*) FROM cloud_workspace_tombstones").fetchone()[0] == 0


def test_every_refusal_on_a_workspace_route_has_a_reason(api):
    item = create(api)
    url = f"{BASE}/{item['id']}"
    foreign = sessions.start_session(api.env.db, api.env.foreign, api.env.other_workspace)
    calls = [
        ("GET", BASE + "?x=1", None, {}),  # query string
        ("GET", BASE + "#frag", None, {}),
        ("GET", f"{BASE}/bad%20id", None, {}),  # identifier
        ("POST", f"{BASE}/bad%20id/discard", {"expected_revision": 1}, {}),
        ("GET", f"{BASE}/{'0' * 32}", None, {}),  # record
        ("GET", url + "/extra", None, {}),  # route
        ("GET", BASE + "/", None, {}),
        ("POST", BASE, body(cpu_cores=10**400), {}),  # huge integer: range, never a crash
        ("POST", BASE, body(memory_mib=10**400), {}),
        ("POST", BASE, body(storage_gib=-(10**400)), {}),
        ("POST", url, {"expected_revision": 10**400, "name": "x"}, {}),
        ("GET", BASE, None, {"cookie": False}),  # session
        ("GET", BASE, None, {"cookie": foreign, "member": True}),
    ]
    for method, path, payload, extra in calls:
        extra = {key: value for key, value in extra.items() if key != "member"}
        status, result = api.request(method, path, payload, **extra)
        if status < 400:
            continue  # the foreign person's own (empty) list is a success
        assert result.get("reason"), (method, path, status, result)
    # A non-owner member too.
    status, result = member_api(api)("POST", BASE, body(name="Mine"))
    assert (status, result["reason"]) == (403, "member")
    # The huge integers are refused as out of range, with the field named.
    for field in ("cpu_cores", "memory_mib", "storage_gib"):
        status, result = api.request("POST", BASE, body(**{field: 10**400}))
        assert (status, result) == (
            400,
            {"error": "invalid_request", "field": field, "reason": "range"},
        )
    status, result = api.request(
        "POST", BASE, raw=b'{"name":"x","image_source":"custom","image":"a","cpu_cores":1e999}'
    )
    assert (status, result["field"], result["reason"]) == (400, "cpu_cores", "range")
    assert api.request("GET", BASE + "?x=1")[1] == {"error": "invalid_request", "reason": "query"}


def test_an_encoded_path_is_judged_as_the_route_it_is(api):
    item = create(api)
    ask = member_api(api)
    # /%77orkspaces is /workspaces: a member reads it exactly as the plain path.
    assert ask(path="/%77orkspaces")[0] == 200
    assert ask(path="/%77orkspaces")[1] == ask(path=BASE)[1]
    assert ask(path=f"/%77orkspaces/{item['id']}")[0] == 200
    assert ask("POST", "/%77orkspaces", body(name="Mine"))[0] == 403


def test_the_migration_runs_once_and_backfills_the_version(api):
    create(api)
    with api.env.store._journal() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == workspaces.SCHEMA_VERSION
        # A database from before the version column existed: the column is added and filled from the revision.
        conn.execute("UPDATE cloud_workspaces SET revision=3")
        conn.execute("ALTER TABLE cloud_workspaces DROP COLUMN version")
        conn.execute("PRAGMA user_version=0")
        workspaces.schema(conn)
        assert conn.execute("SELECT version FROM cloud_workspaces").fetchone()[0] == 3
        assert conn.execute("PRAGMA user_version").fetchone()[0] == workspaces.SCHEMA_VERSION
        # Once done it is not repeated: a changed table is left alone.
        conn.execute("UPDATE cloud_workspaces SET version=9")
        workspaces.schema(conn)
        assert conn.execute("SELECT version FROM cloud_workspaces").fetchone()[0] == 9


def test_listing_names_each_machine_with_one_query(api):
    _, node = api.enrolled()
    for number in range(3):
        create(
            api, name=f"On machine {number}", image=f"ghcr.io/acme/a{number}:1", node_id=node["id"]
        )
    create(api, name="Nowhere", image="ghcr.io/acme/z:1")
    shown = {item["name"]: item["machine_name"] for item in api.request(path=BASE)[1]["workspaces"]}
    assert shown == {
        "On machine 0": node["name"],
        "On machine 1": node["name"],
        "On machine 2": node["name"],
        "Nowhere": None,
    }


def test_presets_are_served_and_sit_inside_the_limits(api):
    served = api.request(path=BASE + "/images")[1]["presets"]
    assert [item["id"] for item in served] == ["small", "standard", "large"]
    limits = workspaces.LIMITS
    for item in served:
        assert limits["cpu_cores"]["min"] <= item["cpu_cores"] <= limits["cpu_cores"]["max"]
        assert limits["memory_mib"]["min"] <= item["memory_mib"] <= limits["memory_mib"]["max"]
        assert limits["storage_gib"]["min"] <= item["storage_gib"] <= limits["storage_gib"]["max"]
        # Every preset is a value the form could have sent: it creates cleanly.
        created = api.request(
            "POST",
            BASE,
            body(
                name=f"Preset {item['id']}",
                image=f"ghcr.io/acme/{item['id']}:1",
                cpu_cores=item["cpu_cores"],
                memory_mib=item["memory_mib"],
                storage_gib=item["storage_gib"],
            ),
        )
        assert created[0] == 201
    standard = next(item for item in served if item["id"] == "standard")
    assert (standard["cpu_cores"], standard["memory_mib"], standard["storage_gib"]) == (2, 4096, 20)


def test_a_private_looking_name_that_only_resolves_elsewhere_is_accepted_and_documented(api):
    # The app cannot see DNS: a name that merely resolves to a private address is accepted here.
    # Pulling an image is the lifecycle card's job and it must check the address it resolved; nothing pulls yet.
    assert create(api, image="127.0.0.1.nip.io/app:1")["image_registry"] == "127.0.0.1.nip.io"


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": None},
        {"Origin": "null"},
        {"Origin": "http://evil.test"},
        {"Origin": ["http://app.test", "http://evil.test"]},
        {"Content-Type": None},
        {"Content-Type": "text/plain"},
    ],
)
def test_a_refused_request_is_not_reported_as_a_refused_person(api, headers):
    # The shared write boundary refuses a foreign Origin or a simple request with 403; on these routes that
    # is the request being wrong (invalid_request / request), never "only the owner can change ..." for the owner.
    item = create(api)
    url = f"{BASE}/{item['id']}"
    for path, payload in (
        (BASE, body(name="Foreign")),
        (url, {"expected_revision": 1, "name": "Foreign"}),
        (url + "/discard", {"expected_revision": 1}),
        (url + "/credential", {}),
    ):
        status, result = api.request("POST", path, payload, headers=headers)
        assert (status, result) == (400, {"error": "invalid_request", "reason": "request"}), path
    assert api.request(path=BASE)[1]["workspaces"] == [item]
    # A member is still told they are a member: the person check runs before the request check.
    ask = member_api(api)
    assert ask("POST", BASE, body(name="Foreign"), headers=headers) == (
        403,
        {"error": "owner_required", "reason": "member"},
    )


def test_a_signed_out_write_is_sign_in_not_invalid_request(api):
    assert api.request("POST", BASE, body(), cookie=False) == (
        401,
        {"error": "sign_in", "reason": "session"},
    )


@pytest.mark.parametrize(
    "name",
    ["Ｂuild box", "Build box", "ＢＵＩＬＤ ＢＯＸ", "build box"],
)
def test_names_that_fold_to_the_same_key_are_one_name(api, name):
    create(api, name="Build box")
    status, result = api.request("POST", BASE, body(name=name, image="ghcr.io/acme/other:1"))
    if name == "Build box":
        assert (status, result["reason"]) == (
            400,
            "characters",
        )  # a no-break space is refused outright
    else:
        assert (status, result["reason"]) == (409, "name_taken")


def test_circled_and_fullwidth_digits_and_the_ligature_do_not_coexist_with_their_plain_form(api):
    create(api, name="Box 1")
    assert api.request("POST", BASE, body(name="Box ①", image="ghcr.io/a/b:1"))[1]["reason"] == (
        "name_taken"
    )
    create(api, name="office", image="ghcr.io/a/c:1")
    assert api.request("POST", BASE, body(name="oﬃce", image="ghcr.io/a/d:1"))[1]["reason"] == (
        "name_taken"
    )


@pytest.mark.parametrize("name", ["Straße", "ǅ", "ﬃ", "Ａ① ｂ", "İstanbul", "ΐ", "ǰ", "ẞ"])
def test_the_name_key_is_a_fixed_point(name):
    key = workspaces.name_key(name)
    assert workspaces.name_key(key) == key


def test_stored_keys_are_recomputed_once_with_the_older_record_keeping_the_plain_key(api):
    first = create(api, name="Box 1")
    second = create(api, name="Other", image="ghcr.io/a/b:1")
    with api.env.store._journal() as conn:
        # What the previous release stored: a name key that was only NFC and case-folded.
        conn.execute("DROP INDEX cloud_workspaces_name_key")
        conn.execute(
            "UPDATE cloud_workspaces SET name='Box ①',name_key='box ①' WHERE id=?", (second["id"],)
        )
        conn.execute("UPDATE cloud_workspaces SET created_at=1 WHERE id=?", (first["id"],))
        conn.execute("UPDATE cloud_workspaces SET created_at=2 WHERE id=?", (second["id"],))
        conn.execute("PRAGMA user_version=2")
        workspaces.schema(conn)
        keys = dict(conn.execute("SELECT id,name_key FROM cloud_workspaces").fetchall())
        assert conn.execute("PRAGMA user_version").fetchone()[0] == workspaces.SCHEMA_VERSION
    assert keys[first["id"]] == "box 1"
    assert keys[second["id"]] == f"box 1\x00{second['id']}"
    # Both records are still listed and the new key applies to everything created afterwards.
    assert api.request(path=BASE)[1]["total"] == 2
    assert api.request("POST", BASE, body(name="BOX 1", image="ghcr.io/a/e:1"))[1]["reason"] == (
        "name_taken"
    )


def test_ready_needs_an_applied_revision_that_covers_the_record(api):
    item = create(api)
    for kwargs in ({}, {"applied_revision": 0}):
        with pytest.raises(ValueError):
            set_state(api, item["id"], "ready", **kwargs)
    api.request("POST", f"{BASE}/{item['id']}", {"expected_revision": 1, "cpu_cores": 3})
    # The record is on revision 2: a machine that applied revision 1 is not ready for it.
    with pytest.raises(ValueError):
        set_state(api, item["id"], "ready", applied_revision=1)
    shown = api.request(path=f"{BASE}/{item['id']}")[1]["workspace"]
    assert (shown["status"], shown["started"], shown["applied_revision"]) == (
        "no_machine",
        False,
        None,
    )
    _, node = api.enrolled()
    api.request("POST", f"{BASE}/{item['id']}", {"expected_revision": 2, "node_id": node["id"]})
    set_state(api, item["id"], "ready", applied_revision=3)
    shown = api.request(path=f"{BASE}/{item['id']}")[1]["workspace"]
    assert (shown["status"], shown["applied_revision"]) == ("ready", 3)


def _old_database(conn):
    """The table as the first release left it: no key, request, hash or version column."""
    conn.execute("UPDATE cloud_workspaces SET revision=3")
    for column in ("version", "request_id", "request_hash", "name_key"):
        conn.execute("DROP INDEX IF EXISTS cloud_workspaces_name_key")
        conn.execute("DROP INDEX IF EXISTS cloud_workspaces_request")
        conn.execute(f"ALTER TABLE cloud_workspaces DROP COLUMN {column}")
    conn.execute("DROP TABLE cloud_workspace_tombstones")
    conn.execute("PRAGMA user_version=0")


def test_a_failure_between_migration_steps_changes_nothing_and_the_next_open_finishes_it(
    api, monkeypatch
):
    create(api)
    with api.env.store._journal() as conn:
        _old_database(conn)
    store = api.env.store
    original = workspaces.name_key
    calls = []

    def crash(name):
        calls.append(name)
        raise RuntimeError("the process died between the column and the backfill")

    monkeypatch.setattr(workspaces, "name_key", crash)
    with pytest.raises(RuntimeError):
        with store._journal():
            pass
    assert calls
    monkeypatch.setattr(workspaces, "name_key", original)
    # Opened by hand, without the migration, to see what the failed attempt left behind.
    import sqlite3

    raw = sqlite3.connect(str(store._path() if callable(store._path) else store._path))
    try:
        columns = {row[1] for row in raw.execute("PRAGMA table_info(cloud_workspaces)")}
        assert "version" not in columns and "name_key" not in columns
        assert raw.execute("PRAGMA user_version").fetchone()[0] == 0
    finally:
        raw.close()
    with store._journal() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == workspaces.SCHEMA_VERSION
        row = conn.execute("SELECT version,name_key,revision FROM cloud_workspaces").fetchone()
        assert (row["version"], row["name_key"], row["revision"]) == (3, "build box", 3)


def test_the_migration_takes_its_own_transaction_and_rolls_back_as_one(api, monkeypatch):
    create(api)
    import sqlite3

    path = api.env.store._path() if callable(api.env.store._path) else api.env.store._path
    with api.env.store._journal() as conn:
        _old_database(conn)
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        monkeypatch.setattr(
            workspaces, "name_key", lambda name: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        with pytest.raises(RuntimeError):
            workspaces.schema(conn)
        assert not conn.in_transaction
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        assert "version" not in {
            row[1] for row in conn.execute("PRAGMA table_info(cloud_workspaces)")
        }
    finally:
        conn.close()


def _statements(monkeypatch):
    """Every SQL statement a request runs inside the Compute journal (each request opens its own Store)."""
    from commonplace.compute.control import Store

    seen = []
    original = Store._journal

    @contextmanager
    def journal(self):
        with original(self) as conn:
            conn.set_trace_callback(seen.append)
            yield conn

    monkeypatch.setattr(Store, "_journal", journal)
    return seen


def test_listing_runs_the_same_number_of_statements_however_many_records_there_are(
    api, monkeypatch
):
    _, node = api.enrolled()
    seen = _statements(monkeypatch)
    api.request(path=BASE)
    empty = len(seen)
    assert empty  # the count below compares real statements, not two empty lists
    for number in range(8):
        create(api, name=f"On {number}", image=f"ghcr.io/acme/a{number}:1", node_id=node["id"])
    seen.clear()
    status, result = api.request(path=BASE)
    assert status == 200 and len(result["workspaces"]) == 8
    assert all(item["machine_name"] == node["name"] for item in result["workspaces"])
    assert len(seen) == empty
    # One JOINed query names the machines: no statement reads the machines table on its own.
    assert not [text for text in seen if "FROM nodes" in text and "cloud_workspaces" not in text]
    seen.clear()
    api.request(path=f"{BASE}/{result['workspaces'][0]['id']}")
    assert len([text for text in seen if "cloud_workspaces" in text and "SELECT" in text]) == 1


def _legacy_duplicate(api):
    """Two records whose names fold to one key, as a store from before the NFKC key can hold them."""
    first = create(api, name="Box 1")
    second = create(api, name="Other", image="ghcr.io/a/b:1")
    with api.env.store._journal() as conn:
        conn.execute("DROP INDEX cloud_workspaces_name_key")
        conn.execute(
            "UPDATE cloud_workspaces SET name='Box ①',name_key='box ①' WHERE id=?", (second["id"],)
        )
        conn.execute("UPDATE cloud_workspaces SET created_at=1 WHERE id=?", (first["id"],))
        conn.execute("UPDATE cloud_workspaces SET created_at=2 WHERE id=?", (second["id"],))
        conn.execute("PRAGMA user_version=2")
        workspaces.schema(conn)
    return first, second


def test_renaming_into_the_name_of_a_legacy_suffixed_duplicate_is_name_taken_not_a_server_error(
    api,
):
    first, second = _legacy_duplicate(api)
    third = create(api, name="Third", image="ghcr.io/a/c:1")
    # The record holding the plain key moves away, so only the suffixed duplicate still holds the name.
    assert (
        api.request("POST", f"{BASE}/{first['id']}", {"expected_revision": 1, "name": "Moved"})[0]
        == 200
    )
    for taken in ("Box ①", "BOX 1", "Box 1"):
        status, result = api.request(
            "POST", f"{BASE}/{third['id']}", {"expected_revision": 1, "name": taken}
        )
        assert (status, result["reason"], result["field"]) == (409, "name_taken", "name"), taken
    shown = api.request(path=f"{BASE}/{third['id']}")[1]["workspace"]
    assert shown["name"] == "Third" and shown["version"] == 1


def test_a_stored_image_reference_that_cannot_be_read_does_not_fail_the_whole_list(api):
    good = create(api, name="Good")
    bad = create(api, name="Bad", image="ghcr.io/a/b:1")
    with api.env.store._journal() as conn:
        conn.execute(
            "UPDATE cloud_workspaces SET image_reference='ftp://broken host' WHERE id=?",
            (bad["id"],),
        )
    status, result = api.request(path=BASE)
    assert status == 200, result
    rows = {row["id"]: row for row in result["workspaces"]}
    assert set(rows) == {good["id"], bad["id"]}
    assert rows[good["id"]]["image_unreadable"] is False
    broken = rows[bad["id"]]
    assert broken["image_unreadable"] is True
    assert broken["image"] == "" and broken["image_registry"] is None
    assert broken["image_tag"] is None and broken["image_digest"] is None
    assert broken["image_pinned"] is False
    # The same row on its own page and for a member: still a fixed projection, never an error.
    assert api.request(path=f"{BASE}/{bad['id']}")[0] == 200
    assert member_api(api)("GET", BASE)[0] == 200


def test_the_control_journal_only_turns_protocol_denials_into_a_store_error(api):
    from commonplace.compute.control import StoreError

    with pytest.raises(TypeError):
        with api.env.store._journal():
            raise TypeError("a bug in the caller")
    with pytest.raises(OverflowError):
        with api.env.store._journal():
            raise OverflowError("a bug in the caller")
    from commonplace.compute.protocol import ProtocolDenied

    with pytest.raises(StoreError):
        with api.env.store._journal():
            raise ProtocolDenied("bad")


def test_a_stored_image_reference_that_cannot_be_read_does_not_block_edits(api):
    item = create(api, name="Old", image="ghcr.io/a/b:1")
    url = f"{BASE}/{item['id']}"
    with api.env.store._journal() as conn:
        conn.execute(
            "UPDATE cloud_workspaces SET image_reference='ftp://broken host' WHERE id=?",
            (item["id"],),
        )
    # A rename, then a limit change: neither touches the image, so neither is refused because of it.
    status, result = api.request("POST", url, {"expected_revision": 1, "name": "Renamed"})
    assert status == 200, result
    assert result["workspace"]["name"] == "Renamed" and result["workspace"]["image_unreadable"]
    status, result = api.request("POST", url, {"expected_revision": 2, "memory_mib": 8192})
    assert status == 200, result
    assert result["workspace"]["memory_mib"] == 8192 and result["workspace"]["image_unreadable"]
    # Choosing an image repairs the record; an invalid new one is still refused with its own reason.
    status, result = api.request(
        "POST", url, {"expected_revision": 3, "image_source": "custom", "image": "ftp://x"}
    )
    assert (status, result["field"], result["reason"]) == (400, "image", "scheme")
    status, result = api.request(
        "POST",
        url,
        {"expected_revision": 3, "image_source": "custom", "image": "ghcr.io/acme/tools:2"},
    )
    assert status == 200, result
    assert result["workspace"]["image"] == "ghcr.io/acme/tools:2"
    assert result["workspace"]["image_unreadable"] is False

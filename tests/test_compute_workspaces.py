"""Cloud workspace records on the owner Compute REST: validation, status, access and edits."""

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
    assert api.request(path=BASE) == (200, {"workspaces": [], "can_manage": True})
    item = create(api)
    assert item["cpu_cores"] == 2 and item["memory_mib"] == 4096 and item["storage_gib"] == 20
    assert item["network"] == "outbound"
    assert item["status"] == "no_machine" and item["status_reason"] is None
    assert item["started"] is False and item["restart_needed"] is False
    assert item["node_id"] is None and item["machine_name"] is None
    assert item["image"] == "ghcr.io/acme/tools:1.2" and item["image_pinned"] is False
    assert item["image_registry"] == "ghcr.io" and item["image_tag"] == "1.2"
    assert api.request(path=BASE) == (200, {"workspaces": [item], "can_manage": True})
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


@pytest.mark.parametrize(
    "reference",
    [
        "ubuntu",
        "ubuntu:24.04",
        "library/ubuntu:24.04",
        "docker.io/library/ubuntu:24.04",
        "ghcr.io/acme/tools@" + DIGEST,
        "ghcr.io/acme/tools:1.2@" + DIGEST,
        "registry.example.com:5000/team/app/sub-name_x:v1.0-rc1",
    ],
)
def test_valid_image_references_are_accepted_and_pinning_is_reported(api, reference):
    item = create(api, image=reference)
    assert item["image"] == reference
    assert item["image_pinned"] is ("@" in reference)
    assert item["image_digest"] == (DIGEST if "@" in reference else None)


@pytest.mark.parametrize(
    "reference,reason",
    [
        ("", "empty"),
        ("a" * 256, "too_long"),
        ("ubuntu; rm -rf /", "characters"),
        ("ubuntu && id", "characters"),
        ("ubuntu`id`", "characters"),
        ("ubuntu$(id)", "characters"),
        ("ubuntu|cat", "characters"),
        ("ubuntu\nwhoami", "characters"),
        ("ubuntu\x00", "characters"),
        ("ubuntu image", "characters"),
        ("ubuntu'", "characters"),
        ('ubuntu"', "characters"),
        ("--privileged", "characters"),
        ("-v/:/host", "characters"),
        ("/etc/passwd", "characters"),
        ("../../etc", "characters"),
        (".hidden", "characters"),
        ("https://evil.example/app", "scheme"),
        ("docker://ubuntu", "scheme"),
        ("user@host.example/app", "digest"),
        ("ubuntu@sha256:abc", "digest"),
        ("ubuntu@sha512:" + "a" * 128, "digest"),
        ("ubuntu@" + DIGEST + "@" + DIGEST, "digest"),
        ("Ubuntu", "name"),
        ("ubuntu//x", "name"),
        ("ubuntu/..", "name"),
        ("ubuntu:", "tag"),
        ("ubuntu:-x", "tag"),
        ("ubuntu:" + "a" * 129, "tag"),
        ("Registry.Example/app", "registry"),
        ("host.example:99999/app", "registry"),
        ("host.example:0/app", "registry"),
        ("héllo/app", "characters"),
        # Registries on local or private networks, IP literals in any notation, and no path.
        ("localhost/app", "registry_private"),
        ("localhost:5000/app:latest", "registry_private"),
        ("127.0.0.1:5000/app", "registry_private"),
        ("169.254.169.254/app:1", "registry_private"),
        ("10.0.0.5/team/app", "registry_private"),
        ("192.168.1.10:5000/app", "registry_private"),
        ("172.16.0.1/app", "registry_private"),
        ("0x7f.0.0.1/app", "registry_private"),
        ("registry:5000/app", "registry_private"),
        ("registry.local/app", "registry_private"),
        ("build.internal:5000/app", "registry_private"),
        ("foo.localhost/app", "registry_private"),
        ("ghcr.io:443", "registry_no_path"),
        ("ghcr.io", "registry_no_path"),
        ("registry.example.com:5000", "registry_no_path"),
    ],
)
def test_malformed_and_injected_image_references_are_refused_and_never_stored(
    api, reference, reason
):
    status, result = api.request("POST", BASE, body(image=reference))
    assert status == 400
    assert result == {"error": "invalid_request", "field": "image", "reason": reason}
    assert api.request(path=BASE) == (200, {"workspaces": [], "can_manage": True})


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
        ("\u2800\u2800", "no_letter"),
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


def test_edit_bumps_the_revision_only_for_what_needs_a_restart(api):
    item = create(api)
    url = f"{BASE}/{item['id']}"
    renamed = api.request("POST", url, {"expected_revision": 1, "name": "Renamed"})[1]["workspace"]
    assert renamed["name"] == "Renamed" and renamed["revision"] == 1
    resized = api.request(
        "POST", url, {"expected_revision": 1, "cpu_cores": 4, "memory_mib": 8192}
    )[1]["workspace"]
    assert (resized["cpu_cores"], resized["memory_mib"], resized["revision"]) == (4, 8192, 2)
    assert resized["restart_needed"] is False  # never started: applies when it starts
    same = api.request("POST", url, {"expected_revision": 2, "cpu_cores": 4})[1]["workspace"]
    assert same["revision"] == 2 and same["updated_at"] == resized["updated_at"]


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
    denied = (403, {"error": "owner_required"})
    assert ask("POST", BASE, body(name="Mine")) == denied
    assert ask("POST", f"{BASE}/{item['id']}", {"expected_revision": 1, "name": "Mine"}) == denied
    assert ask("POST", f"{BASE}/{item['id']}/credential", {}) == denied
    assert ask("POST", f"{BASE}/{item['id']}/discard", {"expected_revision": 1}) == denied
    # The owner check comes before the body is read: a bad body is still a 403 for a member.
    for path in (BASE, f"{BASE}/{item['id']}", f"{BASE}/{item['id']}/discard"):
        assert ask("POST", path, raw=b"not json") == denied
        assert ask("POST", path, {"unknown": 1}) == denied
    # Everything else in Compute stays owner-only for a member.
    for path in ("", "/nodes", "/credentials", "/workspaces-x"):
        assert ask(path=path) == denied
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
            {"error": "owner_required"},
        ), (method, path)
        # Without the cookie the token alone is just as unable to act.
        status, result = api.request(method, path, payload, headers=headers, cookie=False)
        assert status in (401, 403) and "workspace" not in result and "workspaces" not in result
    assert api.request(path=BASE)[1] == before


def test_signed_out_and_foreign_people_are_refused_consistently(api):
    item = create(api)
    assert api.request(path=BASE, cookie=False)[0] == 401
    assert api.request("POST", BASE, body(name="Z"), cookie=False)[0] == 401
    foreign = sessions.start_session(api.env.db, api.env.foreign, api.env.other_workspace)
    # A person of another workspace reads their own (empty) list and cannot reach this one's ids.
    assert api.request(path=BASE, cookie=foreign) == (200, {"workspaces": [], "can_manage": True})
    assert api.request(path=f"{BASE}/{item['id']}", cookie=foreign) == (404, {"error": "not_found"})
    assert api.request(
        "POST", f"{BASE}/{item['id']}", {"expected_revision": 1, "name": "X"}, cookie=foreign
    ) == (404, {"error": "not_found"})
    assert api.request("POST", f"{BASE}/{item['id']}", raw=b"[]", cookie=foreign) == (
        404,
        {"error": "not_found"},
    )
    assert api.request(path=f"{BASE}/{'0' * 32}")[0] == 404
    assert api.request(path=f"{BASE}/bad-id")[0] == 404


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
    assert status == 200 and len(result["workspaces"]) == 100
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

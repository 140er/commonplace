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
        "localhost/app",
        "localhost:5000/app:latest",
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
    "payload",
    [
        {},
        {"name": "x"},
        {"name": "x", "image_source": "custom"},
        {**body(), "secret": "x"},
        {**body(), "registry_password": "hunter2"},
        {**body(), "registry_credential_id": "x"},
        {**body(), "state": "ready"},
        {**body(), "applied_revision": 1},
        {**body(), "id": "chosen"},
        {**body(), "session_hash": "x"},
    ],
)
def test_unknown_missing_and_server_owned_fields_are_refused(api, payload):
    assert api.request("POST", BASE, payload) == (400, {"error": "invalid_request"})
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


def test_unknown_machine_is_not_found_and_nothing_is_stored(api):
    status, result = api.request("POST", BASE, body(node_id="0" * 32))
    assert (status, result["error"], result["field"]) == (404, "not_found", "node_id")
    assert api.request(path=BASE)[1]["workspaces"] == []
    assert api.request("POST", BASE, body(node_id="not valid!"))[0] == 400


def test_status_codes_follow_the_record(api):
    _, node = api.enrolled()
    item = create(api, node_id=node["id"], private_registry=True)
    assert item["status"] == "needs_credential" and item["credential_bound"] is False
    status, bound = api.request(
        "POST",
        f"{BASE}/{item['id']}/credential",
        {"credential_id": "registry-login", "credential_version": 2},
    )
    assert status == 200 and bound["workspace"]["status"] == "pending"
    assert bound["workspace"]["registry_credential_id"] == "registry-login"
    set_state(api, item["id"], "ready", applied_revision=1)
    ready = api.request(path=f"{BASE}/{item['id']}")[1]["workspace"]
    assert ready["status"] == "ready" and ready["started"] is True
    set_state(api, item["id"], "failed", "out_of_memory")
    failed = api.request(path=f"{BASE}/{item['id']}")[1]["workspace"]
    assert (failed["status"], failed["status_reason"]) == ("failed", "out_of_memory")


def test_lifecycle_seam_refuses_unknown_states_and_reasons(api):
    item = create(api)
    for args in (("running", None), ("failed", None), ("failed", "because"), ("ready", "x")):
        with pytest.raises(ValueError):
            set_state(api, item["id"], *args)


def test_registry_credential_is_a_reference_bound_once(api):
    plain = create(api, name="Plain")
    # A workspace that does not say it needs a registry login cannot take one.
    status, result = api.request(
        "POST", f"{BASE}/{plain['id']}/credential", {"credential_id": "x", "credential_version": 1}
    )
    assert (status, result["error"], result["reason"]) == (409, "conflict", "registry_not_private")
    private = create(api, name="Private", private_registry=True)
    url = f"{BASE}/{private['id']}/credential"
    for bad in (
        {},
        {"credential_id": "x"},
        {"credential_id": "x", "credential_version": 0},
        {"credential_id": "bad id!", "credential_version": 1},
        {"credential_id": "x", "credential_version": 1, "password": "hunter2"},
    ):
        assert api.request("POST", url, bad) == (400, {"error": "invalid_request"})
    ok = {"credential_id": "registry-login", "credential_version": 1}
    assert api.request("POST", url, ok)[0] == 200
    assert api.request("POST", url, {**ok, "credential_id": "other"}) == (
        409,
        {"error": "credential_already_bound"},
    )


def test_registry_credential_is_dropped_when_the_registry_or_the_flag_changes(api):
    item = create(api, private_registry=True)
    url = f"{BASE}/{item['id']}"
    api.request("POST", url + "/credential", {"credential_id": "login", "credential_version": 1})
    same_registry = api.request(
        "POST", url, {"expected_revision": 1, "image": "ghcr.io/acme/other:2"}
    )[1]["workspace"]
    assert same_registry["credential_bound"] is True and same_registry["revision"] == 2
    moved = api.request(
        "POST", url, {"expected_revision": 2, "image": "registry.example.com/acme/tools:1"}
    )[1]["workspace"]
    assert moved["credential_bound"] is False and moved["status"] == "no_machine"
    assert moved["private_registry"] is True
    api.request("POST", url + "/credential", {"credential_id": "login", "credential_version": 1})
    public = api.request("POST", url, {"expected_revision": 3, "private_registry": False})[1]
    assert public["workspace"]["credential_bound"] is False


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
    assert edited["restart_needed"] is True and edited["status"] == "ready"
    set_state(api, item["id"], "ready", applied_revision=2)
    assert api.request(path=url)[1]["workspace"]["restart_needed"] is False
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


def test_member_reads_but_never_writes_and_sees_no_registry_reference(api):
    _, node = api.enrolled()
    item = create(api, node_id=node["id"], private_registry=True)
    api.request(
        "POST",
        f"{BASE}/{item['id']}/credential",
        {"credential_id": "registry-login", "credential_version": 1},
    )
    ask = member_api(api)
    status, result = ask(path=BASE)
    assert status == 200 and result["can_manage"] is False
    seen = result["workspaces"][0]
    assert seen["name"] == item["name"] and seen["machine_name"] == node["name"]
    assert "registry_credential_id" not in seen and "registry_credential_version" not in seen
    assert "address" not in seen and "host_key" not in seen
    status, detail = ask(path=f"{BASE}/{item['id']}")
    assert status == 200 and detail["can_manage"] is False
    assert ask(path=BASE + "/images")[0] == 200
    denied = (403, {"error": "owner_required"})
    assert ask("POST", BASE, body(name="Mine")) == denied
    assert ask("POST", f"{BASE}/{item['id']}", {"expected_revision": 1, "name": "Mine"}) == denied
    assert (
        ask(
            "POST",
            f"{BASE}/{item['id']}/credential",
            {"credential_id": "x", "credential_version": 1},
        )
        == denied
    )
    # Everything else in Compute stays owner-only for a member.
    for path in ("", "/nodes", "/credentials", "/workspaces-x"):
        assert ask(path=path) == denied
    assert api.request(path=f"{BASE}/{item['id']}")[1]["workspace"]["name"] == item["name"]


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

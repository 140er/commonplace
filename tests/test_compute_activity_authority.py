"""Current authority fences every protected Activity read and consent surface."""

import pytest
from test_compute_activity_api import activity_hub, enrolled, request  # noqa: F401


@pytest.mark.parametrize("change", ["expires_at", "revoked", "owner", "workspace", "kind"])
def test_current_connection_fences_all_owner_activity_surfaces(enrolled, change):  # noqa: F811
    hub, machine, path, snapshot = enrolled
    assert request(hub, "POST", path + "/consent", {"enabled": True})[0] == 200
    assert (
        request(
            hub,
            "POST",
            "/api/runner/activity",
            snapshot,
            cookie=False,
            token=machine["machine_token"],
        )[0]
        == 200
    )
    other = None
    if change == "owner":
        other = hub.db.create_user("other@example.test")
        hub.db.add_member(hub.workspace, other, "owner", actor=hub.user)
    elif change == "workspace":
        other = hub.db.create_workspace("Other", hub.user)
    with hub.db._tx():
        cid = machine["machine_connection_id"]
        if change == "expires_at":
            hub.db._conn.execute(
                "UPDATE connections SET expires_at=? WHERE id=?", ("2001-01-01T00:00:00Z", cid)
            )
        elif change == "revoked":
            hub.db._conn.execute(
                "UPDATE connections SET revoked_at=? WHERE id=?", ("2001-01-01T00:00:00Z", cid)
            )
        elif change == "owner":
            hub.db._conn.execute("UPDATE connections SET user_id=? WHERE id=?", (other, cid))
        elif change == "workspace":
            hub.db._conn.execute("UPDATE connections SET workspace_id=? WHERE id=?", (other, cid))
        else:
            hub.db._conn.execute(
                "UPDATE runner_connections SET kind='worker' WHERE connection_id=?", (cid,)
            )
    assert request(hub, path=path)[0] == 404
    assert request(hub, path=path + "/" + snapshot["threads"][0]["id"])[0] == 404
    assert request(hub, "POST", path + "/consent", {"enabled": True})[0] == 404
    assert machine["machine_id"] not in {host["id"] for host in request(hub)[1]["hosts"]}

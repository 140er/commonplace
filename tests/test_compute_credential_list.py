"""Reference-only credential list for the gateway/target form; real custody files, no keys leave."""

import json
import os
import re
import stat
from pathlib import Path

import pytest

from commonplace import sessions
from commonplace.compute.credentials import (
    CredentialBinding,
    CredentialStore,
    initialize_wrapping_key,
)
from tests.test_compute_api import API
from tests.test_compute_control import compute_env  # noqa: F401 -- shared real-store fixture

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX protected custody boundary")

SECRET_PARTS = re.compile(r"ssh-ed25519|BEGIN|PRIVATE|encrypted_key|public_key|wrapped")


@pytest.fixture
def api(compute_env):  # noqa: F811
    return API(compute_env)


def private_root(root):
    """Owner-only workspace folder, as production custody requires; the parent only loses group write."""
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    root.parent.chmod(0o755)


class Custody:
    """Provisions synthetic custody the way an operator would, outside the browser."""

    def __init__(self, env, tmp_path):
        self.env, self.root = env, env.path.parent
        private_root(self.root)
        self.database = self.root / "compute-credentials.sqlite3"
        service = tmp_path / "service-key"
        service.mkdir(mode=0o700)
        self.args = (self.database, service / "compute.wrap", self.root, lambda *_: True)
        self.context = object()
        initialize_wrapping_key(self.args[1], self.root, database_path=self.database)
        self.store = CredentialStore.initialize(*self.args, clock=lambda: env.clock.now)

    def make(self, purpose="gateway", account="svc", version=1, workspace=None, **kw):
        node = "" if purpose == "gateway" else "node-one"
        binding = CredentialBinding(
            workspace or self.env.workspace, "owner", "gateway-one", node, account, purpose, version
        )
        return self.store.generate(binding, self.context, **kw)


@pytest.fixture
def custody(api, tmp_path, compute_private_filesystem):
    made = Custody(api.env, tmp_path)
    try:
        yield made
    finally:
        made.store.close()


def test_no_custody_yet_is_reported_without_creating_anything(
    api, tmp_path, compute_private_filesystem
):
    private_root(api.env.path.parent)
    assert api.request(path="/credentials") == (200, {"credentials": [], "status": "unprovisioned"})
    assert not (api.env.path.parent / "compute-credentials.sqlite3").exists()


def test_list_is_reference_only_and_never_carries_key_material(api, custody, monkeypatch):
    gateway = custody.make("gateway", "svc", 1)
    target = custody.make("target", "worker", 2)
    revoked = custody.make("gateway", "old", 3)
    custody.store.revoke(revoked.credential_id, revoked.binding, custody.context)
    # The route reads the real clock, so this credential was issued long ago and has since run out.
    api.env.clock.now -= 1000
    expired = custody.make("gateway", "late", 4, expires_at=api.env.clock.now + 5)
    api.env.clock.now += 1000
    custody.make("gateway", "elsewhere", 1, workspace=api.env.other_workspace)

    def forbidden(*args, **kwargs):
        pytest.fail("the list must not open the encrypted store or its wrapping key")

    monkeypatch.setattr(CredentialStore, "__init__", forbidden)
    status, body = api.request(path="/credentials")
    assert status == 200 and body["status"] == "ready"
    rows = {row["id"]: row for row in body["credentials"]}
    assert set(rows) == {
        gateway.credential_id,
        target.credential_id,
        revoked.credential_id,
        expired.credential_id,
    }
    assert all(
        set(row) == {"id", "purpose", "account", "version", "state", "expires_at"}
        for row in rows.values()
    )
    assert rows[gateway.credential_id] | {"expires_at": 0} == {
        "id": gateway.credential_id,
        "purpose": "gateway",
        "account": "svc",
        "version": 1,
        "state": "pending",
        "expires_at": 0,
    }
    assert (
        rows[target.credential_id]["purpose"] == "target"
        and rows[target.credential_id]["version"] == 2
    )
    assert rows[revoked.credential_id]["state"] == "revoked"
    assert rows[expired.credential_id]["state"] == "expired"
    text = json.dumps(body)
    assert not SECRET_PARTS.search(text)
    for value in (gateway.public_key, "gateway-one", "node-one", "owner"):
        assert value not in text
    assert api.env.other_workspace not in text and "elsewhere" not in text


def test_unsafe_or_unreadable_custody_reports_unavailable_without_detail(api, custody):
    custody.make()
    custody.database.chmod(0o644)
    status, body = api.request(path="/credentials")
    assert (status, body) == (200, {"credentials": [], "status": "unavailable"})
    custody.database.chmod(0o600)
    custody.database.write_bytes(b"not a database")
    assert api.request(path="/credentials") == (200, {"credentials": [], "status": "unavailable"})


def test_only_the_workspace_owner_may_list_and_the_route_is_read_only(api, custody):
    member = sessions.start_session(api.env.db, api.env.member, api.env.workspace)
    assert api.request(path="/credentials", cookie=member) == (403, {"error": "owner_required"})
    assert api.request("POST", "/credentials", {})[0] == 404
    assert api.request(path="/credentials/cc_" + "0" * 32)[0] == 404
    assert stat.S_IMODE(custody.database.stat().st_mode) == 0o600


def test_doc_provisioning_step_runs_and_its_result_is_listed(
    api, tmp_path, compute_private_filesystem
):
    """The exact operator block shown in the form (documented in RUNNING.md) creates a listed credential."""
    doc = Path("docs/compute/RUNNING.md").read_text()
    block = re.search(r"```sh\n(uv run python - <<'PY'\n.*?\nPY)\n```", doc, re.S)
    assert block, "RUNNING.md documents the credential provisioning step"
    script = block.group(1).split("\n", 1)[1].rsplit("\nPY", 1)[0]
    root = api.env.path.parent
    private_root(root)
    service = tmp_path / "service-key"
    service.mkdir(mode=0o700)
    for old, new in {
        "WORKSPACE_DATA_DIR": str(root),
        "WRAPPING_KEY_FILE": str(service / "compute.wrap"),
        "WORKSPACE_ID": api.env.workspace,
        "PERSON_ID": api.env.owner,
        "GATEWAY_ID": "gateway-one",
        "ACCOUNT": "svc",
        "PURPOSE": "gateway",
        "VERSION": "1",
    }.items():
        script = script.replace(old, new)
    import contextlib
    import io

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(compile(script, "provision", "exec"), {"__name__": "provision"})
    issued = out.getvalue().split()[0]
    assert re.fullmatch(r"cc_[0-9a-f]{32}", issued) and "PRIVATE" not in out.getvalue()
    status, body = api.request(path="/credentials")
    assert status == 200 and [row["id"] for row in body["credentials"]] == [issued]

"""Full app and real owner REST: synthetic pending enrollments, no remote I/O."""

import threading

import pytest

from commonplace import sessions
from commonplace.boards.service import BoardService
from commonplace.compute.control import open_store
from commonplace.compute.transport import ParamikoDriver
from commonplace.control_db import ControlDB
from commonplace.extensions import ToolContext
from commonplace.gateway import Gateway
from commonplace.workers import WorkerSupervisor
from tests.test_compute_admission import admission_state as admission_state
from tests.test_compute_control import compute_env as compute_env, public_pin
from tests.test_compute_executor import execution_env as execution_env
from tests.test_compute_fence import local_state as local_state
from tests.test_compute_script_integration import script_state as script_state


def test_compute_browser_enrollment_scope_and_exact_approval(tmp_path, monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    for name in ("CHAT", "HOLD"):
        monkeypatch.setenv(f"COMMONPLACE_{name}_WORKER", "off")
    monkeypatch.setenv("COMMONPLACE_RATE_LIMITS", "off")
    for name in ("COMMONPLACE_ALLOWED_HOSTS", "COMMONPLACE_APP_URL", "COMMONPLACE_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        ParamikoDriver, "connect_gateway", lambda *args: pytest.fail("No remote transport")
    )
    db = ControlDB(str(tmp_path / "control.sqlite3"))
    supervisor = WorkerSupervisor(str(tmp_path), launcher=object())
    gateway = Gateway(db, supervisor)
    owner = db.create_user("owner@synthetic.test")
    wid = db.create_workspace("Compute browser fixture", owner)
    cookie = sessions.start_session(db, owner, wid)
    principal = sessions.session_principal(db, cookie)
    service = BoardService(ToolContext(principal=principal, gateway=gateway))
    service.board_add("compute", wing="fixture")
    task = service.task_add("Inspect synthetic target", board="compute")["number"]
    server = gateway.build_server("127.0.0.1", 0)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                context.add_cookies([{"name": sessions.COOKIE, "value": cookie, "url": origin}])
                page = context.new_page()
                page.set_default_timeout(10000)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/app/#/compute")
                page.locator('[data-compute-action="gateway"]').click()
                page.locator('select[name="board"]').select_option("compute")
                _enrollment(
                    page, "Fixture gateway", "192.0.2.10", "gateway-credential", public_pin(1)
                )
                page.get_by_role("button", name="Save pending gateway", exact=True).click()
                page.locator(".compute-row strong").filter(has_text="Fixture gateway").wait_for()
                page.locator('[data-compute-action="node"]').click()
                gateway_id = open_store(gateway, wid).list_gateways(
                    principal, session_hash=sessions._hash(cookie)
                )[0]["id"]
                page.locator('select[name="gateway_id"]').select_option(gateway_id)
                page.locator('input[name="interface"]').fill("eth0")
                _enrollment(page, "Fixture target", "10.77.0.9", "target-credential", public_pin(2))
                page.get_by_role("button", name="Save pending target", exact=True).click()
                page.locator(".compute-row strong").filter(has_text="Fixture target").wait_for()
                page.locator('[data-compute-action="scope"]').click()
                page.locator('select[name="gateway_id"]').select_option(gateway_id)
                for name, value in {
                    "task_number": task,
                    "hosts": 4,
                    "concurrency": 1,
                    "rate_per_second": 1,
                    "duration_seconds": 10,
                    "results": 4,
                    "expires_minutes": 5,
                }.items():
                    page.locator(f'input[name="{name}"]').fill(str(value))
                page.locator('textarea[name="cidrs"]').fill("10.77.0.0/24")
                page.locator('textarea[name="interfaces"]').fill("eth0")
                page.locator('input[name="scope_confirmed"]').check()
                page.get_by_role("button", name="Grant bounded passive scope", exact=True).click()
                page.locator('[data-compute-action="revoke"]').click()
                page.get_by_text("Passive scope revoked.", exact=True).wait_for()
                page.locator('[data-compute-action="proposal"]').click()
                store = open_store(gateway, wid)
                node_id = store.list_nodes(principal, session_hash=sessions._hash(cookie))[0]["id"]
                page.locator('select[name="node_id"]').select_option(node_id)
                page.locator('input[name="task_number"]').fill(str(task))
                page.get_by_role("button", name="Create proposal for approval", exact=True).click()
                page.locator('[data-compute-action="approve"]').click()
                execution = store.list_executions(principal, session_hash=sessions._hash(cookie))[0]
                page.locator('input[name="expected_digest"]').fill(execution["digest"])
                page.locator('input[name="expires_minutes"]').fill("1")
                page.get_by_role(
                    "button", name="Record approval without dispatch", exact=True
                ).click()
                approved = page.locator(".compute-row").filter(has_text=execution["digest"])
                approved.get_by_text("Approved · not dispatched", exact=True).wait_for()
                page.reload()
                approved.get_by_text("Approved · not dispatched", exact=True).wait_for()
                page.locator('[data-compute-action="cancel"]').click()
                page.get_by_text("Cancelled before dispatch", exact=True).wait_for()
                inventory = store.inventory(principal, session_hash=sessions._hash(cookie))
                assert inventory["capabilities"] == {
                    "transport": False,
                    "passive_discovery": False,
                    "scripts": False,
                }
                assert inventory["executions"][0]["state"] == "cancelled"
                assert inventory["scopes"][0]["revoked"] is True
                assert inventory["gateways"][0]["state"] == "pending_verification"
                assert inventory["nodes"][0]["state"] == "pending_verification"
                discovery = page.request.get(origin + "/api/compute/discoveries")
                assert discovery.status == 200
                assert discovery.json() == {
                    "discoveries": [],
                    "capabilities": {"passive_discovery": False},
                }
                assert page.locator('[data-compute-action="discovery"]').is_disabled()
                assert page.locator("#compute-discoveries").inner_text() == "Passive observations"
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                assert errors == []
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        supervisor.close()
        db.close()


def _enrollment(page, name, address, credential, pin):
    for field, value in {
        "name": name,
        "address": address,
        "account": "compute",
        "credential_id": credential,
        "credential_version": "1",
    }.items():
        page.locator(f'input[name="{field}"]').fill(value)
    page.locator('select[name="host_key_algorithm"]').select_option(pin["algorithm"])
    page.locator('textarea[name="host_key_key"]').fill(pin["key"])
    page.locator('input[name="pin_confirmed"]').check()


def test_compute_browser_immutable_script_inspection_and_approval(script_state, monkeypatch):
    """Signed synthetic runtime proves management wiring, never confinement."""
    from commonplace.compute.executor import ExecutionService

    playwright = pytest.importorskip("playwright.sync_api")
    state, env = script_state, script_state.env
    for name in ("CHAT", "HOLD"):
        monkeypatch.setenv(f"COMMONPLACE_{name}_WORKER", "off")
    monkeypatch.setenv("COMMONPLACE_RATE_LIMITS", "off")
    for name in ("COMMONPLACE_ALLOWED_HOSTS", "COMMONPLACE_APP_URL", "COMMONPLACE_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(ExecutionService, "start", lambda self: False)
    monkeypatch.setattr(
        ParamikoDriver, "connect_gateway", lambda *args: pytest.fail("No remote script dispatch")
    )
    supervisor = WorkerSupervisor(str(env.path.parent.parent.parent), launcher=object())
    gateway = Gateway(env.db, supervisor)
    server = gateway.build_server("127.0.0.1", 0)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    source = "# Exact café <script>inert</script>\nprint('synthetic')\n"
    try:
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                context.add_cookies([{"name": sessions.COOKIE, "value": env.cookie, "url": origin}])
                page = context.new_page()
                page.set_default_timeout(10000)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/app/#/compute")
                page.get_by_role("button", name="Add Python script", exact=True).click()
                page.locator('select[name="node_id"]').select_option(state.node["id"])
                page.locator('input[name="task_number"]').fill(str(env.task))
                page.locator('textarea[name="source"]').fill(source)
                page.locator('textarea[name="arguments"]').fill('["; literal $(id)"]')
                page.locator('textarea[name="output_paths"]').fill("result.txt")
                page.get_by_role("button", name="Save script bundle", exact=True).click()
                page.get_by_text(
                    "Immutable script saved. Inspect its source and manifest before proposing it for approval.",
                    exact=True,
                ).wait_for()
                page.get_by_role(
                    "button", name="Inspect source and manifest", exact=True
                ).first.click()
                assert page.get_by_label("Exact script source", exact=True).inner_text() == source
                page.get_by_role("button", name="Propose this script", exact=True).click()
                page.get_by_role("button", name="Create script proposal", exact=True).click()
                page.get_by_role("button", name="Inspect approval", exact=True).click()
                store = open_store(gateway, env.workspace)
                proposal = next(
                    row
                    for row in store.list_executions(env.principal, **env.auth)
                    if row["operation"] == "workspace.script.v1"
                )
                page.locator('input[name="expected_digest"]').fill(proposal["digest"])
                page.locator('input[name="expires_minutes"]').fill("1")
                page.get_by_role(
                    "button", name="Record approval without dispatch", exact=True
                ).click()
                approved = page.locator(".compute-row").filter(has_text=proposal["digest"])
                approved.get_by_text("Approved · not dispatched", exact=True).wait_for()
                page.reload()
                approved.get_by_text("Approved · not dispatched", exact=True).wait_for()
                scripts = store.list_scripts(env.principal, **env.auth)
                created = next(
                    row
                    for row in scripts
                    if store.get_script(env.principal, row["id"], **env.auth)["source"] == source
                )
                assert created["manifest"]["arguments"] == ("; literal $(id)",)
                assert proposal["script"]["manifest_digest"] == created["digest"]
                with store._journal() as conn:
                    assert (
                        conn.execute(
                            "SELECT count(*) FROM execution_runs WHERE execution_id=?",
                            (proposal["id"],),
                        ).fetchone()[0]
                        == 0
                    )
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                assert errors == []
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        supervisor.close()

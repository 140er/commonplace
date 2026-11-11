"""Real-browser owner flow: enroll, attest and edit the wings of a runner machine in Settings."""

import json

from commonplace import sessions
from tests._commonplace_helpers import hub_fixture as hub_fixture

WINGS = ["platform", "compute", "notes"]


def _remember(page, origin, headers):
    for wing in WINGS:
        response = page.request.post(
            origin + "/api/memory",
            data={"text": "Synthetic workspace example.", "theme": wing, "topic": "demo"},
            headers=headers,
        )
        assert response.status == 201, response.status


def test_owner_enrolls_attests_and_edits_wings_without_the_console(hub, chromium):
    from playwright.sync_api import expect

    link = sessions.create_login_link(hub.db, "owner@example.com", hub.workspace)
    session = sessions.redeem_login_link(hub.db, link)
    origin = f"http://127.0.0.1:{hub.port}"
    context = chromium.new_context(viewport={"width": 1280, "height": 1000})
    context.add_cookies([{"name": sessions.COOKIE, "value": session, "url": origin}])
    context.grant_permissions(["clipboard-read", "clipboard-write"], origin=origin)
    try:
        page = context.new_page()
        errors, logs, calls = [], [], []
        page.on("pageerror", lambda error: errors.append(type(error).__name__))
        page.on("console", lambda message: logs.append(message.text))

        def record(request):
            if "/api/runners" in request.url and request.method != "GET":
                calls.append((request.method, request.url.removeprefix(origin), request.post_data))

        page.on("request", record)
        _remember(page, origin, {"Origin": origin})

        page.goto(origin + "/app/#/settings", wait_until="domcontentloaded")
        section = page.locator(".runners-settings")
        expect(section.get_by_role("heading", name="Runner machines")).to_be_visible()

        # Enroll: the form posts the grant and the tokens are shown once.
        section.get_by_text("Add runner", exact=True).click()
        form = section.locator('[data-form="runner-enroll"]')
        form.get_by_label("Label").fill("example-machine")
        form.get_by_label("Projects").fill("demo, other")
        form.get_by_label("Topics").fill("worker")
        form.get_by_label("Writes to").select_option("platform")
        form.get_by_role("checkbox", name="compute").check()
        form.get_by_label("Max runtime mode").select_option("auto-accept-edits")
        form.get_by_label("Max in flight").fill("2")
        form.get_by_role("button", name="Enroll runner").click()
        secrets = section.locator("[data-runner-reveal]")
        expect(secrets.get_by_text("will not be shown again")).to_be_visible()
        machine_token = secrets.locator("[data-runner-token=machine]").input_value()
        worker_token = secrets.locator("[data-runner-token=worker]").input_value()
        assert machine_token and worker_token and machine_token != worker_token
        method, path, body = calls[0]
        assert (method, path) == ("POST", "/api/runners")
        assert json.loads(body) == {
            "label": "example-machine",
            "projects": ["demo", "other"],
            "topics": ["worker"],
            "wings": ["platform", "compute"],
            "max_runtime_mode": "auto-accept-edits",
            "max_in_flight": 2,
        }
        secrets.get_by_role("button", name="Copy worker token").click()
        assert page.evaluate("navigator.clipboard.readText()") == worker_token
        expect(section.locator("[data-runner]")).to_have_count(1)

        # Dismiss: the tokens leave the DOM, both storages, the URL and the console.
        secrets.get_by_role("button", name="Dismiss").click()
        expect(secrets).to_be_empty()
        for token in (machine_token, worker_token):
            assert token not in page.content()
            assert token not in page.url
            assert token not in page.evaluate("JSON.stringify([localStorage, sessionStorage])")
            assert not any(token in line for line in logs)
        page.reload(wait_until="domcontentloaded")  # a later render keeps no secret
        expect(section.locator("[data-runner]")).to_have_count(1)
        assert machine_token not in page.content() and worker_token not in page.content()

        # Attest: topics, provider and the machine's worker connection, prefilled.
        row = section.locator("[data-runner]").first
        machine_id = row.get_attribute("data-runner")
        row.get_by_text("Attest", exact=True).click()
        expect(row.get_by_label("Worker connection id")).not_to_be_empty()
        row.get_by_label("Provider instance id").fill("worker")
        row.get_by_role("button", name="Attest machine").click()
        expect(row.get_by_text("owner_attested")).to_be_visible()
        expect(row.get_by_text("not verified")).to_be_visible()
        method, path, body = calls[-1]
        assert (method, path) == ("POST", f"/api/runners/{machine_id}/attest")
        assert json.loads(body)["topics"] == ["worker"]

        # Wings edit: add a read wing, then see the reset the note promised.
        row.get_by_text("Read wings", exact=True).click()
        expect(
            row.locator("[data-runner-read]").get_by_text("resets the owner attestation")
        ).to_be_visible()
        row.locator("[data-runner-read]").get_by_role("checkbox", name="notes").check()
        row.get_by_role("button", name="Save read wings").click()
        expect(row.get_by_text("Reads: platform, compute, notes")).to_be_visible()
        method, path, body = calls[-1]
        assert (method, path) == ("PATCH", f"/api/runners/{machine_id}")
        assert json.loads(body) == {"wings": ["platform", "compute", "notes"]}
        rows = page.request.get(origin + "/api/runners").json()["machines"]
        saved = next(r for r in rows if r["id"] == machine_id)
        assert saved["wings"] == WINGS and saved["binding_state"] == "unbound"

        # Leaving the route without dismissing also drops the tokens.
        section.get_by_text("Add runner", exact=True).click()
        form = section.locator('[data-form="runner-enroll"]')
        form.get_by_label("Label").fill("second-machine")
        form.get_by_label("Projects").fill("demo")
        form.get_by_label("Topics").fill("worker")
        form.get_by_role("button", name="Enroll runner").click()
        token = secrets.locator("[data-runner-token=worker]").input_value()
        assert token
        page.evaluate("location.hash = '#/'")
        page.evaluate("location.hash = '#/settings'")
        expect(section.locator("[data-runner]")).to_have_count(2)
        assert token not in page.content() and not section.locator("[data-runner-token]").count()
        assert not errors
    finally:
        context.close()

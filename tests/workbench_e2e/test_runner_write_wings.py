"""Real-browser owner flow: Settings shows a runner worker's write set and widens it."""

import os
from pathlib import Path

from commonplace import sessions
from tests._commonplace_helpers import hub_fixture as hub_fixture

WINGS = ["platform", "compute", "notes"]


def test_owner_sees_and_widens_runner_write_wings(hub, chromium):
    from playwright.sync_api import expect

    link = sessions.create_login_link(hub.db, "owner@example.com", hub.workspace)
    session = sessions.redeem_login_link(hub.db, link)
    origin = f"http://127.0.0.1:{hub.port}"
    context = chromium.new_context(viewport={"width": 1280, "height": 1000})
    context.add_cookies([{"name": sessions.COOKIE, "value": session, "url": origin}])
    try:
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(type(error).__name__))
        headers = {"Origin": origin}
        # Wings exist once content lands in them; the grant then names three of them.
        for wing in WINGS:
            response = page.request.post(
                origin + "/api/memory",
                data={"text": "Synthetic workspace example.", "theme": wing, "topic": "demo"},
                headers=headers,
            )
            assert response.status == 201, response.status
        response = page.request.post(
            origin + "/api/runners",
            data={
                "label": "example-machine",
                "projects": ["demo"],
                "topics": ["worker"],
                "wings": WINGS,
            },
            headers=headers,
        )
        assert response.status == 201, response.status
        machine = response.json()["machine_id"]

        page.goto(origin + "/app/#/settings", wait_until="domcontentloaded")
        section = page.locator(".runners-settings")
        expect(section.get_by_role("heading", name="Runner machines")).to_be_visible()
        expect(section.get_by_text("Writes: platform", exact=True)).to_be_visible()
        section.get_by_text("Write access", exact=True).click()
        primary = section.get_by_role("checkbox", name="platform (first granted wing")
        expect(primary).to_be_checked()
        expect(primary).to_be_disabled()
        expect(section.get_by_role("checkbox", name="notes", exact=True)).not_to_be_checked()
        section.get_by_role("checkbox", name="compute", exact=True).check()
        section.get_by_role("button", name="Save write access").click()
        expect(section.get_by_text("Write access saved", exact=True)).to_be_visible()
        expect(section.get_by_text("Writes: platform, compute", exact=True)).to_be_visible()

        rows = page.request.get(origin + "/api/runners").json()["machines"]
        row = next(r for r in rows if r["id"] == machine)
        assert row["write_set"] == ["platform", "compute"]
        assert row["binding_state"] == "unbound"
        worker = hub.db.authenticate_connection(row["worker_connection_id"]).policy
        assert worker.write_wing == "platform" and worker.write_wings == ("compute",)
        assert not worker.can_write_wing("notes")

        page.reload(wait_until="domcontentloaded")
        expect(section.get_by_text("Writes: platform, compute", exact=True)).to_be_visible()
        if os.environ.get("CP_E2E_SHOTS"):
            target = Path(os.environ["CP_E2E_SHOTS"])
            target.mkdir(parents=True, exist_ok=True)
            section.get_by_text("Write access", exact=True).click()
            section.screenshot(path=str(target / "runner-write-wings.png"))
        assert not errors
    finally:
        context.close()

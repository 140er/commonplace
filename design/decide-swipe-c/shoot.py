"""Phone and desktop shots of the decide queue (swipe C), through a real seeded Hub.

Run from the repository root: uv run python design/decide-swipe-c/shoot.py [--output DIR]
Phones are 390x844 at 3x, the small check is 360x740, desktop is 1440x900.
"""

import argparse
import base64
import json
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from commonplace import sessions  # noqa: E402
from commonplace.boards.service import BoardService  # noqa: E402
from commonplace.extensions import ToolContext  # noqa: E402
from tests._commonplace_helpers import Hub  # noqa: E402
from tests.e2e_handoff.shoot import chromium  # noqa: E402
from tests.e2e_handoff.ui_server import Browser  # noqa: E402

DEFAULT_OUTPUT = Path(__file__).resolve().parent / "png"
PHONE = (390, 844, 3)
SMALL = (360, 740, 3)
DESKTOP = (1440, 900, 1)


class NoWorker:
    def start(self, workspace_id, workspace_dir):
        raise RuntimeError("no memory worker in the decide shots")


def seed(hub):
    person = sessions.person_principal(hub.db, hub.admin, hub.workspace)
    owner = BoardService(ToolContext(principal=person, gateway=hub.gateway))
    owner.board_add(slug="commonplace", wing="shared")
    agents = {}
    for name in ("design-agent", "search-agent", "ops-agent", "copy-agent", "ui-agent"):
        principal = hub.gateway.authenticate(f"Bearer {hub.owner_token(name)}")
        agents[name] = BoardService(ToolContext(principal=principal, gateway=hub.gateway))
    rows = [
        (
            "design-agent",
            "Voice needs-you digest",
            "Voice button next to Send. Which one?",
            ["Glyph only (advised)", "The word Speak", "Both"],
            60,
        ),
        (
            "search-agent",
            "Keyword hits in search",
            "Exact words match now. Finish stemming and ranking first, or park the card?",
            ["Finish it (advised)", "Park the card"],
            40,
        ),
        (
            "ops-agent",
            "Scrub old paths from history",
            "The scrub is done on develop. Also rewrite the old history on GitHub, or keep it on develop only?",
            ["Keep it on develop only", "Rewrite history"],
            12,
        ),
        (
            "copy-agent",
            "Pricing page copy",
            "What should the first line on the pricing page say?",
            [],
            5,
        ),
        (
            "ui-agent",
            "Live task rows with branch, PR and heartbeat",
            "Done? Rows show branch, PR and a live heartbeat dot. Screens are on the card.",
            ["Ship"],
            180,
        ),
    ]
    numbers = []
    for name, title, question, options, _minutes in rows:
        card = agents[name].task_add(title, board="commonplace")
        agents[name].ask(question, options=options or None, task=card["number"])
        numbers.append(card["number"])
    return owner, agents, numbers, rows


def backdate(data_dir, numbers, rows):
    # Ages in the meta line read like the renders; the order stays the seed order.
    path = next(Path(data_dir).rglob("boards.sqlite3"))
    now = time.time()
    with sqlite3.connect(path) as conn:
        for index, (number, row) in enumerate(zip(numbers, rows)):
            stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - row[4] * 60))
            created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 86400 + index * 60))
            conn.execute(
                "UPDATE tasks SET updated_at=?, created_at=? WHERE id=?", (stamp, created, number)
            )


class Phone:
    def __init__(self, browser, origin, output):
        self.b, self.origin, self.output = browser, origin, output

    def size(self, width, height, scale):
        self.b.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": width, "height": height, "deviceScaleFactor": scale, "mobile": width < 641},
        )
        self.b.call(
            "Emulation.setTouchEmulationEnabled", {"enabled": width < 641, "maxTouchPoints": 5}
        )

    def go(self, hash_):
        self.b.call("Page.navigate", {"url": f"{self.origin}/app/{hash_}"})
        time.sleep(0.4)
        self.b.wait("document.readyState === 'complete'")
        self.b.call("Page.reload", {"ignoreCache": True})
        time.sleep(0.4)
        self.b.wait("document.readyState === 'complete'")

    def ready(self, selector, timeout=10):
        self.b.wait(f"!!document.querySelector({json.dumps(selector)})", timeout=timeout)

    def click(self, selector):
        self.ready(selector)
        self.b.evaluate(f"document.querySelector({json.dumps(selector)}).click()")
        time.sleep(0.45)

    def type(self, selector, text):
        self.b.evaluate(
            f"(() => {{ const f = document.querySelector({json.dumps(selector)}); f.focus(); f.value = {json.dumps(text)}; }})()"
        )

    def shot(self, name):
        time.sleep(0.4)
        self.b.evaluate(
            "document.activeElement && document.activeElement.blur && document.activeElement.matches('h2, .decide-prop, .done-title') && document.activeElement.blur()"
        )
        data = self.b.call("Page.captureScreenshot", {"format": "png"})["data"]
        path = self.output / f"{name}.png"
        path.write_bytes(base64.b64decode(data))
        print(path)

    def drag(self, fraction):
        # A touch drag held mid-way, past the arm delay: the card, stamp, frame, button and track.
        time.sleep(0.4)
        self.b.evaluate(
            "(() => { const c = document.querySelector('.decide-card.now'); const r = c.getBoundingClientRect();"
            "const x = r.left + r.width / 2, y = r.top + r.height / 2;"
            "const ev = (type, dx) => c.dispatchEvent(new PointerEvent(type, { bubbles: true, pointerId: 7, pointerType: 'touch', isPrimary: true, clientX: x + dx, clientY: y }));"
            f"ev('pointerdown', 0); ev('pointermove', Math.sign({fraction}) * 14); ev('pointermove', r.width * {fraction}); }})()"
        )


def capture(output):
    binary = shutil.which("google-chrome")
    if not binary:
        raise RuntimeError("google-chrome is required for screenshots")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="decide-shots-") as temporary:
        directory = Path(temporary)
        hub = Hub(directory / "data", NoWorker())
        try:
            owner, agents, numbers, rows = seed(hub)
            backdate(directory / "data", numbers, rows)
            voice, keyword, scrub, pricing, live = numbers
            origin = f"http://127.0.0.1:{hub.port}"
            link = sessions.create_login_link(hub.db, "owner@example.com", hub.workspace)
            with chromium(binary, directory, directory / "profile") as tools:
                browser = Browser(tools, isolated=False)
                p = Phone(browser, origin, output)
                try:
                    p.size(*PHONE)
                    browser.call(
                        "Page.navigate", {"url": origin + "/app/login?" + urlencode({"t": link})}
                    )
                    browser.wait(f"location.href.startsWith({json.dumps(origin + '/app/')})")
                    browser.evaluate("localStorage.clear()")

                    p.go("#/")
                    p.ready(".needbox .answer")
                    p.shot("entry-home")
                    p.go("#/decide")
                    p.ready(".decide-prop")
                    p.shot("c-1-card")
                    p.go("#/decide")
                    p.ready(".decide-prop")
                    p.drag(0.42)
                    p.shot("c-2b-swipe-yes")
                    p.go("#/decide")
                    p.ready(".decide-prop")
                    p.drag(-0.42)
                    p.shot("c-2-swipe-no")
                    p.go("#/decide")
                    p.click('[data-action="queue-no"]')
                    p.shot("c-3-after-no")
                    p.go(f"#/decide/{pricing}")
                    p.ready(".decide-ask")
                    p.shot("c-7-no-options")
                    p.go(f"#/decide/{live}")
                    p.click('[data-action="queue-no"]')
                    p.shot("c-8-one-option-no")
                    # Undo window: a row sent, two seconds in, then undone so nothing goes out.
                    p.go("#/decide")
                    p.click('[data-action="queue-no"]')
                    p.click('[data-action="queue-answer"][data-option="2"]')
                    time.sleep(2.1)
                    p.shot("c-4-undo")
                    p.click('[data-action="queue-undo"]')
                    # Offline: sends paused, the question readable.
                    p.go("#/decide")
                    p.ready(".decide-prop")
                    browser.evaluate(
                        "Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => false }); dispatchEvent(new Event('offline'))"
                    )
                    p.ready(".decide-card[data-swipe='off']")
                    p.shot("offline")
                    # Failed: the server does not answer; the question comes back after the current one.
                    p.go("#/decide")
                    p.ready(".decide-prop")
                    browser.evaluate(
                        "(() => { const real = window.fetch; window.fetch = (url, opts) => String(url).includes('/decide') ? Promise.resolve(new Response('{}', { status: 502, headers: { 'Content-Type': 'application/json' } })) : real(url, opts); })()"
                    )
                    p.click('[data-action="queue-yes"]')
                    browser.wait("!!document.querySelector('.decide-status.bad')", timeout=10)
                    p.shot("failed")
                    # Desktop and the small phone, before anything is answered.
                    p.size(*DESKTOP)
                    p.go("#/decide")
                    p.ready(".decide-prop")
                    p.shot("desktop-card")
                    p.click('[data-action="queue-no"]')
                    p.shot("desktop-after-no")
                    p.size(*SMALL)
                    p.go("#/decide")
                    p.ready(".decide-prop")
                    p.shot("small-c-1-card")
                    p.click('[data-action="queue-no"]')
                    p.shot("small-c-3-after-no")
                    p.size(*PHONE)

                    # Waiting: ask the agent for new options, let the hold run out, then reach the card.
                    p.go(f"#/decide/{scrub}")
                    p.click('[data-action="queue-no"]')
                    p.click('[data-action="queue-ask-new"]')
                    time.sleep(5.6)
                    browser.wait(
                        f"JSON.parse(localStorage.getItem('cmmn.decide.asked') || '{{}}')[{scrub}] !== undefined",
                        timeout=10,
                    )
                    for _ in range(4):
                        p.click('[data-action="queue-later"]')
                    p.ready(".decide-card.waiting")
                    p.shot("c-5-waiting")
                    # New options: the agent asks again, and the card comes back marked new after a reload.
                    time.sleep(1.1)
                    agents["ops-agent"].ask(
                        "The scrub is done on develop. Also rewrite the old history on GitHub, or keep it on develop only?",
                        options=[
                            "Rewrite only the 3 tags",
                            "Rewrite all history",
                            "Keep develop only, archive the rest",
                        ],
                        task=scrub,
                    )
                    p.go(f"#/decide/{scrub}")
                    p.ready(".tag.new")
                    p.shot("c-6-new-tag")
                    # Answered elsewhere: the card is answered from another device while it is on screen.
                    p.go("#/decide")
                    p.ready(".decide-prop")
                    owner.decide(
                        voice,
                        choice="Both",
                        then="return",
                        version=owner.task_show(voice)["version"],
                    )
                    p.click('[data-action="queue-yes"]')
                    browser.evaluate("dispatchEvent(new Event('pagehide'))")
                    browser.wait("!!document.querySelector('.decide-card.stale')", timeout=10)
                    p.shot("stale")
                    # All done: answer the rest, park one for later.
                    p.click('[data-action="queue-next"]')
                    p.click('[data-action="queue-yes"]')
                    p.ready(".tag.new")
                    p.click('[data-action="queue-yes"]')
                    p.ready(".decide-field.solo")
                    p.type(".decide-field input", "Your memory, on your machine.")
                    p.click(".decide-field .send")
                    p.click('[data-action="queue-later"]')
                    browser.wait("!!document.querySelector('.decide-done')", timeout=10)
                    time.sleep(5.6)
                    p.shot("done")
                finally:
                    browser.close()
        finally:
            hub.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    capture(parser.parse_args().output.expanduser().resolve())

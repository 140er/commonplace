"""Capture the five seeded UI states using Chromium, not Lightpanda.

Run from the repository root: uv run python -m tests.e2e_handoff.shoot
The authenticated profile is acquired by submitting the real login form.
"""

import argparse
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from tests.e2e_handoff.ui_server import Browser, Devtools, stop_process, ui_server

DEFAULT_OUTPUT = Path.home() / "work/mempalace-notes/handoff-system/png"
_FLAGS = [
    "--headless=new",
    "--hide-scrollbars",
    "--window-size=1440,900",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-sync",
    "--password-store=basic",
    "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
]


@contextmanager
def chromium(binary, directory, profile):
    log_path = directory / "chromium.log"
    with log_path.open("w+") as log:
        process = subprocess.Popen(
            [
                binary,
                *_FLAGS,
                f"--user-data-dir={profile}",
                "--remote-debugging-port=0",
                "about:blank",
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        tools = None
        try:
            active = profile / "DevToolsActivePort"
            deadline = time.monotonic() + 15
            while not active.exists():
                if process.poll() is not None:
                    raise RuntimeError(f"Chromium exited: {log_path.read_text()}")
                if time.monotonic() >= deadline:
                    raise TimeoutError("Chromium startup timed out")
                time.sleep(0.02)
            port, path = active.read_text().splitlines()[:2]
            tools = Devtools(f"ws://127.0.0.1:{port}{path}")
            yield tools
            tools.call("Browser.close")
            process.wait(timeout=10)
        finally:
            if tools:
                tools.close()
            stop_process(process)


def capture(output=DEFAULT_OUTPUT):
    """Write deterministic settings/card/thread PNGs through the real Hub."""
    binary = shutil.which("google-chrome")
    if not binary:
        raise RuntimeError("google-chrome is required for screenshots")
    output = Path(output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="handoff-ui-") as temporary:
        directory = Path(temporary)
        profile = directory / "profile"
        with ui_server(directory / "data") as server:
            with chromium(binary, directory, profile) as tools:
                browser = Browser(tools, isolated=False)
                try:
                    browser.login(server, server.owner_token)
                    assert browser.evaluate("document.querySelector('h1').textContent") == "Memory"
                finally:
                    browser.close()
            states = [
                ("settings-off", "/app/settings/memory", False),
                ("settings-on", "/app/settings/memory", True),
                ("card-handed-off", "/app/cards/142", True),
                ("card-failed", "/app/cards/143", True),
                ("thread", server.old_path, True),
            ]
            for name, path, enabled in states:
                server.settings(enabled, task=enabled)
                destination = output / f"{name}.png"
                result = subprocess.run(
                    [
                        binary,
                        *_FLAGS,
                        f"--user-data-dir={profile}",
                        "--timeout=10000",
                        "--virtual-time-budget=1000",
                        f"--screenshot={destination}",
                        server.origin + path,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=25,
                )
                if result.returncode or not destination.is_file():
                    raise RuntimeError(f"Screenshot {name} failed: {result.stderr}")
                print(destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    capture(parser.parse_args().output)

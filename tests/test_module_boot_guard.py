"""The entry document remains neutral when executable modules cannot load."""

import base64
import hashlib
import re
import subprocess
from pathlib import Path

import pytest

from commonplace.web import STATIC
from tests.test_cold_start_routes import cold_hub as cold_hub
from tests.test_commonplace_public_surface import request


@pytest.mark.parametrize(
    "case",
    [
        "429",
        "500",
        "network",
        "watchdog",
        "pending",
        "recovery",
        "backoff",
        "storage-off",
        "optional-preload",
    ],
)
def test_failed_module_graph_retains_retry_state(case):
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("module_boot_guard.mjs")), case],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_document_before_identity_has_no_signin_form_and_guard_is_csp_pinned(cold_hub):
    hub, _cookie, _service = cold_hub
    status, headers, body = request(hub, "GET", "/app/")
    assert status == 200
    assert b"Loading Commonplace" in body and b"data-module-retry" in body
    assert b"Email a sign-in link" not in body and b"<h1>Sign in" not in body
    script = re.search(rb"<script data-boot-guard>(.*?)</script>", body, re.S)[1]
    assert script == (STATIC / "boot-guard.js").read_bytes()
    digest = base64.b64encode(hashlib.sha256(script).digest()).decode()
    assert f"'sha256-{digest}'" in headers["Content-Security-Policy"]
    assert "unsafe-inline" not in headers["Content-Security-Policy"]
    assert body.index(b"data-boot-guard") < body.index(b'src="/app/app.js"')

"""Frozen behavioral regressions for program delivery, also run on the pinned base.

The tiny payload tests the existing owner/code HTTP contract, not execution.
Actual package installation is covered separately with a real built artifact.
"""

# ruff: noqa: F811

import base64
import hashlib
import json
import platform
import sys
from http.client import HTTPConnection
from types import SimpleNamespace

import pytest

from tests.runner_harness import owners, signup_hub  # noqa: F401
from tests.test_compute_pairing import flow, machine_key  # noqa: F401


@pytest.fixture
def program_flow(flow):
    raw = b"Synthetic program delivery contract; never executed.\n"
    pin = {
        "version": 1,
        "purpose": "runner-program-delivery",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size": len(raw),
        "python": sys.implementation.cache_tag,
        "system": platform.system(),
        "machine": platform.machine(),
    }
    flow.hub.gateway.runner_program_release = SimpleNamespace(pin=pin, raw=raw)
    return flow, pin, raw


def test_owner_command_pins_program_without_requiring_a_preinstalled_runner(program_flow):
    flow, pin, _ = program_flow
    made = flow.made()
    encoded = made["setup"]["envelope"]
    envelope = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    assert envelope.get("program") == pin, "fresh-machine command has no pinned runner program"
    assert "sha256sum" in made["setup"]["command"], "launcher must be pinned before execution"


def test_live_pairing_code_delivers_the_exact_owner_pinned_program(program_flow):
    flow, pin, raw = program_flow
    made = flow.made()
    values = flow.envelope(made)
    request = {
        "workspace_id": values["workspace_id"],
        "pairing_id": values["pairing_id"],
        "code": made["setup"]["code"],
        "delivery": {"purpose": pin["purpose"], "sha256": pin["sha256"]},
    }
    host, port = flow.hub.server.server_address
    connection = HTTPConnection(host, port)
    try:
        connection.request(
            "POST",
            "/api/runner-pairing/check",
            json.dumps(request),
            {"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        received = response.read()
        assert response.status == 200, "a live owner pairing cannot deliver the runner program"
        assert response.getheader("Content-Type") == "application/octet-stream"
        assert received == raw
    finally:
        connection.close()
    assert flow.get(made)["state"] == "waiting", "program delivery must not spend enrollment"

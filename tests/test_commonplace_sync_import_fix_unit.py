"""Unauthorized import requests must be refused before reading their bodies."""

from types import SimpleNamespace

import pytest

from commonplace import imports
from commonplace.handoff.api import _RequestError
from commonplace.policy import Policy


def test_fix9_acl_refusal_reads_no_body(monkeypatch):
    read = []
    monkeypatch.setattr(
        imports,
        "_read_json",
        lambda handler, limit: read.append(limit) or {"source": "claude-code", "threads": []},
    )
    principal = SimpleNamespace(
        policy=Policy.build(scopes=[], read_wings=[], write_wing="inbox", trust="untrusted")
    )
    with pytest.raises(_RequestError) as error:
        imports._import_envelope(SimpleNamespace(), principal, SimpleNamespace())
    assert error.value.status == 403
    assert read == []

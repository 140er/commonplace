"""Gateway search keeps closet_preview for unrestricted owners and strips it for everyone else."""

import importlib.util
from types import SimpleNamespace

import pytest

from commonplace.handoff import mirror_access, provenance_refs

PREVIEW = "topic:SECRET-TOPIC|entities:Mallory|→drawer_ids:d1"


def _principal(role, *, sees_all=True, trust="trusted", person=True):
    policy = SimpleNamespace(
        trust=trust,
        sees_all_wings=sees_all,
        has_scope=lambda scope: False,
    )
    return SimpleNamespace(role=role, person=person, policy=policy, workspace_id="ws")


def _access():
    methods = {
        "__call__": lambda self, room: True,
        "parse": staticmethod(provenance_refs.refs),
        "reference": staticmethod(lambda _: False),
        "association": staticmethod(lambda *_: False),
        "import_allowed": staticmethod(lambda node: False),
        "ordinary_record": staticmethod(lambda node: True),
        "preview_allowed": staticmethod(lambda node: True),
        "recall": staticmethod(lambda: None),
    }
    access = type("Access", (), methods)()
    access.scrubbed = {}
    return access


def _search(module, principal):
    result = {
        "results": [
            {
                "drawer_id": "d1",
                "wing": "shared",
                "room": "r1",
                "text": "Exact stored words",
                "closet_preview": PREVIEW,
            }
        ]
    }
    return module.gate(None, principal, "search", result, access=_access())["results"][0]


@pytest.mark.parametrize(
    "principal",
    [
        _principal("member"),
        _principal("owner", person=False),
        _principal("owner", sees_all=False),
        _principal("owner", trust="untrusted"),
        _principal(None),
    ],
    ids=["member", "owner-api-token", "owner-scoped-token", "owner-untrusted", "no-role"],
)
def test_non_owner_search_strips_closet_preview(principal):
    hit = _search(mirror_access, principal)
    assert "closet_preview" not in hit
    assert hit["text"] == "Exact stored words"


def test_owner_search_keeps_closet_preview():
    assert _search(mirror_access, _principal("owner"))["closet_preview"] == PREVIEW


def test_mutation_reverting_owner_check_is_caught():
    """Dropping the owner exemption makes the owner test fail, so the test has teeth."""
    source = open(mirror_access.__file__, encoding="utf-8").read()
    mutated = source.replace(
        'key == "closet_preview" and not owner_reader', 'key == "closet_preview"'
    )
    assert mutated != source
    spec = importlib.util.spec_from_loader("commonplace.handoff.mirror_access_mut", loader=None)
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "commonplace.handoff"
    exec(compile(mutated, "mirror_access_mut", "exec"), module.__dict__)
    assert "closet_preview" not in _search(module, _principal("owner"))
    # The unmutated module still serves owners, which is the behaviour the mutant breaks.
    assert "closet_preview" in _search(mirror_access, _principal("owner"))

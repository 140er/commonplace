"""Plain legacy file citations require complete local authority."""

# ruff: noqa: F401, F811
import pytest

from tests.test_typed_association_reads import (
    case,
    fact,
    functional_worker_transport,
    hub_fixture,
    invoke,
    seed,
)

RAW = "archive/private notes.md"


def matches(c, rooms, raw=RAW):
    ids = [f"file-{i:04}" for i in range(len(rooms))]
    if ids:
        c.col.add(
            ids=ids,
            documents=["Exact stored words"] * len(ids),
            metadatas=[{"wing": "shared", "room": r, "source_file": raw} for r in rooms],
        )
        assert c.col.get(ids=ids)["metadatas"][0]["source_file"] == raw


@pytest.mark.parametrize("mode", ["unmatched", "hidden_match", "mixed_matches"])
def test_card253_s11_plain_file(case, mode):
    c = case
    rooms = {
        "unmatched": [],
        "hidden_match": [c.hidden.id],
        "mixed_matches": [c.primary.id, c.hidden.id],
    }[mode]
    matches(c, rooms)
    seed(c, "source_file", RAW)
    row = fact(c)
    assert "source_file" not in row["sources"][0], "s11 unproved plain file retained"


@pytest.mark.parametrize("role", ["member", "owner"])
@pytest.mark.parametrize("encoded", [False, True])
def test_card253_s11_readable(case, role, encoded):
    from urllib.parse import quote

    c = case
    matches(c, [c.primary.id, c.primary.id])
    value = quote(quote(RAW, safe=""), safe="") if encoded else RAW
    seed(c, "source_file", value)
    assert fact(c, role)["sources"][0]["source_file"] == value


def test_card253_s11_external_url(case):
    c = case
    c.col.add(
        ids=["url"],
        documents=["Exact words"],
        metadatas=[
            {
                "wing": "shared",
                "room": c.primary.id,
                "source_url": "https://example.test/notes",
            }
        ],
    )
    assert (
        invoke(c, "get_drawer", drawer_id="url")["metadata"]["source_url"]
        == "https://example.test/notes"
    )


@pytest.mark.parametrize(
    "fault",
    ["basename", "prefix", "roomless", "late_hidden", "changed", "revoked", "malformed", "timeout"],
)
def test_card253_s11_proof_guards(case, monkeypatch, fault):
    from commonplace.handoff.recall import Recall

    c = case
    raw = (
        RAW.rsplit("/", 1)[1] if fault == "basename" else RAW + ".bak" if fault == "prefix" else RAW
    )
    from tests.test_typed_association_reads import stored

    target_room = (
        stored(c, "capture:primary:codex:target").id if fault == "revoked" else c.primary.id
    )
    rooms = [""] if fault == "roomless" else [target_room]
    if fault == "late_hidden":
        rooms = [c.primary.id] * 500 + [c.hidden.id]
    matches(c, rooms, raw)
    seed(c, "source_file", RAW)
    original = c.h.gateway.mediator.workers.call_tool
    reached = []

    def call(p, name, args):
        if name == "read_associations" and args["kind"] == "source_file":
            reached.append(args)
            if fault == "timeout":
                raise TimeoutError("unavailable")
            if fault == "malformed":
                return {"associations": [{}]}
            if "expect" in args:
                if fault == "changed":
                    c.col.update(ids=["file-0000"], metadatas=[{"room": c.hidden.id}])
                if fault == "revoked":
                    old = Recall.can_read
                    monkeypatch.setattr(
                        Recall,
                        "can_read",
                        lambda self, p, room, **kw: (
                            False if room == target_room else old(self, p, room, **kw)
                        ),
                    )
        return original(p, name, args)

    monkeypatch.setattr(c.h.gateway.mediator.workers, "call_tool", call)
    row = invoke(c, "kg_query", entity="TypedFact")["facts"][0]
    assert row["object"] == "Exact  original words"
    assert "source_file" not in row["sources"][0]
    assert reached
    assert c.col.count() == len(rooms)

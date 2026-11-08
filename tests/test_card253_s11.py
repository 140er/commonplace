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


@pytest.mark.parametrize("role", ["member", "owner"])
@pytest.mark.parametrize("spelling", ["encoded", "padded"])
@pytest.mark.parametrize("surface", ["drawer", "citation"])
def test_card253_s11_literal_kept(case, role, spelling, surface):
    from pathlib import Path
    from urllib.parse import quote

    c = case
    value = quote(RAW, safe="") if spelling == "encoded" else " " + RAW + " "
    matches(c, [c.primary.id], value)
    if surface == "drawer":
        reply = invoke(c, "get_drawer", role, drawer_id="file-0000")
        assert reply["metadata"].get("source_file") == Path(value).name, (
            "literal drawer citation lost"
        )
    else:
        seed(c, "source_file", value)
        assert fact(c, role)["sources"][0].get("source_file") == value, "literal fact citation lost"


@pytest.mark.parametrize("role", ["member", "owner"])
@pytest.mark.parametrize("spelling", ["encoded", "padded"])
def test_card253_s11_hidden_raw_union(case, role, spelling):
    from urllib.parse import quote

    c = case
    value = quote(RAW, safe="") if spelling == "encoded" else RAW + " "
    matches(c, [c.primary.id])
    c.col.add(
        ids=["hidden-literal"],
        documents=["Exact stored words"],
        metadatas=[
            {
                "wing": "shared",
                "room": c.hidden.id,
                "source_file": value,
            }
        ],
    )
    assert c.col.get(ids=["hidden-literal"])["metadatas"][0]["source_file"] == value
    seed(c, "source_file", value)
    assert c.kg._conn().execute("SELECT source_file FROM fact_sources").fetchone()[0] == value
    assert "source_file" not in fact(c, role)["sources"][0], "hidden raw union citation retained"


@pytest.mark.parametrize("extra_wing", [False, True])
def test_card253_s11_literal_wing(case, extra_wing):
    from tests.test_typed_association_reads import SCOPES, TOOLS

    c = case
    uid = c.h.db.create_user("literal-reader@example.test")
    c.h.db.add_member(c.h.workspace, uid, "member", actor=c.h.admin)
    c.token = c.h.token(
        "literal",
        user=uid,
        tools=TOOLS,
        scopes=SCOPES,
        read_wings=["shared"],
        write_wings=["shared"],
        trust="trusted",
    )
    principal = c.h.db.authenticate_token(c.token)
    assert not principal.policy.sees_all_wings
    assert principal.policy.can_read_wing("shared")
    assert not principal.policy.can_read_wing("other")
    assert all(principal.policy.has_scope(scope) for scope in SCOPES)
    assert principal.policy.refusal("mempalace_kg_query") is None
    assert c.h.gateway.handoff.recall.can_read(principal, c.primary.id)
    assert not c.h.gateway.handoff.recall.can_read(principal, c.hidden.id)
    matches(c, [c.primary.id])
    if extra_wing:
        c.col.add(
            ids=["other-wing"],
            documents=["Exact stored words"],
            metadatas=[
                {
                    "wing": "other",
                    "room": c.primary.id,
                    "source_file": RAW,
                }
            ],
        )
    seed(c, "source_file", RAW)
    assert ("source_file" in fact(c)["sources"][0]) is not extra_wing


def test_card253_s11_non_chroma(case, monkeypatch):
    from mempalace import mcp_server as mcp

    c = case
    matches(c, [c.primary.id])
    seed(c, "source_file", RAW)
    assert fact(c)["sources"][0]["source_file"] == RAW
    original = mcp.read_associations
    reached = []

    def generic(kind, identifier, expect=None):
        reached.append(kind)
        with monkeypatch.context() as patch:
            patch.setattr(mcp, "_is_chroma_backend", lambda: False)
            return original(kind, identifier, expect)

    monkeypatch.setattr(mcp, "read_associations", generic)
    assert "source_file" not in fact(c)["sources"][0]
    assert reached == ["source_file"]


@pytest.mark.parametrize("count", [1, 10])
def test_card253_s11_response_memo(case, monkeypatch, count):
    from commonplace.handoff import mirror_access

    c = case
    matches(c, [c.primary.id])
    for i in range(count):
        c.kg.add_triple(
            "Repeated",
            "keeps",
            f"Exact words {i}",
            wing="shared",
            sensitivity="public",
            source_thread=c.primary.id,
            source_file=RAW,
        )
    c.calls.clear()
    original = mirror_access.provenance_refs.refs
    parsed = []

    def parse(value):
        parsed.append(value)
        return original(value)

    monkeypatch.setattr(mirror_access.provenance_refs, "refs", parse)
    for request in range(2):
        reply = invoke(c, "kg_query", entity="Repeated")
        assert len(reply["facts"]) == count
        assert all(row["sources"][0]["source_file"] == RAW for row in reply["facts"])
        calls = [args for _, name, args in c.calls if name == "read_associations"]
        assert len(calls) == (request + 1) * 2
        assert calls[-2]["identifier"] == (RAW, RAW)
        assert calls[-1]["identifier"] == (RAW, RAW)
        assert len(calls[-1]["expect"]) == 1  # identical spellings never duplicate the row
    assert parsed.count(RAW) == 2  # one parse and proof per request, not per fact


def test_card253_s11_no_selector(case, monkeypatch):
    from mempalace.backends import chroma_sqlite

    def unexpected(*args):
        raise AssertionError("missing selector opened storage")

    with monkeypatch.context() as patch:
        patch.setattr(chroma_sqlite, "_ready_snapshot", unexpected)
        assert (
            chroma_sqlite.sqlite_drawer_rows(case.config.palace_path, "mempalace_drawers") is None
        )


def test_card253_s11_union_revalidation(case, monkeypatch):
    from urllib.parse import quote

    c = case
    value = quote(RAW, safe="")
    matches(c, [c.primary.id])
    seed(c, "source_file", value)
    original = c.h.gateway.mediator.workers.call_tool
    reached = []

    def changed(principal, name, args):
        if name == "read_associations" and args["kind"] == "source_file":
            assert args["identifier"] == (value, RAW)
            reached.append(args)
            if "expect" in args:
                c.col.add(
                    ids=["late-literal"],
                    documents=["Exact stored words"],
                    metadatas=[
                        {
                            "wing": "shared",
                            "room": c.hidden.id,
                            "source_file": value,
                        }
                    ],
                )
        return original(principal, name, args)

    monkeypatch.setattr(c.h.gateway.mediator.workers, "call_tool", changed)
    assert "source_file" not in fact(c)["sources"][0]
    assert len(reached) == 2 and "expect" in reached[1]

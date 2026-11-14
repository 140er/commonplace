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
    assert (row["subject"], row["predicate"], row["object"]) == (
        "TypedFact",
        "keeps",
        "Exact  original words",
    )


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
    # Private read is owner-only, so a real member literal-wing reader never holds it.
    scopes = [scope for scope in SCOPES if scope != "private:read"]
    uid = c.h.db.create_user("literal-reader@example.test")
    c.h.db.add_member(c.h.workspace, uid, "member", actor=c.h.admin)
    c.token = c.h.token(
        "literal",
        user=uid,
        tools=TOOLS,
        scopes=scopes,
        read_wings=["shared"],
        write_wings=["shared"],
        trust="trusted",
    )
    principal = c.h.db.authenticate_token(c.token)
    assert principal.role == "member" and not principal.policy.has_scope("private:read")
    assert not principal.policy.sees_all_wings
    assert principal.policy.can_read_wing("shared")
    assert not principal.policy.can_read_wing("other")
    assert all(principal.policy.has_scope(scope) for scope in scopes)
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
    reads_before = len(c.reads)
    row = fact(c)
    assert len(c.reads) > reads_before, "kg_query did not reach the thread reader"
    assert ("source_file" in row["sources"][0]) is not extra_wing


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


def test_card253_s11_no_selector(tmp_path, monkeypatch):
    from mempalace.backends import chroma_sqlite

    def unexpected(*args):
        raise AssertionError("missing selector opened storage")

    with monkeypatch.context() as patch:
        patch.setattr(chroma_sqlite, "_ready_snapshot", unexpected)
        assert chroma_sqlite.sqlite_drawer_rows(str(tmp_path), "mempalace_drawers") is None


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


@pytest.mark.parametrize("role", ["member", "owner"])
@pytest.mark.parametrize("value", ["   ", "%20%20"])
def test_card253_s11_empty_identity(case, role, value):
    c = case
    matches(c, [c.primary.id], raw="")
    seed(c, "source_file", value)
    c.calls.clear()
    control = fact(c, role)
    c.col.add(
        ids=["hidden-empty"],
        documents=["Exact stored words"],
        metadatas=[{"wing": "shared", "room": c.hidden.id, "source_file": ""}],
    )
    assert c.col.get(ids=["hidden-empty"])["metadatas"][0]["source_file"] == ""
    hidden = fact(c, role)
    assert all("source_file" not in row["sources"][0] for row in (control, hidden)), (
        "empty decoded identity proved citation"
    )
    assert hidden == control, "empty source rows changed public output"
    assert not any(name == "read_associations" for _, name, _ in c.calls)


@pytest.mark.parametrize("identifier", [("   ", ""), ["%20%20", ""]])
def test_card253_s11_worker_empty_selector(monkeypatch, identifier):
    from mempalace import mcp_server as mcp

    def unexpected():
        raise AssertionError("empty decoded selector reached backend")

    monkeypatch.setattr(mcp, "_is_chroma_backend", unexpected)
    assert mcp.read_associations("source_file", identifier) == {"associations": None}


@pytest.fixture
def file_search(case, monkeypatch):
    from tests.test_typed_association_reads import SCOPES, TOOLS

    c = case
    tools = [*TOOLS, "mempalace_search"]
    c.token = c.h.token(
        "file-search",
        user=c.member.user_id,
        tools=tools,
        scopes=SCOPES,
        read_wings=["*"],
        write_wings=["shared"],
        trust="trusted",
    )
    c.search_posts = []
    original = c.h.gateway.mediator.workers.post

    def post(principal, request):
        c.search_posts.append(request)
        return original(principal, request)

    monkeypatch.setattr(c.h.gateway.mediator.workers, "post", post)
    member = c.h.db.authenticate_token(c.token)
    for principal in (member, c.owner):
        assert principal.policy.trust == "trusted" and principal.policy.sees_all_wings
        assert all(principal.policy.has_scope(scope) for scope in SCOPES)
        assert principal.policy.refusal("mempalace_search") is None
        assert c.h.gateway.handoff.recall.can_read(principal, c.primary.id)
        assert not c.h.gateway.handoff.recall.can_read(principal, c.hidden.id)
    return c


@pytest.mark.parametrize("role", ["member", "owner"])
@pytest.mark.parametrize("mode", ["readable", "mixed"])
def test_card253_s11_search_raw_proof(file_search, role, mode):
    import json
    from pathlib import Path

    c = file_search
    matches(c, [c.primary.id] if mode == "readable" else [c.primary.id, c.hidden.id])
    before = len(c.reads)
    reply = invoke(c, "search", role, query="Exact stored words")
    hits = reply["results"]
    assert hits, reply
    assert len(c.reads) > before
    assert any(r.get("params", {}).get("name") == "mempalace_search" for r in c.search_posts)
    if mode == "mixed":
        output = json.dumps(hits)
        assert RAW not in output and Path(RAW).name not in output, "search retained unproved path"
    else:
        assert all(
            h.get("source_path") == RAW and h.get("source_file") == Path(RAW).name for h in hits
        ), "search lost readable directory citation"


@pytest.mark.parametrize("field", ["source_drawer_id", "source_closet"])
def test_card253_s11_blank_typed_selector(case, field):
    c = case
    seed(c, field, "   ")
    c.calls.clear()
    row = fact(c)
    assert field not in row
    assert all(field not in source for source in row["sources"])
    assert not any(name == "read_associations" for _, name, _ in c.calls)


def test_card253_s11_worker_whitespace_selector(monkeypatch):
    from mempalace import mcp_server as mcp

    def unexpected():
        raise AssertionError("whitespace decoded selector reached backend")

    monkeypatch.setattr(mcp, "_is_chroma_backend", unexpected)
    assert mcp.read_associations("source_file", ("   ", "   ")) == {"associations": None}


@pytest.mark.parametrize("role", ["member", "owner"])
@pytest.mark.parametrize("mode", ["readable", "mixed"])
def test_card253_s11_media_raw_proof(file_search, monkeypatch, role, mode):
    import json
    from pathlib import Path
    from types import SimpleNamespace

    from mempalace.palace import ASSETS_COLLECTION_NAME, get_collection
    from mempalace.searcher import media_search

    c = file_search
    matches(c, [c.primary.id] if mode == "readable" else [c.primary.id, c.hidden.id])
    assets = get_collection(c.config.palace_path, collection_name=ASSETS_COLLECTION_NAME)
    assets.add(
        ids=["asset"],
        documents=["Exact stored words"],
        metadatas=[
            {
                "wing": "shared",
                "room": c.primary.id,
                "source_path": RAW,
                "source_file": RAW,
                "media_type": "image",
                "mime_type": "image/png",
                "title": Path(RAW).name,
                "related_drawer_id": "file-0000",
            }
        ],
    )
    # Exercise the real media retrieval with the suite's local deterministic
    # embedding provider; only opt in to the model-family routing check.
    monkeypatch.setattr(
        media_search,
        "MempalaceConfig",
        lambda: SimpleNamespace(
            embedding_model="embeddinggemma2",
            collection_name=c.config.collection_name,
        ),
    )
    original = media_search.search_with_media
    raw = []

    def media(*args, **kwargs):
        result = original(*args, **kwargs)
        raw.append(result)
        return result

    monkeypatch.setattr(media_search, "search_with_media", media)
    reply = invoke(c, "search", role, query="Exact stored words", include_media=True)
    assert raw and not raw[0].get("error"), raw
    assert any(
        h.get("source_path") == RAW and h.get("result_type") == "asset" for h in raw[0]["results"]
    )
    hits = reply["results"]
    assert any(h.get("result_type") == "asset" for h in hits), reply
    if mode == "mixed":
        output = json.dumps(hits)
        assert RAW not in output and Path(RAW).name not in output
    else:
        assert all(
            h.get("source_path") == RAW and h.get("source_file") == Path(RAW).name for h in hits
        )


def test_card253_s11_import_alias_scope():
    from types import SimpleNamespace

    from commonplace.handoff import mirror_access, provenance_refs

    access = SimpleNamespace(
        scrubbed={},
        parse=provenance_refs.refs,
        reference=lambda _: False,
        association=lambda *_: False,
    )
    original = {"source_file": RAW, "source_thread": "thr_deadbeef"}
    cleaned = mirror_access.scrub_provenance(original, access, import_file_alias=True)
    assert cleaned == {"source_file": RAW}
    assert original == {"source_file": RAW, "source_thread": "thr_deadbeef"}
    assert mirror_access.scrub_provenance(dict(original), access) == {}


@pytest.mark.parametrize("role", ["member", "owner"])
@pytest.mark.parametrize("mode", ["closet_only", "closet_mixed"])
def test_card253_s11_closet_preview(file_search, monkeypatch, role, mode):
    import json

    from mempalace import mcp_server as mcp
    from mempalace.palace import get_closets_collection

    c = file_search
    matches(c, [c.primary.id] if mode == "closet_only" else [c.primary.id, c.hidden.id])
    secret = "SECRET-TOPIC-ZQ"
    closets = get_closets_collection(c.config.palace_path)
    closets.add(
        ids=["hidden-closet"],
        documents=[f"topic:{secret}|entities:Mallory|→drawer_ids:file-0001 Exact stored words"],
        metadatas=[{"wing": "shared", "room": c.hidden.id, "source_file": RAW}],
    )
    assert closets.get(ids=["hidden-closet"])["metadatas"][0]["room"] == c.hidden.id
    original = mcp.search_memories
    raw = []

    def search(*args, **kwargs):
        result = original(*args, **kwargs)
        raw.append(result)
        return result

    monkeypatch.setattr(mcp, "search_memories", search)
    reply = invoke(c, "search", role, query=f"Exact stored words {secret}")
    assert raw and any(secret in h.get("closet_preview", "") for h in raw[0]["results"])
    hits = reply["results"]
    assert hits, reply
    assert secret not in json.dumps(hits), "gateway search exposed hidden closet preview"
    assert all("closet_preview" not in hit for hit in hits)


@pytest.mark.parametrize("tool", ["get_drawer", "get_drawers", "list_drawers"])
def test_card253_s11_import_reader_raw_proof(case, tool):
    from pathlib import Path

    from tests.test_typed_association_reads import SCOPES

    c = case
    matches(c, [c.primary.id, c.primary.id])
    args = {
        "get_drawer": {"drawer_id": "file-0000"},
        "get_drawers": {"drawer_ids": ["file-0000", "file-0001"]},
        "list_drawers": {"wing": "shared", "limit": 10},
    }[tool]
    replies = []
    for import_read in (False, True):
        c.token = c.h.token(
            f"literal-file-reader-{import_read}",
            user=c.member.user_id,
            tools=["mempalace_" + tool],
            scopes=[*SCOPES, *(["memory:import:read"] if import_read else [])],
            read_wings=["shared"],
            write_wings=["shared"],
            trust="trusted",
        )
        principal = c.h.db.authenticate_token(c.token)
        assert not principal.policy.sees_all_wings
        assert principal.policy.has_scope("memory:import:read") is import_read
        assert principal.policy.refusal("mempalace_" + tool) is None
        assert c.h.gateway.handoff.recall.can_read(principal, c.primary.id)
        assert not c.h.gateway.handoff.recall.can_read(principal, c.hidden.id)
        c.calls.clear()
        replies.append(invoke(c, tool, **args))
    assert replies[1] == replies[0], "import reader lost proven file citation"
    rows = replies[1].get("results", replies[1].get("drawers", [replies[1]]))
    assert rows and all(row["metadata"].get("source_file") == Path(RAW).name for row in rows)
    proofs = [args for _, name, args in c.calls if name == "read_associations"]
    assert len(proofs) == 2 and all(args["identifier"] == (RAW, RAW) for args in proofs)

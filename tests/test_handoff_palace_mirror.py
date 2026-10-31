"""Thread mirroring through the internal worker client, including a real worker."""

from dataclasses import replace

import pytest

from commonplace.gateway import WorkerUnavailable
from commonplace.handoff.models import MIRROR_WING, StoredThread
from commonplace.handoff.palace_mirror import MirrorError, mirror, palace_search
from commonplace.handoff.thread_store import Chunk
from mempalace.ids import make_drawer_id_from_content
from tests._commonplace_helpers import hub_fixture  # noqa: F401


@pytest.fixture
def stored():
    return StoredThread("thr_first", "workspace", "source", 3, 2, "digest")


@pytest.fixture
def chunks():
    return [
        Chunk(0, 0, 1, "[0] user message: café 🍵\r\n\n[1] assistant message:  keep spaces  \n"),
        Chunk(1, 2, 2, '[2] tool tool_result kettle: {"temperature": 85}\n'),
    ]


def _hit(stored_id="thr_first", index=0, text="[0] user message: exact text", **extra):
    return {
        "wing": MIRROR_WING,
        "room": stored_id,
        "source_file": f"handoff:{stored_id}#{index}",
        "text": text,
        "similarity": 0.5,
        **extra,
    }


class FakeWorker:
    def __init__(self):
        self.calls = []
        self.entries = {}
        self.search_results = {}

    def __call__(self, principal, name, arguments):
        self.calls.append((principal, name, arguments.copy()))
        if name == "mempalace_add_drawer":
            entry_id = make_drawer_id_from_content(
                arguments["wing"], arguments["room"], arguments["content"]
            )
            exists = entry_id in self.entries
            self.entries.setdefault(
                entry_id,
                {
                    "drawer_id": entry_id,
                    "wing": arguments["wing"],
                    "room": arguments["room"],
                    "content": arguments["content"],
                    "metadata": {
                        "source_file": arguments["source_file"],
                        "claimed_by": arguments["added_by"],
                    },
                },
            )
            return {
                "success": True,
                "drawer_id": entry_id,
                "reason": "already_exists" if exists else "added",
            }
        if name == "mempalace_search":
            return {"results": self.search_results.get(arguments["room"], [])[: arguments["limit"]]}
        if name == "mempalace_get_drawer":
            return self.entries[arguments["drawer_id"]]
        raise AssertionError(name)


def test_mirror_preserves_chunks_identity_origin_and_order(stored, chunks):
    worker = FakeWorker()
    principal = object()
    ids = mirror(worker, principal, stored, chunks)
    assert len(set(ids)) == 2
    assert list(worker.entries) == ids
    for chunk, entry_id, call in zip(chunks, ids, worker.calls):
        assert call == (
            principal,
            "mempalace_add_drawer",
            {
                "wing": MIRROR_WING,
                "room": stored.id,
                "content": chunk.text,
                "source_file": f"handoff:{stored.id}#{chunk.index}",
                "added_by": f"handoff:{chunk.seq_start}-{chunk.seq_end}:{len(chunk.text)}",
            },
        )
        assert worker.entries[entry_id]["content"] == chunk.text
    assert mirror(worker, principal, stored, chunks) == ids
    assert len(worker.entries) == 2


def test_retry_after_partial_write_adds_no_duplicates(stored, chunks):
    worker = FakeWorker()
    principal = object()

    def interrupted(actor, name, arguments):
        if arguments["source_file"].endswith("#1"):
            raise WorkerUnavailable()
        return worker(actor, name, arguments)

    with pytest.raises(MirrorError):
        mirror(interrupted, principal, stored, chunks)
    assert len(worker.entries) == 1
    assert len(mirror(worker, principal, stored, chunks)) == 2
    assert len(worker.entries) == 2


def test_empty_work_makes_no_worker_calls(stored):
    worker = FakeWorker()
    assert mirror(worker, object(), stored, []) == []
    assert palace_search(worker, object(), [], "needle") == []
    assert worker.calls == []


def test_text_the_worker_would_change_is_rejected(stored):
    worker = FakeWorker()
    with pytest.raises(MirrorError, match="verbatim"):
        mirror(worker, object(), stored, [Chunk(0, 0, 0, "[0] user message: lone \ud800")])
    assert worker.calls == []


def test_identical_chunks_reuse_a_drawer_and_keep_the_input_mapping(stored):
    worker = FakeWorker()
    principal = object()
    chunks = [
        Chunk(0, 1, 1, "[1] assistant message: repeated piece"),
        Chunk(1, 1, 1, "[1] assistant message: distinct piece"),
        Chunk(2, 1, 1, "[1] assistant message: repeated piece"),
    ]
    ids = mirror(worker, principal, stored, chunks)
    assert len(ids) == 3
    assert ids[0] == ids[2]
    assert ids[0] != ids[1]
    assert len(worker.entries) == 2
    assert len(worker.calls) == 2
    assert worker.entries[ids[0]]["metadata"]["source_file"] == f"handoff:{stored.id}#0"
    assert mirror(worker, principal, stored, chunks) == ids
    assert len(worker.entries) == 2


@pytest.mark.parametrize("limit", [0, -1])
def test_nonpositive_limit_makes_no_worker_calls(limit):
    worker = FakeWorker()
    assert palace_search(worker, object(), ["thr_first"], "needle", limit) == []
    assert worker.calls == []


@pytest.mark.parametrize("operation", ["mirror", "search"])
@pytest.mark.parametrize(
    "result",
    [None, [], "bad", {}, {"error": "offline"}, {"success": False}, {"isError": True}],
)
def test_bad_worker_results_raise_mirror_error(operation, result, stored, chunks):
    def call_tool(*_args):
        return result

    with pytest.raises(MirrorError):
        if operation == "mirror":
            mirror(call_tool, object(), stored, chunks)
        else:
            palace_search(call_tool, object(), [stored.id], "needle")


@pytest.mark.parametrize("operation", ["mirror", "search"])
@pytest.mark.parametrize("error", [WorkerUnavailable(), OSError("offline"), ValueError("bad JSON")])
def test_worker_exceptions_raise_mirror_error(operation, error, stored, chunks):
    def call_tool(*_args):
        raise error

    with pytest.raises(MirrorError) as raised:
        if operation == "mirror":
            mirror(call_tool, object(), stored, chunks)
        else:
            palace_search(call_tool, object(), [stored.id], "needle")
    assert raised.value.__cause__ is error


@pytest.mark.parametrize("entry_id", [None, "", 7])
def test_mirror_requires_an_entry_id(entry_id, stored, chunks):
    with pytest.raises(MirrorError, match="entry id"):
        mirror(lambda *_: {"success": True, "drawer_id": entry_id}, object(), stored, chunks)


def test_search_scopes_each_thread_and_globally_orders_and_limits():
    worker = FakeWorker()
    principal = object()
    worker.search_results = {
        "thr_first": [
            _hit(index=4, similarity=0.2),
            _hit(index=2, similarity=0.8, text="[8] user message: first\n[11] tool other: last\n"),
        ],
        "thr_other": [_hit("thr_other", 6, similarity=0.9)],
    }
    hits = palace_search(
        worker, principal, ["thr_first", "thr_other", "thr_first"], "raw?\nquery", 2
    )
    assert [(hit.stored_thread_id, hit.chunk_index, hit.score) for hit in hits] == [
        ("thr_other", 6, 0.9),
        ("thr_first", 2, 0.8),
    ]
    assert (hits[1].seq_start, hits[1].seq_end) == (8, 11)
    assert hits[1].text == worker.search_results["thr_first"][1]["text"]
    assert worker.calls == [
        (
            principal,
            "mempalace_search",
            {"query": "raw?\nquery", "wing": MIRROR_WING, "room": room, "limit": 8},
        )
        for room in ("thr_first", "thr_other")
    ]


@pytest.mark.parametrize(
    "extra",
    [
        {"wing": "shared"},
        {"room": "thr_hidden"},
        {"wing": None},
        {"room": None},
        {"source_file": None},
        {"source_file": 7},
        {"source_file": "handoff:thr_hidden#0"},
        {"source_file": "handoff:thr_first#-1"},
        {"source_file": "handoff:thr_first#0suffix"},
        {"source_file": "handoff:thr_first#0\n"},
        {"source_path": "handoff:thr_hidden#0"},
    ],
)
def test_unrelated_and_out_of_scope_hits_are_dropped(extra):
    worker = FakeWorker()
    worker.search_results["thr_first"] = [_hit(**extra)]
    assert palace_search(worker, object(), ["thr_first"], "needle") == []


def test_thread_id_is_literal_and_full_source_path_is_used():
    worker = FakeWorker()
    worker.search_results["thr.[first]"] = [
        _hit("thr.[first]", source_path="handoff:thr.[first]#7", source_file="display"),
        _hit("thr.[first]", source_path="handoff:thrXf#0"),
    ]
    hits = palace_search(worker, object(), ["thr.[first]"], "needle")
    assert [hit.chunk_index for hit in hits] == [7]


def test_duplicates_keep_the_best_score_and_ties_keep_worker_order():
    worker = FakeWorker()
    worker.search_results["thr_first"] = [
        _hit(index=7, similarity=0.9),
        _hit(index=3, similarity=0.2),
        _hit(index=3, similarity=0.9),
        _hit(index=3, similarity=0.3),
    ]
    hits = palace_search(worker, object(), ["thr_first"], "needle")
    assert [(hit.chunk_index, hit.score) for hit in hits] == [(7, 0.9), (3, 0.9)]


def test_cross_thread_recall_preserves_hybrid_score_before_limit():
    worker = FakeWorker()
    worker.search_results["thr_first"] = [_hit(similarity=0.5, score=0.3)]
    worker.search_results["thr_second"] = [_hit("thr_second", similarity=None, score=0.4)]
    hits = palace_search(worker, object(), ["thr_first", "thr_second"], "needle", limit=1)
    assert [hit.stored_thread_id for hit in hits] == ["thr_second"]


def test_cross_thread_recall_normalizes_bm25_over_the_merged_pool():
    from mempalace.searcher import _hybrid_rank

    query = "ZXQ739251 port"
    worker = FakeWorker()
    worker.search_results["thr_first"] = _hybrid_rank(
        [_hit(text="[0] user message: port", distance=0.8, similarity=0.2)], query
    )
    worker.search_results["thr_second"] = _hybrid_rank(
        [_hit("thr_second", text=f"[0] user message: {query}", distance=None, similarity=None)],
        query,
    )
    assert (
        worker.search_results["thr_first"][0]["score"]
        > worker.search_results["thr_second"][0]["score"]
    )
    hits = palace_search(worker, object(), ["thr_first", "thr_second"], query, limit=1)
    assert [hit.stored_thread_id for hit in hits] == ["thr_second"]


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ({"similarity": None, "score": 0.7}, 0.7),
        ({"similarity": 0.9, "score": 0.3}, 0.3),
        ({"similarity": None, "distance": 0.2}, -0.2),
        ({"similarity": None}, 0.0),
        ({"similarity": float("nan"), "distance": 0.4}, -0.4),
        ({"similarity": float("inf")}, 0.0),
        ({"similarity": True}, 0.0),
    ],
)
def test_missing_or_nonfinite_scores_are_safe(extra, expected):
    worker = FakeWorker()
    worker.search_results["thr_first"] = [_hit(**extra)]
    hits = palace_search(worker, object(), ["thr_first"], "needle")
    assert hits[0].score == expected


@pytest.mark.parametrize("results", [None, {}, "bad", [None], ["bad"]])
def test_malformed_search_results_raise_mirror_error(results):
    with pytest.raises(MirrorError):
        palace_search(lambda *_: {"results": results}, object(), ["thr_first"], "needle")


@pytest.mark.parametrize(
    "extra",
    [
        {"text": None},
        {"text": 5},
        {"text": "no sequence header"},
        {"claimed_by": "handoff:7-1:10"},
    ],
)
def test_unusable_mirrored_chunks_raise_mirror_error(extra):
    worker = FakeWorker()
    worker.search_results["thr_first"] = [_hit(**extra)]
    with pytest.raises(MirrorError):
        palace_search(worker, object(), ["thr_first"], "needle")


@pytest.mark.parametrize("label", [None, 5, "other", "handoff:bad"])
def test_legacy_labels_use_sequence_prefixes(label):
    worker = FakeWorker()
    worker.search_results["thr_first"] = [_hit(claimed_by=label)]
    hit = palace_search(worker, object(), ["thr_first"], "needle")[0]
    assert (hit.seq_start, hit.seq_end) == (0, 0)


def test_range_metadata_handles_continuations_and_sequence_like_user_text(stored):
    worker = FakeWorker()
    text = "continuation without a header\n[900] user message: quoted example\r\n"
    chunk = Chunk(3, 6, 6, text)
    entry_id = mirror(worker, object(), stored, [chunk])[0]
    worker.search_results[stored.id] = [
        _hit(index=3, text=text, claimed_by=worker.entries[entry_id]["metadata"]["claimed_by"])
    ]
    hit = palace_search(worker, object(), [stored.id], "continuation")[0]
    assert (hit.seq_start, hit.seq_end) == (6, 6)
    assert hit.text == text


def _split_worker(stored):
    worker = FakeWorker()
    text = "[5] assistant message: full verbatim text\r\n  "
    entry_id = mirror(worker, object(), stored, [Chunk(0, 5, 5, text)])[0]
    worker.search_results[stored.id] = [
        _hit(text=text[10:20], drawer_id=entry_id, claimed_by=f"handoff:5-5:{len(text)}"),
        _hit(
            text=text[20:],
            drawer_id=entry_id,
            claimed_by=f"handoff:5-5:{len(text)}",
            similarity=0.8,
        ),
    ]
    return worker, entry_id, text


def test_worker_splits_are_hydrated_once_to_the_complete_chunk(stored):
    worker, entry_id, text = _split_worker(stored)
    principal = object()
    hits = palace_search(worker, principal, [stored.id], "needle")
    assert len(hits) == 1
    assert hits[0].text == text
    assert hits[0].score == 0.8
    assert (hits[0].seq_start, hits[0].seq_end) == (5, 5)
    assert worker.calls[-1] == (principal, "mempalace_get_drawer", {"drawer_id": entry_id})
    assert [name for _, name, _ in worker.calls].count("mempalace_get_drawer") == 1


def test_overfetch_finds_distinct_chunks_after_physical_duplicates(stored):
    worker, _, text = _split_worker(stored)
    worker.search_results[stored.id].extend(
        [_hit(index=1, similarity=0.7), _hit(index=2, similarity=0.6)]
    )
    hits = palace_search(worker, object(), [stored.id], "needle", limit=2)
    assert [(hit.chunk_index, hit.score) for hit in hits] == [(0, 0.8), (1, 0.7)]
    assert hits[0].text == text
    searches = [arguments for _, name, arguments in worker.calls if name == "mempalace_search"]
    assert len(searches) == 1
    assert searches[0]["limit"] == 8


@pytest.mark.parametrize(("limit", "fetch_limit"), [(1, 4), (8, 32), (12, 48), (13, 50), (100, 50)])
def test_search_overfetch_is_bounded(limit, fetch_limit):
    worker = FakeWorker()
    assert palace_search(worker, object(), ["thr_first"], "needle", limit=limit) == []
    assert worker.calls[0][2]["limit"] == fetch_limit


@pytest.mark.parametrize("entry_id", [None, "", 7])
def test_worker_splits_need_an_entry_id(stored, entry_id):
    worker, _, _ = _split_worker(stored)
    worker.search_results[stored.id][0]["drawer_id"] = entry_id
    with pytest.raises(MirrorError, match="no id"):
        palace_search(worker, object(), [stored.id], "needle")


@pytest.mark.parametrize(
    "change",
    [
        {"metadata": None},
        {"wing": "hidden"},
        {"room": "thr_hidden"},
        {"metadata": {"source_file": "handoff:thr_hidden#0"}},
        {"content": None},
        {"content": "incomplete"},
        {"error": "unavailable"},
    ],
)
def test_bad_or_out_of_scope_hydration_raises_mirror_error(stored, change):
    worker, entry_id, _ = _split_worker(stored)
    worker.entries[entry_id].update(change)
    with pytest.raises(MirrorError):
        palace_search(worker, object(), [stored.id], "needle")


def test_hydration_worker_unavailability_raises_mirror_error(stored):
    worker, _, _ = _split_worker(stored)

    def call_tool(principal, name, arguments):
        if name == "mempalace_get_drawer":
            raise WorkerUnavailable()
        return worker(principal, name, arguments)

    with pytest.raises(MirrorError):
        palace_search(call_tool, object(), [stored.id], "needle")


def test_real_worker_mirrors_three_chunks_and_recalls_the_matching_chunk(
    hub, config, monkeypatch, stored
):
    # Exercise the worker's physical splitting and logical hydration, too.
    monkeypatch.setattr(type(config), "chunk_size", property(lambda _self: 80))
    token = hub.token(
        "handoff-agent",
        profile="slim",
        scopes=["handoff:write", "threads:read"],
        read_wings=["shared"],
        write_wing="inbox",
        trust="untrusted",
    )
    principal = hub.db.authenticate_token(token)
    assert principal is not None
    call_tool = hub.gateway.mediator.workers.call_tool
    chunks = [
        Chunk(0, 0, 0, "[0] user message: The workshop calendar uses the afternoon slot."),
        Chunk(
            1, 1, 1, "[1] assistant message: " + "The violet compass opens the observatory. " * 8
        ),
        Chunk(2, 1, 1, "The spare lantern waits beside the north gate.\n  café 🍵\r\n"),
    ]
    stored = replace(stored, workspace_id=hub.workspace, message_count=2, chunk_count=3)
    ids = mirror(call_tool, principal, stored, chunks)
    assert len(set(ids)) == 3
    assert mirror(call_tool, principal, stored, chunks) == ids
    listed = call_tool(
        principal, "mempalace_list_drawers", {"wing": MIRROR_WING, "room": stored.id}
    )
    assert len(listed["drawers"]) == 3
    for chunk, entry_id in zip(chunks, ids):
        entry = call_tool(principal, "mempalace_get_drawer", {"drawer_id": entry_id})
        assert entry["content"] == chunk.text
        assert entry["metadata"]["source_file"] == f"handoff:{stored.id}#{chunk.index}"
        assert entry["metadata"]["hub_connection"] == "handoff-agent"
        assert entry["metadata"]["hub_workspace"] == hub.workspace

    hits = palace_search(call_tool, principal, [stored.id], "violet compass observatory", 3)
    assert hits[0].chunk_index == 1
    assert hits[0].text == chunks[1].text
    assert (hits[0].seq_start, hits[0].seq_end) == (1, 1)
    assert {hit.chunk_index for hit in hits} == {0, 1, 2}
    continuation = palace_search(call_tool, principal, [stored.id], "spare lantern north gate", 1)[
        0
    ]
    assert continuation.chunk_index == 2
    assert continuation.text == chunks[2].text
    assert (continuation.seq_start, continuation.seq_end) == (1, 1)
    assert palace_search(call_tool, principal, ["thr_missing"], "violet compass") == []


def test_cross_thread_rerank_uses_worker_query_and_locale(monkeypatch):
    from mempalace.i18n import get_stopwords
    from mempalace.searcher import _hybrid_rank

    clean = "session ZXQ739251"
    raw = "Kubernetes cluster migration session " * 20 + clean
    monkeypatch.setenv("MEMPALACE_LANG", "de")
    worker = FakeWorker()
    worker.search_results["thr_first"] = _hybrid_rank(
        [
            _hit(
                text="[0] user message: session " * 20 + "Kubernetes cluster migration",
                distance=0.5,
            )
        ],
        clean,
        stop_words=frozenset(get_stopwords("en")),
    )
    worker.search_results["thr_second"] = _hybrid_rank(
        [_hit("thr_second", text="[0] user message: ZXQ739251", distance=None)],
        clean,
        stop_words=frozenset(get_stopwords("en")),
    )

    def call(principal, name, args):
        result = worker(principal, name, args)
        if name == "mempalace_search":
            return {**result, "query": clean, "ranking_lang": "en"}
        return result

    hits = palace_search(call, object(), ["thr_first", "thr_second"], raw, limit=1)
    assert hits[0].stored_thread_id == "thr_second"

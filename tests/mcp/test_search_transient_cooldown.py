"""A transient index error costs one search the cache drop and wait, not every search.

While the HNSW flush window lasts every search met the error, dropped the palace caches,
slept 2 s holding the request lock and searched again: a flat extra ~4 s per search, and
searches running together dropped each other's handles. One search per cooldown does that;
a search that meets the error again inside the cooldown is answered from the lexical index.
"""

import time

from _mcp_server_helpers import _patch_mcp_server

TRANSIENT = {"error": "Search error: Error executing plan: Internal error: Error finding id"}


def _vector_always_fails(calls):
    def fake_search(*args, **kwargs):
        calls.append(bool(kwargs.get("vector_disabled")))
        if kwargs.get("vector_disabled"):
            return {"results": [{"text": "lexical hit", "wing": "w", "room": "r"}]}
        return dict(TRANSIENT)

    return fake_search


def test_second_search_in_the_cooldown_is_answered_at_once_from_the_lexical_index(
    monkeypatch, config, kg
):
    _patch_mcp_server(monkeypatch, config, kg)
    from mempalace import mcp_server

    calls, resets, sleeps = [], [], []
    monkeypatch.setattr(mcp_server, "search_memories", _vector_always_fails(calls))
    monkeypatch.setattr(mcp_server, "_force_chroma_cache_reset", lambda: resets.append(1))
    monkeypatch.setattr(mcp_server.time, "sleep", lambda seconds: sleeps.append(seconds))

    first = mcp_server.tool_search(query="anything")
    assert "error" in first and len(resets) == 1 and sleeps == [2]  # one drop and wait, as before

    second = mcp_server.tool_search(query="anything")
    assert len(resets) == 1, "a second search inside the cooldown dropped the caches again"
    assert sleeps == [2], "a second search inside the cooldown waited again"
    assert second["results"][0]["text"] == "lexical hit"
    assert second["index_catching_up"] is True


def test_the_cooldown_ends_and_the_next_window_is_retried_as_before(monkeypatch, config, kg):
    _patch_mcp_server(monkeypatch, config, kg)
    from mempalace import mcp_server

    clock = [1000.0]
    monkeypatch.setattr(mcp_server.time, "monotonic", lambda: clock[0])
    calls, resets = [], []
    monkeypatch.setattr(mcp_server, "search_memories", _vector_always_fails(calls))
    monkeypatch.setattr(mcp_server, "_force_chroma_cache_reset", lambda: resets.append(1))
    monkeypatch.setattr(mcp_server.time, "sleep", lambda seconds: None)

    mcp_server.tool_search(query="anything")
    clock[0] += mcp_server._TRANSIENT_RESET_COOLDOWN_S + 1
    mcp_server.tool_search(query="anything")
    assert len(resets) == 2


def test_a_search_in_a_steady_flush_window_stays_fast_without_patched_sleep(
    monkeypatch, config, kg
):
    """Through the real sleep: after the first, searches in the window do not wait 2 s."""
    _patch_mcp_server(monkeypatch, config, kg)
    from mempalace import mcp_server

    calls = []
    monkeypatch.setattr(mcp_server, "search_memories", _vector_always_fails(calls))
    monkeypatch.setattr(mcp_server, "_force_chroma_cache_reset", lambda: None)
    monkeypatch.setattr(mcp_server, "_transient_reset_at", time.monotonic(), raising=False)

    started = time.monotonic()
    for _ in range(3):
        result = mcp_server.tool_search(query="anything")
        assert result.get("index_catching_up") is True, result
    assert time.monotonic() - started < 1.5, "searches in the flush window each waited"

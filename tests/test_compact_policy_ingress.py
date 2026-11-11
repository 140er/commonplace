"""External metadata cannot select the server's keyword-only write policy."""

import json
from unittest.mock import Mock

import pytest

from mempalace.stored_text import KEYWORD_ONLY, RESERVED_METADATA, user_tool_arguments
from tests._commonplace_helpers import hub_fixture  # noqa: F401


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("mempalace_add_drawer", {"wing": "w", "room": "r", "content": "document"}),
        ("mempalace_update_drawer", {"drawer_id": "d", "content": "changed"}),
    ],
)
def test_mcp_ingress_strips_reserved_policy(name, arguments, monkeypatch):
    from mempalace import mcp_server as server

    seen = []

    def handler(**kwargs):
        seen.append(kwargs)
        return {"success": True}

    monkeypatch.setitem(server.TOOLS[name], "handler", handler)
    monkeypatch.setattr(server, "_mcp_tool_preflight_refusal", lambda *args: None)
    error, _ = server.call_tool(
        1, {"name": name, "arguments": {**arguments, **dict.fromkeys(RESERVED_METADATA, True)}}
    )
    assert error is None
    assert seen == [arguments]


def test_cli_event_and_artifact_metadata_remain_verbatim():
    from mempalace import cli

    metadata = {
        **dict.fromkeys(RESERVED_METADATA, True),
        "topic": "normal",
        "nested": dict.fromkeys(RESERVED_METADATA, "literal"),
    }
    assert cli._parse_metadata_arg(json.dumps(metadata)) == metadata


def test_hub_forwarding_strips_metadata_without_mutating_caller(monkeypatch):
    from mempalace.hub_client import HubClient

    client = HubClient.__new__(HubClient)
    post = Mock(return_value={})
    monkeypatch.setattr(client, "post", post)
    arguments = {
        **dict.fromkeys(RESERVED_METADATA, True),
        "lexical_only": True,
        "metadata": {**dict.fromkeys(RESERVED_METADATA, True), "wing": "w"},
    }
    client.call_tool("mempalace_add_drawer", arguments)
    assert post.call_args.args[0]["params"]["arguments"] == {"metadata": {"wing": "w"}}
    assert arguments["metadata"][KEYWORD_ONLY] is True
    assert user_tool_arguments(
        {"metadatas": [{**dict.fromkeys(RESERVED_METADATA, True), "wing": "w"}]}
    ) == {"metadatas": [{"wing": "w"}]}


@pytest.mark.parametrize("trust", ["untrusted", "trusted"])
def test_gateway_token_cannot_select_internal_policy(hub, monkeypatch, trust):
    from mempalace import mcp_server

    seen = []
    original = mcp_server.TOOLS["mempalace_add_drawer"]["handler"]

    def handler(**kwargs):
        seen.append(kwargs.copy())
        return original(**kwargs)

    monkeypatch.setitem(mcp_server.TOOLS["mempalace_add_drawer"], "handler", handler)
    token = hub.token(
        "writer", scopes=["memory:write"], profile="slim", write_wing="shared", trust=trust
    )
    result, response = hub.call(
        token,
        "mempalace_add_drawer",
        wing="shared",
        room="logs",
        content="Exact text",
        lexical_only=True,
        **dict.fromkeys(RESERVED_METADATA, True),
    )
    assert result["success"], response
    assert len(seen) == 1
    assert "lexical_only" not in seen[0]
    assert not RESERVED_METADATA.intersection(seen[0])


def test_external_source_adapter_metadata_strips_reserved_policy(tmp_path):
    from mempalace.sources.base import DrawerRecord
    from mempalace.sources.context import PalaceContext

    collection = Mock()
    context = PalaceContext(collection, Mock(), str(tmp_path))
    metadata = {**dict.fromkeys(RESERVED_METADATA, True), "wing": "w"}
    context.upsert_drawer(
        DrawerRecord(content="verbatim", source_file="source.txt", metadata=metadata)
    )
    stored = collection.upsert.call_args.kwargs["metadatas"][0]
    assert not RESERVED_METADATA.intersection(stored)
    assert metadata[KEYWORD_ONLY] is True


def test_other_backends_keep_embedding_records_with_internal_marker(tmp_path, monkeypatch):
    from mempalace.backends.embedding_wrapper import EmbeddingCollection
    from mempalace.backends.sqlite_exact import SQLiteExactBackend

    backend = SQLiteExactBackend()
    embed = Mock(return_value=[[1, 0]])
    monkeypatch.setattr("mempalace.backends.embedding_wrapper._embed_texts", embed)
    try:
        col = EmbeddingCollection(backend.get_collection(str(tmp_path), "records", create=True))
        col.add(ids=["k"], documents=["document"], metadatas=[{KEYWORD_ONLY: True}])
        embed.assert_called_once()
        assert col.get(include=["embeddings"]).embeddings == [[1, 0]]
        assert col.query(query_embeddings=[[1, 0]]).ids == [["k"]]
    finally:
        backend.close()

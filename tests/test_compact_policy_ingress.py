"""External metadata cannot select the server's keyword-only write policy."""

import json
from unittest.mock import Mock

import pytest

from mempalace.stored_text import KEYWORD_ONLY, RESERVED_METADATA, user_tool_arguments


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


def test_cli_metadata_strips_reserved_policy():
    from mempalace import cli

    metadata = {**dict.fromkeys(RESERVED_METADATA, True), "topic": "normal"}
    assert cli._parse_metadata_arg(json.dumps(metadata)) == {"topic": "normal"}


def test_hub_forwarding_strips_metadata_without_mutating_caller(monkeypatch):
    from mempalace.hub_client import HubClient

    client = HubClient.__new__(HubClient)
    post = Mock(return_value={})
    monkeypatch.setattr(client, "post", post)
    arguments = {
        **dict.fromkeys(RESERVED_METADATA, True),
        "metadata": {**dict.fromkeys(RESERVED_METADATA, True), "wing": "w"},
    }
    client.call_tool("mempalace_add_drawer", arguments)
    assert post.call_args.args[0]["params"]["arguments"] == {"metadata": {"wing": "w"}}
    assert arguments["metadata"][KEYWORD_ONLY] is True
    assert user_tool_arguments(
        {"metadatas": [{**dict.fromkeys(RESERVED_METADATA, True), "wing": "w"}]}
    ) == {"metadatas": [{"wing": "w"}]}


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

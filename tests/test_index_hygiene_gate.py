"""Small offline gate exercises the real MCP search handler and SQLite ranking."""

import importlib.util
import inspect
import sys
from collections import Counter
from pathlib import Path

import pytest

from commonplace.handoff.transcripts import parse
from mempalace.index_hygiene import index_text
from mempalace.lexical import tokenize

_SPEC = importlib.util.spec_from_file_location(
    "index_hygiene_gate", Path(__file__).parents[1] / "benchmarks" / "index_hygiene_gate.py"
)
assert _SPEC is not None and _SPEC.loader is not None
gate = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = gate
_SPEC.loader.exec_module(gate)


@pytest.fixture(scope="module")
def reports():
    return {
        strategy: gate.run_gate(strategy=strategy, noise=4)
        for strategy in ("default", "vector", "union")
    }


def test_transcript_shapes_are_deterministic_and_keep_raw_sources():
    corpus = gate.synthetic_corpus(noise=4)
    assert corpus == gate.synthetic_corpus(noise=4)
    assert corpus != gate.synthetic_corpus(seed=39, noise=4)
    assert set(corpus.transcripts) == {"claude-code", "codex"}
    for fmt, source in corpus.transcripts.items():
        parsed = parse(source)
        messages = [message for message in corpus.messages if message.id.startswith(fmt)]
        assert [message.raw for message in messages] == [message.raw for message in parsed]
        assert "".join(message.raw for message in messages) == source
        assert {message.kind for message in messages} >= {
            "message",
            "tool_call",
            "tool_result",
            "other",
        }
    payloads = Counter(payload for message in corpus.messages for payload in message.payloads)
    assert max(payloads.values()) == 8


def test_token_diff_counts_independently_annotated_runs():
    result = gate.token_diff(gate.synthetic_corpus(noise=4))
    assert result == {
        "pass": True,
        "outside_tokens": 443,
        "lost_tokens": 0,
        "base64_chars": 17184,
        "exact_spans_and_text": True,
    }


def test_token_gate_fails_when_an_outside_word_is_lost(monkeypatch):
    monkeypatch.setattr(
        gate, "index_text", lambda text: index_text(text).replace("observatory", "")
    )
    result = gate.token_diff(gate.synthetic_corpus(noise=0))
    assert result["pass"] is False
    assert result["lost_tokens"] == 2
    assert result["exact_spans_and_text"] is False


def test_token_gate_fails_on_reordering_even_without_lost_tokens(monkeypatch):
    monkeypatch.setattr(
        gate, "index_text", lambda text: index_text(text).replace("violet comet", "comet violet")
    )
    result = gate.token_diff(gate.synthetic_corpus(noise=0))
    assert result["lost_tokens"] == 0
    assert result["pass"] is False


def test_vector_policy_blocks_tool_output_needles(reports):
    report = reports["vector"]
    assert report["pass"] is False
    assert report["checks"]["needles_top5"]["found"] == 4
    for case in report["policy"]["needles"]:
        assert case["top5"] == (case["kind"] == "conversation")
    assert report["checks"]["recall_at_10"] == {"pass": False, "today": 1.0, "policy": 0.4}
    assert report["checks"]["vector_tool_share"]["today_hits"] == [44, 100]
    assert report["checks"]["vector_tool_share"]["policy_hits"] == [0, 100]
    assert "BLOCKED" in gate.format_report(report)
    assert "feat/search-union-default" in gate.format_report(report)


def test_default_strategy_is_measured_without_overriding_handler(reports):
    from mempalace import mcp_server

    assert reports["default"]["effective_strategy"] in {"vector", "union"}
    # A default change in the separate PR should cause the default run to pass.
    effective = inspect.signature(mcp_server.tool_search).parameters["candidate_strategy"].default
    assert reports["default"]["effective_strategy"] == effective
    assert reports["default"]["checks"] == reports[effective]["checks"]


def test_union_recovers_keyword_only_output_through_default_tool_path(reports):
    report = reports["union"]
    assert report["pass"] is True
    assert report["checks"]["needles_top5"]["found"] == 10
    assert report["checks"]["recall_at_10"] == {"pass": True, "today": 1.0, "policy": 1.0}
    assert report["checks"]["token_diff"]["lost_tokens"] == 0
    assert report["checks"]["vector_tool_share"]["policy"] == 0
    assert all(case["top5"] for case in report["policy"]["needles"])


def test_benchmark_seam_retains_verbatim_storage_and_keyword_only_rows():
    corpus = gate.synthetic_corpus(noise=1)
    with gate.index_chat(corpus, policy=True) as (directory, collection):
        stored = collection.get(include=["documents", "embeddings"])
        rows = dict(zip(stored.ids, zip(stored.documents, stored.embeddings)))
        for message in corpus.messages:
            document, embedding = rows[message.id]
            assert document == message.text
            if message.kind in {"tool_result", "other"}:
                assert len(embedding) == 0
            else:
                assert len(embedding) == 256
        lexical = collection.lexical_search(query="compiler cache directory", n_results=5)
        assert {"claude-code-4", "codex-4"} <= {hit.id for hit in lexical.hits}
        # Base64 payload words are absent from the keyword index, but present
        # in stored drawers. Surrounding path words remain searchable.
        lexical = collection.lexical_search(query="long component", n_results=5)
        assert {"claude-code-5", "codex-5"} <= {hit.id for hit in lexical.hits}
    assert not Path(directory).exists()


def test_synthetic_paraphrase_uses_fixed_synonyms_and_keyword_anchor():
    corpus = gate.synthetic_corpus(noise=0)
    for needle in corpus.needles:
        if needle.match != "paraphrase":
            continue
        target = next(message for message in corpus.messages if message.id == needle.target)
        query_terms = set(tokenize(needle.query))
        target_terms = set(tokenize(target.text))
        assert query_terms - target_terms
        assert query_terms & target_terms
    assert gate.synthetic_embedding("irrigation dawn") == gate.synthetic_embedding(
        "watering sunrise"
    )


def test_cli_reports_all_checks_and_returns_gate_status(monkeypatch, capsys, reports):
    monkeypatch.setattr(gate, "run_gate", lambda **kwargs: reports[kwargs["strategy"]])
    assert gate.main(["--strategy", "vector", "--noise", "4"]) == 1
    output = capsys.readouterr().out
    assert "FAIL needles_top5" in output
    assert "FAIL recall_at_10" in output
    assert "PASS token_diff" in output
    assert "PASS vector_tool_share" in output
    assert gate.main(["--strategy", "union", "--json"]) == 0
    assert '"pass": true' in capsys.readouterr().out


def test_invalid_arguments_fail_before_building_indexes():
    with pytest.raises(ValueError, match="strategy"):
        gate.run_gate(strategy="thread_recall")
    with pytest.raises(ValueError, match="noise"):
        gate.synthetic_corpus(noise=-1)


def test_policy_never_embeds_tool_output_but_does_embed_calls():
    corpus = gate.synthetic_corpus(noise=1)
    embedded = []

    class RecordingEmbedder(gate.GateEmbedder):
        def documents(self, texts):
            embedded.extend(texts)
            return super().documents(texts)

    with gate.index_chat(corpus, policy=True, embedder=RecordingEmbedder()):
        pass
    assert embedded == [
        index_text(message.text)
        for message in corpus.messages
        if message.kind in {"message", "tool_call"}
    ]
    assert any('"command"' in text for text in embedded)

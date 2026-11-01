"""Small offline gate exercises the real MCP search handler and SQLite ranking."""

import importlib.util
import inspect
import json
import os
import subprocess
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
    assert max(payloads.values()) > len(corpus.transcripts)
    records = [json.loads(line) for line in corpus.transcripts["claude-code"].splitlines()]
    parsed = parse(corpus.transcripts["claude-code"], "claude-code")
    for record, message in zip(records, parsed):
        block = record["message"]["content"][0]
        if block["type"] == "tool_result":
            assert record["type"] == record["message"]["role"] == "user"
            assert (message.role, message.kind) == ("tool", "tool_result")
            assert message.text.startswith(block["content"][0]["text"])
    assert parsed[4].kind == "tool_result"
    assert "amber-cache" in parsed[4].text
    assert any(block["type"] == "image" for block in records[4]["message"]["content"][0]["content"])


def test_token_diff_counts_independently_annotated_runs():
    corpus = gate.synthetic_corpus(noise=4)
    result = gate.token_diff(corpus)
    outside_tokens = base64_chars = 0
    for message in corpus.messages:
        outside = message.text
        for payload in message.payloads:
            base64_chars += len(payload) * outside.count(payload)
            outside = outside.replace(payload, " ")
        outside_tokens += len(tokenize(outside))
    assert result == {
        "pass": True,
        "outside_tokens": outside_tokens,
        "lost_tokens": 0,
        "base64_chars": base64_chars,
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
    assert all(case["top5"] for case in report["today"]["needles"])
    assert report["checks"]["needles_top5"]["found"] == 4
    for case in report["policy"]["needles"]:
        assert case["top5"] == (case["kind"] == "conversation")
    assert report["checks"]["recall_at_10"] == {"pass": False, "today": 1.0, "policy": 0.4}
    tool_hits, total_hits = report["checks"]["vector_tool_share"]["today_hits"]
    assert 0 < tool_hits < total_hits == 10 * len(report["today"]["needles"])
    assert report["checks"]["vector_tool_share"]["policy_hits"] == [0, total_hits]
    assert "BLOCKED" in gate.format_report(report)
    assert "feat/search-union-default" in gate.format_report(report)


def test_default_strategy_is_measured_without_overriding_handler(reports):
    from mempalace import mcp_server

    assert reports["default"]["effective_strategy"] in {"vector", "union"}
    # A default change in the separate PR should cause the default run to pass.
    effective = inspect.signature(mcp_server.tool_search).parameters["candidate_strategy"].default
    assert effective is None
    assert reports["default"]["effective_strategy"] == "union"
    assert reports["default"]["checks"] == reports["union"]["checks"]


def test_compact_gate_uses_real_writer_and_preserves_default_recall():
    report = gate.run_gate(backend="compact", noise=4)
    assert report["backend"] == "compact"
    assert report["effective_strategy"] == "union"
    assert report["pass"]
    assert report["checks"]["needles_top5"]["found"] == 10
    assert report["today"]["recall_at_10"] == report["policy"]["recall_at_10"] == 1.0
    assert report["today"]["vector_tool_hits"] > 0
    assert report["policy"]["vector_tool_hits"] == 0
    for before, after in zip(report["today"]["needles"], report["policy"]["needles"]):
        assert before["rank_at_5"] == after["rank_at_5"]
        assert before["rank_at_10"] == after["rank_at_10"]


def test_union_recovers_every_needle_per_format_and_exposes_rank_cost(reports):
    report = reports["union"]
    assert report["pass"] is True
    assert all(case["top5"] for case in report["today"]["needles"])
    assert report["checks"]["needles_top5"]["found"] == len(report["policy"]["needles"])
    assert report["checks"]["recall_at_10"] == {"pass": True, "today": 1.0, "policy": 1.0}
    assert report["checks"]["token_diff"]["lost_tokens"] == 0
    assert report["checks"]["vector_tool_share"]["policy"] == 0
    for fmt in ("claude-code", "codex"):
        cases = [case for case in report["policy"]["needles"] if case["format"] == fmt]
        assert len(cases) == 5
        assert all(case["top5"] for case in cases)
    for today, policy in zip(report["today"]["needles"], report["policy"]["needles"]):
        if policy["query"] == "archive backup password":
            assert (today["rank_at_5"], policy["rank_at_5"]) == (1, 2)
        elif policy["kind"] == "tool_output" and policy["match"] == "paraphrase":
            assert (today["rank_at_5"], policy["rank_at_5"]) == (2, 4)
    output = gate.format_report(report)
    assert "rank@5=1->2" in output
    assert "rank@5=2->4" in output
    assert "Separate collection claude-code: top5 today=5/5, policy=5/5" in output
    assert report["hybrid_weights"] == {"vector": 0.6, "bm25": 0.4}


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


def test_cli_reports_all_checks_and_returns_gate_status(monkeypatch, capfd, reports):
    monkeypatch.setattr(gate, "run_gate", lambda **kwargs: reports[kwargs["strategy"]])
    assert gate.main(["--strategy", "vector", "--noise", "4"]) == 1
    output = capfd.readouterr().out
    assert "FAIL needles_top5" in output
    assert "FAIL recall_at_10" in output
    assert "PASS token_diff" in output
    assert "PASS vector_tool_share" in output
    assert gate.main(["--strategy", "union", "--json"]) == 0
    assert '"pass": true' in capfd.readouterr().out


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


def test_operator_config_and_environment_cannot_change_the_gate(monkeypatch, tmp_path, reports):
    from mempalace import config as config_module

    operator = tmp_path / "operator"
    operator.mkdir()
    hostile = {"hybrid_rank_vector_weight": 1, "hybrid_rank_bm25_weight": 0, "lang": "nl"}
    config_file = operator / "config.json"
    config_file.write_text(json.dumps(hostile))
    monkeypatch.setattr(config_module, "_default_config_dir", lambda: operator)
    values = {
        "MEMPALACE_HYBRID_VECTOR_WEIGHT": "1",
        "MEMPALACE_HYBRID_BM25_WEIGHT": "0",
        "MEMPALACE_LANG": "nl",
        "MEMPAL_LANG": "de",
        "MEMPALACE_CONFIG_DIR": str(operator),
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    actual = gate.run_gate(strategy="union", noise=4)
    assert actual == reports["union"]
    assert all(os.environ[key] == value for key, value in values.items())
    assert json.loads(config_file.read_text()) == hostile


def test_corpus_has_competing_tool_and_conversation_vocabulary():
    corpus = gate.synthetic_corpus(noise=0)
    targets = {needle.target for needle in corpus.needles}
    for word in ("archive", "backup", "password", "cache"):
        matches = [
            message
            for message in corpus.messages
            if message.id not in targets and word in tokenize(message.text)
        ]
        assert any(message.kind == "message" for message in matches)
        assert any(message.kind == "tool_result" for message in matches)
    image_needle = next(message for message in corpus.messages if message.id == "claude-code-4")
    assert image_needle.payloads
    assert image_needle.kind == "tool_result"


def test_vector_share_check_detects_a_leaking_policy_seam(monkeypatch):
    original = gate.index_chat

    def leaking_index(corpus, *, policy, embedder=None):
        return original(corpus, policy=False, embedder=embedder)

    monkeypatch.setattr(gate, "index_chat", leaking_index)
    report = gate.run_gate(strategy="union", noise=0)
    check = report["checks"]["vector_tool_share"]
    assert check["pass"] is False
    assert check["today_hits"][0] > 0
    assert check["policy_hits"][0] > 0


def test_cli_json_uses_stdout_after_real_mcp_import():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.index_hygiene_gate",
            "--strategy",
            "union",
            "--noise",
            "0",
            "--json",
        ],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["pass"] is True


def test_gate_sandboxes_writer_locks(monkeypatch, tmp_path):
    from mempalace import paths

    operator_locks = tmp_path / "operator-locks"
    monkeypatch.setattr(paths, "lock_dir", lambda: operator_locks)
    original = paths.lock_dir
    with gate.index_chat(gate.synthetic_corpus(noise=0), policy=True) as (directory, _):
        assert paths.lock_dir() == Path(directory) / "locks"
        assert list(paths.lock_dir().glob("mine_palace_*.lock"))
        assert not operator_locks.exists()
    assert paths.lock_dir is original
    assert not Path(directory).exists()
    assert not operator_locks.exists()

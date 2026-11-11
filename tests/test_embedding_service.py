"""The shared local embedding service: same vectors, one model load, a clear failure mode."""

import hashlib
import json
import logging
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest

import mempalace.embedding as embedding
import mempalace.embedding_service as service_module
from mempalace.config import MempalaceConfig
from mempalace.embedding_service import (
    EMBEDDING_SERVICE_UNAVAILABLE_ERROR,
    EmbeddingService,
    EmbeddingServiceClient,
    EmbeddingServiceUnavailableError,
    resolve_address,
)

pytestmark = pytest.mark.skipif(
    not hasattr(socket, "AF_UNIX"), reason="the embedding service needs Unix domain sockets"
)

DIM = 384


def _vector(text: str) -> np.ndarray:
    """A deterministic unit vector with full-precision float32 values."""
    seed = int.from_bytes(
        hashlib.blake2b(text.encode("utf-8", "surrogatepass")).digest()[:8], "big"
    )
    values = np.random.default_rng(seed).standard_normal(DIM).astype(np.float32)
    return values / np.linalg.norm(values)


def _bag_of_words(text: str) -> np.ndarray:
    """Texts sharing words land near each other, so vector search has an order."""
    values = np.zeros(DIM, dtype=np.float32)
    for word in text.lower().split():
        values[int(hashlib.blake2b(word.encode()).hexdigest()[:8], 16) % DIM] += 1.0
    return values / (np.linalg.norm(values) or 1.0)


class StubModel:
    """Stands in for the loaded model inside the service."""

    def __init__(self, vector=_vector, delay=0.0):
        self.vector = vector
        self.delay = delay
        self.calls = []
        self.active = 0
        self.peak = 0
        self._lock = threading.Lock()

    def __call__(self, input):  # noqa: A002 — ChromaDB EF protocol
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.calls.append(list(input))
        try:
            if self.delay:
                time.sleep(self.delay)
            return [self.vector(text) for text in input]
        finally:
            with self._lock:
                self.active -= 1


@pytest.fixture
def socket_path():
    # A Unix socket path is capped near 100 bytes; pytest's tmp_path can exceed it.
    directory = tempfile.mkdtemp(prefix="mp-emb-", dir="/tmp")
    yield os.path.join(directory, "embedding.sock")
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(embedding, "_EF_CACHE", {})
    monkeypatch.delenv("MEMPALACE_EMBEDDING_SERVICE", raising=False)
    monkeypatch.delenv("MEMPALACE_EMBEDDING_MODEL", raising=False)
    monkeypatch.setenv("MEMPALACE_EMBEDDING_DEVICE", "cpu")


def _serve(socket_path, model=None, **options):
    return EmbeddingService(
        socket_path, model="minilm", embedder=model or StubModel(), **options
    ).start()


def _client_ef(monkeypatch, socket_path, model="minilm"):
    monkeypatch.setenv("MEMPALACE_EMBEDDING_SERVICE", socket_path)
    return embedding.get_embedding_function(model=model)


# ── the switch ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", [None, "", "  ", "0", "off", "OFF", "false", "no", False])
def test_switch_is_off_for_unset_and_off_values(value, tmp_path):
    assert resolve_address(value, tmp_path) is None


@pytest.mark.parametrize("value", ["1", "on", "True", "yes", True])
def test_switch_on_means_the_socket_in_the_config_directory(value, tmp_path):
    assert resolve_address(value, tmp_path) == str(tmp_path / "embedding.sock")


def test_switch_takes_an_absolute_socket_path_and_refuses_a_relative_one(tmp_path):
    assert resolve_address("/run/mempalace/e.sock", tmp_path) == "/run/mempalace/e.sock"
    with pytest.raises(ValueError, match="absolute socket path"):
        resolve_address("run/e.sock", tmp_path)


def test_config_reads_env_before_file_and_defaults_to_off(tmp_path, monkeypatch):
    assert MempalaceConfig(config_dir=tmp_path).embedding_service is None
    (tmp_path / "config.json").write_text(json.dumps({"embedding_service": "/a/file.sock"}))
    assert MempalaceConfig(config_dir=tmp_path).embedding_service == "/a/file.sock"
    monkeypatch.setenv("MEMPALACE_EMBEDDING_SERVICE", "/an/env.sock")
    assert MempalaceConfig(config_dir=tmp_path).embedding_service == "/an/env.sock"
    monkeypatch.setenv("MEMPALACE_EMBEDDING_SERVICE", "off")
    assert MempalaceConfig(config_dir=tmp_path).embedding_service is None


def test_default_builds_the_in_process_function_and_never_touches_a_socket(monkeypatch):
    def no_sockets(*args, **kwargs):
        raise AssertionError("the default path opened a socket")

    monkeypatch.setattr(service_module.socket, "socket", no_sockets)
    ef = embedding.get_embedding_function(model="minilm")
    assert not hasattr(ef, "service_client")
    assert ("minilm", ("CPUExecutionProvider",)) in embedding._EF_CACHE


def test_only_the_local_onnx_models_are_routed_to_the_service(monkeypatch, socket_path):
    monkeypatch.setenv("MEMPALACE_EMBEDDING_SERVICE", socket_path)
    monkeypatch.setenv("MEMPALACE_EMBEDDING_API_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("MEMPALACE_EMBEDDING_API_MODEL", "some-model")
    remote = embedding.get_embedding_function(model="openai-compat")
    assert isinstance(remote, embedding.OpenAICompatEmbeddingFunction)
    assert hasattr(embedding.get_embedding_function(model="minilm"), "service_client")
    assert hasattr(embedding.get_embedding_function(model="embeddinggemma"), "service_client")
    # The service itself asks for the in-process function whatever the switch says.
    assert not hasattr(
        embedding.get_embedding_function(model="minilm", local=True), "service_client"
    )


def test_service_refuses_to_hold_a_model_that_stays_in_process(socket_path):
    with pytest.raises(ValueError, match="stays in-process"):
        EmbeddingService(socket_path, model="openai-compat")


# ── identical vectors ────────────────────────────────────────────────────

TEXTS = [
    "The authentication module uses JWT tokens.",
    "",
    "naïve café — 記憶の宮殿 🏛️",
    "a stray byte \udcff kept verbatim",
    'line one\nline two\ttabbed "quoted" \\ backslash',
    "long " * 3000,
]


def test_vectors_are_the_models_bytes_for_every_kind_of_text(monkeypatch, socket_path):
    model = StubModel()
    with _serve(socket_path, model):
        ef = _client_ef(monkeypatch, socket_path)
        vectors = ef(input=TEXTS)
    assert model.calls == [["probe"], TEXTS]
    assert len(vectors) == len(TEXTS)
    for text, vector in zip(TEXTS, vectors):
        assert vector.dtype == np.float32
        assert vector.tobytes() == _vector(text).tobytes()


def test_a_large_batch_is_split_and_comes_back_in_order(monkeypatch, socket_path):
    texts = [f"document {i}" for i in range(service_module.CLIENT_MAX_TEXTS * 2 + 7)]
    model = StubModel()
    with _serve(socket_path, model):
        vectors = _client_ef(monkeypatch, socket_path)(input=texts)
    assert [len(call) for call in model.calls] == [1, 256, 256, 7]  # the start probe first
    assert all(v.tobytes() == _vector(t).tobytes() for t, v in zip(texts, vectors))


def test_a_bare_string_is_one_document_and_queries_use_the_same_path(monkeypatch, socket_path):
    with _serve(socket_path):
        ef = _client_ef(monkeypatch, socket_path)
        assert len(ef(input="abc")) == 1
        assert ef.embed_query(input=["abc"])[0].tobytes() == _vector("abc").tobytes()


def test_the_service_function_keeps_the_in_process_identity(monkeypatch, socket_path):
    """ChromaDB persists and checks these; a palace must not see the switch."""
    local = embedding.get_embedding_function(model="minilm", local=True)
    via_service = _client_ef(monkeypatch, socket_path)
    from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

    assert isinstance(via_service, ONNXMiniLM_L6_V2) and isinstance(local, ONNXMiniLM_L6_V2)
    assert via_service.name() == local.name() == "default"
    assert via_service.get_config() == local.get_config()
    gemma = embedding.get_embedding_function(model="embeddinggemma")
    assert isinstance(gemma, embedding.EmbeddinggemmaONNX)
    assert gemma.name() == "embeddinggemma_300m"


def test_embeddinggemma_rows_come_back_as_lists_of_floats(monkeypatch, socket_path):
    model = StubModel()
    with EmbeddingService(socket_path, model="embeddinggemma", embedder=model):
        ef = _client_ef(monkeypatch, socket_path, model="embeddinggemma")
        rows = ef.embed_documents(input=["hallo wereld"])
    assert type(rows) is list and type(rows[0]) is list and type(rows[0][0]) is float
    assert rows[0] == _vector("hallo wereld").tolist()
    assert ef._session is None  # the model never loaded in this process


def _minilm_model_dir():
    from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

    return Path(ONNXMiniLM_L6_V2.DOWNLOAD_PATH) / ONNXMiniLM_L6_V2.EXTRACTED_FOLDER_NAME


def _needs_minilm():
    if not (_minilm_model_dir() / "model.onnx").is_file():
        pytest.skip("the MiniLM model is not installed; this test never downloads it")


def test_real_minilm_vectors_match_the_in_process_path_bit_for_bit(monkeypatch, socket_path):
    _needs_minilm()
    texts = [f"{'memory palace ' * (i % 40)}drawer {i}" for i in range(70)] + TEXTS[:3]
    in_process = embedding.get_embedding_function(model="minilm", local=True)
    expected = in_process(input=texts)
    expected_query = in_process.embed_query(input=["where is the auth module?"])
    with EmbeddingService(socket_path, model="minilm", device="cpu"):
        ef = _client_ef(monkeypatch, socket_path)
        got = ef(input=texts)
        got_query = ef.embed_query(input=["where is the auth module?"])
        assert "model" not in vars(ef)  # no ONNX session in the calling function
    assert type(got) is type(expected) and len(got) == len(expected) == 73
    for want, have in zip(expected, got):
        assert type(have) is type(want) and have.dtype == want.dtype
        assert have.tobytes() == want.tobytes()
    assert got_query[0].tobytes() == expected_query[0].tobytes()


# ── failure mode ─────────────────────────────────────────────────────────


def test_no_service_is_a_fast_named_error_and_nothing_embeds_instead(monkeypatch, socket_path):
    ef = _client_ef(monkeypatch, socket_path)
    started = time.monotonic()
    with pytest.raises(EmbeddingServiceUnavailableError) as raised:
        ef(input=["anything"])
    assert time.monotonic() - started < 1.0
    message = str(raised.value)
    assert socket_path in message and "socket not found" in message
    assert "does not fall back" in message and "python -m mempalace.embedding_service" in message
    assert "model" not in vars(ef)  # the caller did not load the model itself


def test_a_stopped_service_fails_then_a_restarted_one_serves_again(monkeypatch, socket_path):
    monkeypatch.setattr(service_module, "DOWN_COOLDOWN_S", 0.0)
    ef = _client_ef(monkeypatch, socket_path)
    with _serve(socket_path):
        assert len(ef(input=["a"])) == 1
    with pytest.raises(EmbeddingServiceUnavailableError):
        ef(input=["a"])
    with _serve(socket_path):
        assert ef(input=["a"])[0].tobytes() == _vector("a").tobytes()


def test_after_a_transport_failure_calls_fail_fast_for_the_cooldown(monkeypatch, socket_path):
    client = EmbeddingServiceClient(socket_path, "minilm", timeout=5)
    with pytest.raises(EmbeddingServiceUnavailableError):
        client.embed(["a"])
    with _serve(socket_path):
        # Still inside the cooldown: no new attempt, the first reason is reported.
        with pytest.raises(EmbeddingServiceUnavailableError, match="socket not found"):
            client.embed(["a"])
        client._down_until = 0.0
        assert client.embed(["a"]).shape == (1, DIM)


def test_a_hung_service_times_out_as_unavailable(socket_path):
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(socket_path)
    listener.listen(1)
    try:
        client = EmbeddingServiceClient(socket_path, "minilm", timeout=0.3)
        with pytest.raises(EmbeddingServiceUnavailableError, match="no answer within 0.3s"):
            client.embed(["a"])
    finally:
        listener.close()


def test_a_service_holding_another_model_refuses_instead_of_embedding(socket_path):
    model = StubModel()
    with _serve(socket_path, model):
        client = EmbeddingServiceClient(socket_path, "embeddinggemma")
        with pytest.raises(EmbeddingServiceUnavailableError, match="model_not_served") as raised:
            client.embed(["a"])
        assert "serves 'minilm', not 'embeddinggemma'" in str(raised.value)
        # A refusal is not an outage: the next request is tried.
        assert client._down_until == 0.0
    assert model.calls == [["probe"]]


def test_a_failing_model_is_reported_without_the_callers_text(socket_path, caplog):
    def explode(text):
        raise RuntimeError(f"cannot embed {text}")

    model = StubModel()
    with _serve(socket_path, model) as service:
        model.vector = explode
        client = EmbeddingServiceClient(socket_path, "minilm")
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(EmbeddingServiceUnavailableError) as raised:
                client.embed(["a private sentence"])
        assert "embed_failed" in str(raised.value) and "RuntimeError" in str(raised.value)
        assert "a private sentence" not in str(raised.value)
        assert "a private sentence" not in caplog.text
        assert service.stats()["errors"] == 1


def test_unavailable_is_a_model_error_with_its_own_kind_and_hint(socket_path):
    exc = EmbeddingServiceUnavailableError(socket_path, "no service is listening")
    result = embedding.model_error_result(exc)
    assert result["error"] == EMBEDDING_SERVICE_UNAVAILABLE_ERROR
    assert result["error_class"] == "EmbeddingServiceUnavailableError"
    assert result["error_class"] in embedding.MODEL_ERROR_CLASS_NAMES
    assert "python -m mempalace.embedding_service" in result["hint"]


# ── bounds ───────────────────────────────────────────────────────────────


def test_requests_over_the_limits_are_refused_and_the_service_keeps_serving(socket_path):
    model = StubModel()
    with _serve(socket_path, model, max_texts=4, max_request_bytes=2048) as service:
        client = EmbeddingServiceClient(socket_path, "minilm")
        with pytest.raises(EmbeddingServiceUnavailableError, match="too_large.*5 texts"):
            client._embed_chunk(["t"] * 5, np)
        with pytest.raises(EmbeddingServiceUnavailableError):
            client._embed_chunk(["x" * 5000], np)
        client._down_until = 0.0
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as raw:
            raw.connect(socket_path)
            raw.sendall(b"\x00\x00\x00\x05not{j")
            raw.settimeout(5)
            assert raw.recv(16) == b""  # dropped, no reply
        assert client.embed(["still here"]).shape == (1, DIM)
        assert service.stats()["errors"] >= 3
    assert model.calls == [["probe"], ["still here"]]


@pytest.mark.parametrize(
    "option", ["max_connections", "max_concurrent", "max_request_bytes", "max_texts"]
)
def test_limits_must_be_positive(socket_path, option):
    with pytest.raises(ValueError, match=option):
        EmbeddingService(socket_path, model="minilm", embedder=StubModel(), **{option: 0})


def test_concurrent_callers_each_get_their_own_vectors_within_the_inference_cap(socket_path):
    model = StubModel(delay=0.005)
    with _serve(socket_path, model, max_concurrent=2, max_connections=4) as service:

        def caller(worker):
            client = EmbeddingServiceClient(socket_path, "minilm")
            for round_ in range(5):
                texts = [f"worker {worker} round {round_} text {i}" for i in range(3)]
                vectors = client.embed(texts)
                for text, vector in zip(texts, vectors):
                    assert vector.tobytes() == _vector(text).tobytes()
            return worker

        with ThreadPoolExecutor(max_workers=16) as pool:
            assert sorted(pool.map(caller, range(16))) == list(range(16))
        stats = service.stats()
    assert stats["requests"] == 80 and stats["texts"] == 240 and stats["errors"] == 0
    assert stats["model_loads"] == 1
    assert 1 <= model.peak <= 2


# ── the socket ───────────────────────────────────────────────────────────


def test_the_socket_is_owner_only_and_removed_on_close(socket_path):
    with _serve(socket_path):
        mode = os.stat(socket_path).st_mode
        assert stat.S_ISSOCK(mode) and stat.S_IMODE(mode) == 0o600
    assert not os.path.exists(socket_path)


def test_a_second_service_on_the_same_socket_refuses_and_leaves_the_first_alone(socket_path):
    with _serve(socket_path):
        with pytest.raises(RuntimeError, match="already listening"):
            _serve(socket_path)
        assert EmbeddingServiceClient(socket_path, "minilm").ping()["model"] == "minilm"


def test_a_stale_socket_is_replaced_but_another_file_is_not(socket_path):
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(socket_path)
    stale.close()  # the file stays, nothing listens
    with _serve(socket_path):
        assert EmbeddingServiceClient(socket_path, "minilm").ping()["ok"]
    Path(socket_path).write_text("not a socket")
    with pytest.raises(RuntimeError, match="not a socket"):
        _serve(socket_path)
    assert Path(socket_path).read_text() == "not a socket"


def test_ping_reports_model_limits_and_counters(socket_path):
    with _serve(socket_path, max_concurrent=3) as service:
        client = EmbeddingServiceClient(socket_path, "minilm")
        client.embed(["a", "b"])
        status = client.ping()
    assert status["model"] == "minilm" and status["dim"] == DIM
    assert status["pid"] == os.getpid()
    assert status["limits"]["max_concurrent"] == 3
    assert status["stats"]["texts"] == 2 and status["stats"]["model_loads"] == 1
    assert service.stats()["requests"] == 2


def test_main_reports_a_start_failure_and_exits_nonzero(socket_path, caplog):
    Path(socket_path).write_text("occupied")
    with caplog.at_level(logging.ERROR):
        assert service_module.main(["--socket", socket_path, "--model", "openai-compat"]) == 1
    assert "did not start" in caplog.text


def test_hosted_workers_are_given_the_switch():
    from commonplace.workers import _PASSED_ENV

    assert "MEMPALACE_EMBEDDING_SERVICE" in _PASSED_ENV


# ── a palace through the service: search degrades, writes refuse ─────────

DRAWERS = {
    "drawer_auth": "The authentication module uses JWT tokens for session management.",
    "drawer_db": "Database migrations are handled by Alembic on PostgreSQL.",
    "drawer_ui": "The React frontend uses TanStack Query for server state.",
}


@pytest.fixture
def palace(monkeypatch, socket_path, tmp_path):
    """A palace whose drawers were embedded through a running service."""
    from mempalace.palace import get_collection

    monkeypatch.setattr(service_module, "DOWN_COOLDOWN_S", 0.0)
    monkeypatch.setenv("MEMPALACE_EMBEDDING_SERVICE", socket_path)
    monkeypatch.setenv("MEMPALACE_CONFIG_DIR", str(tmp_path / "config"))
    path = str(tmp_path / "palace")
    service = _serve(socket_path, StubModel(vector=_bag_of_words))
    collection = get_collection(path, create=True)
    collection.upsert(
        ids=list(DRAWERS),
        documents=list(DRAWERS.values()),
        metadatas=[
            {"wing": "project", "room": "code", "source_file": f"{name}.md", "chunk_index": 0}
            for name in DRAWERS
        ],
    )
    yield path, service
    service.close()


def test_search_uses_vectors_while_the_service_is_up(palace):
    from mempalace.searcher import search_memories

    path, _service = palace
    result = search_memories("authentication JWT tokens", path, include_facts=False)
    assert "degraded" not in result and "warning" not in result
    assert result["results"][0]["drawer_id"] == "drawer_auth"
    assert result["results"][0]["distance"] is not None


def test_search_degrades_to_lexical_and_says_so_when_the_service_is_down(palace):
    from mempalace.searcher import search_memories

    path, service = palace
    service.close()
    result = search_memories("Alembic migrations", path, include_facts=False)
    assert "error" not in result
    assert result["degraded"] == "lexical"
    assert result["embedding_service_unavailable"] is True
    assert "keyword match only" in result["warning"]
    assert "python -m mempalace.embedding_service" in result["warning"]
    assert "socket not found" in result["embedding_service_error"]
    assert [hit["drawer_id"] for hit in result["results"]] == ["drawer_db"]
    assert result["results"][0]["text"] == DRAWERS["drawer_db"]  # verbatim
    assert result["results"][0]["distance"] is None  # no invented vector score


def test_degraded_search_keeps_the_wing_filter(palace):
    from mempalace.searcher import search_memories

    path, service = palace
    service.close()
    result = search_memories("Alembic migrations", path, wing="elsewhere", include_facts=False)
    assert result["degraded"] == "lexical" and result["results"] == []


def test_a_caller_forcing_vector_only_still_gets_lexical_results_when_down(palace):
    from mempalace.searcher import search_memories

    path, service = palace
    service.close()
    result = search_memories(
        "TanStack Query", path, candidate_strategy="vector", include_facts=False
    )
    assert result["degraded"] == "lexical"
    assert [hit["drawer_id"] for hit in result["results"]] == ["drawer_ui"]


def test_cli_search_prints_keyword_matches_and_the_reason_when_down(palace, capsys):
    from mempalace.searcher import search

    path, service = palace
    service.close()
    search("Alembic migrations", path)
    captured = capsys.readouterr()
    assert "Alembic" in captured.out
    assert "embedding service" in captured.err and "keyword matches only" in captured.err


def test_writes_are_refused_with_the_error_when_the_service_is_down(palace):
    from mempalace.palace import get_collection

    path, service = palace
    service.close()
    collection = get_collection(path, create=True)
    with pytest.raises(EmbeddingServiceUnavailableError):
        collection.upsert(
            ids=["drawer_new"],
            documents=["A new note."],
            metadatas=[{"wing": "project", "room": "code"}],
        )
    assert collection.get(ids=["drawer_new"]).ids == []
    assert sorted(collection.get(ids=list(DRAWERS)).ids) == sorted(DRAWERS)  # reads still work


def test_search_recovers_vectors_when_the_service_returns(palace, socket_path):
    from mempalace.searcher import search_memories

    path, service = palace
    service.close()
    assert search_memories("JWT tokens", path, include_facts=False)["degraded"] == "lexical"
    with _serve(socket_path, StubModel(vector=_bag_of_words)):
        result = search_memories("JWT tokens", path, include_facts=False)
    assert "degraded" not in result and result["results"][0]["distance"] is not None


# ── one model load instead of N ──────────────────────────────────────────

_COUNT_SESSIONS = textwrap.dedent(
    """
    import json, sys
    import onnxruntime

    created = []
    real = onnxruntime.InferenceSession

    class Counting(real):
        def __init__(self, *args, **kwargs):
            created.append(1)
            super().__init__(*args, **kwargs)

    onnxruntime.InferenceSession = Counting

    from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
    from pathlib import Path
    ONNXMiniLM_L6_V2.DOWNLOAD_PATH = Path(sys.argv[1])

    import mempalace.embedding as embedding
    vectors = embedding.get_embedding_function(model="minilm")(input=["one model, many callers"])
    print(json.dumps({"sessions": len(created), "head": [float(x) for x in vectors[0][:4]]}))
    """
)


def test_n_processes_share_one_model_load_through_the_service(socket_path, tmp_path):
    """Each embedding process loads the model itself; with the switch they load none."""
    _needs_minilm()
    import onnxruntime

    def run(count, switch):
        env = {
            **os.environ,
            "MEMPALACE_CONFIG_DIR": str(tmp_path / "config"),
            "MEMPALACE_EMBEDDING_DEVICE": "cpu",
            "MEMPALACE_EMBEDDING_THREADS": "1",
            "MEMPALACE_EMBEDDING_SERVICE": switch,
        }
        command = [sys.executable, "-c", _COUNT_SESSIONS, str(_minilm_model_dir().parent)]
        processes = [
            subprocess.Popen(command, env=env, stdout=subprocess.PIPE, text=True)
            for _ in range(count)
        ]
        return [json.loads(process.communicate(timeout=120)[0]) for process in processes]

    service_sessions = []
    real_session = onnxruntime.InferenceSession

    class Counting(real_session):
        def __init__(self, *args, **kwargs):
            service_sessions.append(1)
            super().__init__(*args, **kwargs)

    onnxruntime.InferenceSession = Counting
    try:
        with EmbeddingService(socket_path, model="minilm", device="cpu") as service:
            shared = run(3, socket_path)
            stats = service.stats()
    finally:
        onnxruntime.InferenceSession = real_session
    in_process = run(1, "off")

    assert [result["sessions"] for result in in_process] == [1]  # the cost per process today
    assert [result["sessions"] for result in shared] == [0, 0, 0]
    assert len(service_sessions) == 1 and stats["model_loads"] == 1
    assert stats["texts"] == 3
    assert all(result["head"] == in_process[0]["head"] for result in shared)

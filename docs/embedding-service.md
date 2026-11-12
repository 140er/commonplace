# Shared embedding service

Every MemPalace process that embeds text loads its own copy of the embedding
model: each MCP worker, each `mempalace mine`, each CLI search. On a machine
that runs several of them, that is the same model in memory several times.

The embedding service holds the model once and serves it to every process on
the machine. It is **opt-in**: with the switch off (the default) nothing
changes and each process embeds in-process as before.

## Use it

Start the service, then point the callers at its socket:

```bash
python -m mempalace.embedding_service --socket /run/mempalace/embedding.sock

export MEMPALACE_EMBEDDING_SERVICE=/run/mempalace/embedding.sock
mempalace search "why did we switch to GraphQL"
```

| Setting | Env var | Values |
|---|---|---|
| `embedding_service` in `config.json` | `MEMPALACE_EMBEDDING_SERVICE` | unset / `off` (default), `on` (`embedding.sock` in the config directory), or an absolute socket path |
| | `MEMPALACE_EMBEDDING_SERVICE_TIMEOUT` | seconds a caller waits for an answer (default 120) |

The service embeds with the configured `embedding_model` and
`embedding_device` (override with `--model` / `--device`). It serves the local
ONNX models, `minilm` and `embeddinggemma`. `embeddinggemma2` and
`openai-compat` are never routed to it and keep working as before.

Hosted workers receive `MEMPALACE_EMBEDDING_SERVICE` from the gateway's
environment. Their home and config directories differ per workspace, so give
them an absolute socket path, not `on`.

## Local only

The transport is a Unix domain socket, created owner-only (mode 0600); on
Linux the service also checks that the peer runs as the same user. There is
no network listener. Texts go from the caller to the service process on the
same machine and nowhere else, and the service neither logs nor keeps them.

The vectors are the ones the in-process path produces: the service runs the
same embedding function and returns its float32 values unchanged. A palace
cannot tell which path wrote a vector, so the switch can be turned on and off
without re-embedding. (Requests over 256 texts are split. MiniLM pads to a
fixed width, so its result is unchanged; `embeddinggemma` regroups documents
by length per request, which can move a value by one float32 rounding step,
the same as its in-process sub-batching.)

## When the service is down

A caller gets `Embedding service unavailable`, naming the socket and the
reason. It does **not** load the model itself (that would bring back the
per-process copies), and it never falls back to another model or a remote
provider.

- **Search** degrades to lexical (keyword) ranking and says so: the result
  carries `degraded: "lexical"`, `embedding_service_unavailable: true` and a
  `warning`; hits have no vector distance. The CLI prints the reason on
  stderr. Filters still apply. Backends without a bounded lexical index
  return the error instead.
- **Writes** (mining, `add_drawer`, anything that embeds new text) are refused
  with the error. Nothing is written without its vector; retry once the
  service is back.
- **Reads that do not embed** (get, list, status) keep working.

After a failed connection a caller fails fast for three seconds before it
tries the socket again. It recovers by itself when the service returns.

A service asked for a model it does not hold refuses the request; it never
embeds with a different model than the caller's palace records.

## Memory bounds

The service's memory does not grow with the number of callers. It is bounded
by:

| Limit | Flag | Default |
|---|---|---|
| Models loaded | | 1 |
| Concurrent inference runs | `--max-concurrent` | 2 |
| Open connections (more wait in the kernel backlog) | `--max-connections` | 8 |
| Request size | `--max-request-bytes` | 8 MiB |
| Texts per request | `--max-texts` | 1024 |

Inference working memory scales with `--max-concurrent`; lower it to 1 to
trade throughput for the smallest footprint.

`benchmarks/embedding_service_memory.py` compares N processes with and
without the service. On a 4-process run (MiniLM, CPU, one query and one
64-document batch each): 2,466 MiB with a model in every process, 1,255 MiB
with the service (236 MiB across the four callers plus 1,019 MiB in the
service), and one model load instead of four.

## Status

```python
from mempalace.embedding_service import EmbeddingServiceClient
EmbeddingServiceClient("/run/mempalace/embedding.sock", "minilm").ping()
```

returns the model, dimension, limits, resident memory and counters
(`model_loads`, `requests`, `texts`, `errors`).

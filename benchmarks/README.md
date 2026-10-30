# MemPalace Benchmarks — Reproduction Guide

Run the exact same benchmarks we report. Clone, install, run.

## Setup

```bash
git clone https://github.com/MemPalace/mempalace.git
cd mempalace
uv sync --extra dev   # or: pip install -e ".[dev]"
```

## Benchmark 1: LongMemEval (500 questions)

Tests retrieval across ~53 conversation sessions per question. The standard benchmark for AI memory.

```bash
# Download data
mkdir -p /tmp/longmemeval-data
curl -fsSL -o /tmp/longmemeval-data/longmemeval_s_cleaned.json \
  https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s_cleaned.json

# Run (raw mode — our headline 96.6% result)
python benchmarks/longmemeval_bench.py /tmp/longmemeval-data/longmemeval_s_cleaned.json

# Run with AAAK compression (84.2%)
python benchmarks/longmemeval_bench.py /tmp/longmemeval-data/longmemeval_s_cleaned.json --mode aaak

# Run with room-based boosting (89.4%)
python benchmarks/longmemeval_bench.py /tmp/longmemeval-data/longmemeval_s_cleaned.json --mode rooms

# Quick test on 20 questions first
python benchmarks/longmemeval_bench.py /tmp/longmemeval-data/longmemeval_s_cleaned.json --limit 20

# Turn-level granularity
python benchmarks/longmemeval_bench.py /tmp/longmemeval-data/longmemeval_s_cleaned.json --granularity turn
```

**Expected output (raw mode, full 500):**
```
Recall@5:  0.966
Recall@10: 0.982
NDCG@10:   0.889
Time:      ~5 minutes on Apple Silicon
```

## Benchmark 2: LoCoMo (1,986 QA pairs)

Measures retrieval of labelled evidence across 10 long conversations (19-32 sessions each, 400-600 dialog turns), rather than generated-answer accuracy.

**LoCoMo reranking:** `--llm-rerank` reorders IDs after retrieval has already
been cut to top-k. `Avg Recall` checks membership and ignores ordering, so the
flag cannot improve this metric for the same retrieved set. It adds LLM latency
and any provider cost when executed. Omit it for recall-only comparisons and
keep mode, top-k, granularity and embedding model fixed. At top-50 in unfiltered
raw or hybrid session retrieval, every session fits in the retrieved set,
making recall structurally saturated.

```bash
# Clone LoCoMo
git clone https://github.com/snap-research/locomo.git /tmp/locomo

# Run (session granularity, top-10 — our 60.3% result)
python benchmarks/locomo_bench.py /tmp/locomo/data/locomo10.json --granularity session --top-k 10

# Dialog granularity (harder — 48.0%)
python benchmarks/locomo_bench.py /tmp/locomo/data/locomo10.json --granularity dialog --top-k 10

# Higher top-k (session granularity; every session fits under top-50)
python benchmarks/locomo_bench.py /tmp/locomo/data/locomo10.json --granularity session --top-k 50

# Quick test on 1 conversation
python benchmarks/locomo_bench.py /tmp/locomo/data/locomo10.json --granularity session --top-k 10 --limit 1
```

**Expected output (session, top-10, full 10 conversations):**
```
Avg Recall: 0.603
Temporal:   0.692
Time:       ~2 minutes
```

The earlier guide reported 77.8% at top-50 without recording its granularity;
that historical value is not the expected result of the session top-50 command
above. The CLI defaults to top-50, so top-10 comparisons require `--top-k 10`.

## Benchmark 3: ConvoMem (Salesforce, 75K+ QA pairs)

Tests six categories of conversational memory. Downloads from HuggingFace automatically.

```bash
# Run all categories, 50 items each (our 92.9% result)
python benchmarks/convomem_bench.py --category all --limit 50

# Single category
python benchmarks/convomem_bench.py --category user_evidence --limit 100

# Quick test
python benchmarks/convomem_bench.py --category user_evidence --limit 10
```

**Categories available:** `user_evidence`, `assistant_facts_evidence`, `changing_evidence`, `abstention_evidence`, `preference_evidence`, `implicit_connection_evidence`

**Expected output (all categories, 50 each):**
```
Avg Recall: 0.929
Assistant Facts: 1.000
User Facts:      0.980
Time:            ~2 minutes
```

## Benchmark 4: Fact recall (synthetic, offline)

Do facts work as an index into the full chats? A seeded corpus of chat threads is stored verbatim, facts pointing at exact message ranges are filed into the knowledge graph, and each question is answered from the fact index alone, then checked against the message it cites. It needs no download, no model and no network.

```bash
python benchmarks/fact_recall_bench.py              # metrics
python benchmarks/fact_recall_bench.py --threads 200 --json
```

| Metric | Bar | Meaning |
|---|---|---|
| `recall_at_1` | 1.0 | the expected value is the index's first answer |
| `stale_rate` | 0.0 | a replaced value still reads as current |
| `source_hit_rate` | 1.0 | the cited range contains the answer verbatim |
| `old_thread_recall` | 1.0 | facts said only in the oldest thread |
| `private_leaks` | 0 | facts a restricted reader may not see |
| `merge_rate` | 1.0 | a restated fact is one fact with every source |

`tests/test_fact_recall_eval.py` holds these bars in the normal test run and checks each metric fails when the behavior it measures breaks. Today facts are filed by an oracle (`--saver oracle`, as an agent calling `fact_save`); handoff-time extraction will plug in as another saver.

## What Each Benchmark Tests

| Benchmark | What it measures | Why it matters |
|---|---|---|
| **LongMemEval** | Can you find a fact buried in 53 sessions? | Tests basic retrieval quality — the "needle in a haystack" |
| **LoCoMo** | Can you retrieve labelled evidence across conversations over weeks? | Measures evidence coverage for multi-hop and temporal questions |
| **ConvoMem** | Does your memory system work at scale? | Tests all memory types: facts, preferences, changes, abstention |

## Results Files

Raw results are in `benchmarks/results_*.jsonl` and `benchmarks/results_*.json`. Each file contains every question, every retrieved document, and every score — fully auditable.

## Requirements

- Python 3.10+
- `chromadb` (the only dependency)
- ~300MB disk for LongMemEval data
- ~5 minutes for each full benchmark run
- No API key. No internet during benchmark (after data download). No GPU.

## Next Benchmarks (Planned)

- **Scale testing** — ConvoMem at 50/100/300 conversations per item
- **Hybrid AAAK** — search raw text, deliver AAAK-compressed results
- **End-to-end QA** — retrieve + generate answer + measure F1 (needs LLM API key)

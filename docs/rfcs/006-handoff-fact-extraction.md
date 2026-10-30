# Handoff-time fact extraction

Handoff currently preserves full chats and mirrors chunks, but it never files atomic facts from those chats. This step adds a deterministic local extraction pass after durable thread storage. Verbatim ThreadStore messages remain the source of truth; extracted facts are a secondary index with exact stored-thread and message-range pointers.

Design before implementation:

- Extraction runs in its own background job, independently of embedding/mirroring and successor launch. Handoff-off paths do no extraction work. Failure to index a fact does not endanger chat storage or block the hook.
- The extractor accepts messages and trusted source metadata only. It never receives eval ground truth. Explicit named-entity relations produce structured triples; first-person decision/preference candidates remain in the verbatim chat until their speaker can be verified. Proper-name, complete-value assertions are required; past-tense employers and ambiguous location prose are unsupported. Ambiguous subjects, negated/hypothetical assertions, quoted instructions, and ungrounded dates are skipped rather than invented.
- Every automatically extracted fact stays in the source mirror's `private_threads` wing with private sensitivity. The lossless store uses the separate `threads` wing. Text cannot select a shared wing or lower sensitivity. Follow-up source reads retain the existing threads:read and source-wing checks.
- Writes use the existing fact_save policy and privacy path, including kg:write, readable/writable wing requirements, server provenance, and refusal of secrets. Extraction itself makes no network or model calls. Optional LLM refinement is outside this PR.
- Repeated handoffs and append retries must not duplicate facts or source pointers. Keep extraction progress with the durable thread store, and move it forward only after a message's candidates have been handled successfully. Retrying after failure revisits unfinished work; the existing fact-save merge/supersede transaction handles duplicate sources and older history.
- Validation covers source-exact extraction from messages, changed values and chronological filing, repeated/extended handoffs, independent mirror/extractor failures, privacy and policy denial, secret and injection-like content, scoped source opening, and the old-thread recall case. The step-4 oracle gate remains unchanged; extraction gets its own grounded fixture/adapter and reports coverage rather than borrowing planted facts.

The deterministic pass is deliberately conservative. Unsupported wording remains searchable in the verbatim chat and drawer fallback. Step 6 will rank facts ahead of drawers while preserving that fallback.

The single Opus fix round adds precision scoring over every filed subject, adversarial wrapper/qualifier fixtures, opaque fact event references, scheduler failure isolation, and production shutdown of background indexing before permanent worker shutdown. Speaker attribution is deferred rather than inferred from a token owner or transcript role.

### Authorization-only provenance spelling recognition

Provenance authorization keeps the existing ordinary thread/private reference scan
and at most four strict percent-decoding rounds, refusing controls and undecodable
citations. A separate NFKC authorization copy recognizes only complete protected
shapes: `capture:<actor>:<source>:<identity>`,
`import-private:<user>:<source>:<identity>`, and
`capture-fork:<user>:<32 lowercase hex characters>`. ASCII space or NBSP may follow
structural colons; the namespace colon alone may be omitted when the actor starts
with an ASCII letter or digit and contains a digit or underscore (as production
user/connection ids do). This excludes prose actors such as `ing-notes` in
`capturing-notes:codex:v2`. New shadows begin at the field start or after
space, NBSP, `/`, `?`, `&`, `#`, `=`, `(`, `<`, `"` or `'`, or inside an already
recognized reference run. Every namespace start in a run is checked, including
overlapping ordinary and spelled candidates with no intervening boundary. A run
must begin before a candidate to waive its boundary check: a non-boundary
`notes.capture-fork:<user>:<digest>)` retains ordinary whole-run proof rather than
gaining a digest-only shadow. Internal separators stay mandatory; actor/source
components exclude whitespace, colons, path separators and
query delimiters. Identity suffixes retain internal colons and complete `#sha256:`
qualifiers through the existing whitespace/`?` boundary for capture/import ids.
Fork identities end after exactly 32 lowercase hex characters; any following
character (including another hex digit, punctuation, wrapper, text or a hash
suffix) is citation context and cannot hide the protected digest. Any protected
reference embedded in that context requires its own readable proof; proving the
leading fork never proves or suppresses a later candidate. Both ordinary
and spelled forks authorize that digest while preserving the complete citation
bytes. Fewer than 32 lowercase hex characters and uppercase digests do not create
new fork shadows. This does not detect arbitrary Unicode insertion.

Capture source labels are exactly `claude-code` and `codex`. Private import labels
are exactly `claude-code`, `codex`, `cursor`, `t3`, `grok-bot`, `chatgpt`, `claude-ai`
and the grant-only `native-memory`. New shadow namespace/source matching ignores
case and forms lowercase producer labels before lookup; actor and identity case
remain literal. Existing ordinary legacy tokens retain authorization outside these
new domains. A complete shadow authorizes the entire spaced reference rather than
an incomplete ordinary prefix split at an internal structural colon. Embedded
thread references are still independently authorized.

Every recognized reference requires readable local proof in the caller's
workspace. Missing and unreadable production-shaped shadows receive the same
not-found write response as canonical citations. Readable fork spellings are
readable with trailing citation context only when every embedded reference is
also proved. Capture/import identities remain literal,
including trailing punctuation: a spaced spelling with an appended wrapper can
therefore fail closed when no such exact identity exists. Punctuation is not
stripped from those variable-length ids, since it can be part of a real identity.
Read projections omit unproved citation fields while retaining D1-readable fact text. Authorization does not rewrite stored/displayed citation
bytes or subject, predicate, object and content. Ordinary URL/path citations such
as `https://example.test/capturing-notes` and `capture-notes.md`, unshaped trigger
words, and readable canonical fork citations remain compatible. A complete
protected reference embedded in a URL/path still requires proof. Existing
navigable-URL validation applies to new `source_url` writes; KG exposes that
citation as `origin_link`, and file citations in source observation rows.

The decoded field and its NFKC authorization copy each have a hard cap of
16,384 Unicode characters, counting decoded padding before trimming. An over-limit
field is refused in full, never scanned as a truncated prefix. Gateway fact reads
omit only the citation while retaining readable fact text and stored bytes.
KG writes refuse the entire operation before storage with the explicit error
`<field>: citation length limit of 16384 Unicode characters exceeded (decoded or NFKC-normalized)`.
The pending fact is not saved or rewritten; callers can correct the citation and
resubmit the same verbatim fact. Existing triples and source observations remain
unchanged; refused writes never reach a worker mutation. Protected-reference,
control and decoding refusals retain their existing
not-found responses; a length error discloses only the caller's field and limit.
This deliberately also refuses over-limit readable citations, including token-free
legacy files; citations within the cap retain their existing byte-preservation
rules. New URL writes also retain their existing 2,048-character validation.

This is a gateway fact-provenance policy, not a storage-wide citation limit.
Drawer writes retain their existing unchecked `source_file` storage; ordinary
gateway projections scrub top-level provenance fields. Import-proved non-fact
records remain exempt for the `source_path` and nested `metadata.source_file`
aliases constructed after exact import-range proof. Those aliases retain their
original citation bytes. This exception does not grant access to unproved words.

The bound covers both overlapping suffix output and omitted-colon near misses.
For input length n and fixed cap C = 16,384, four strict decoding/control passes
cost O(n) time and memory; candidate scanning costs at most O(C²) time and memory.
No per-start suffix work occurs above either cap, including NFKC expansion.
Thread tokens are deduplicated on insertion, preserving first-occurrence order
without accumulating repeated ids from every overlapping shadow.
Deterministic behavior regressions assert zero candidate enumeration above either
cap and verify public refusal, preservation and exact-boundary compatibility.
The raw decode/control passes remain input-proportional before the decoded cap;
there is no per-response character budget.

Wall-clock and RSS measurements use the serial, informational `timing` lane;
plain pytest excludes that marker by default, matching the behavior lane. Explicit
`-m timing` selects measurements. Performance never gates a PR, merge or release.
Isolated subprocesses report cap-sized and 1 MiB repeated namespaces, omitted-colon
near misses, fullwidth spellings and mixed namespace/thread-id expansion against
3-second scan and 128 MiB peak RSS reference budgets. Their 256 MiB address-space
limit and 300-second hang guard contain bad measurements; budget misses, memory
exhaustion and hang-guard results are recorded as information, not test failures.

This changes reference recognition only: typed associations, provenance aliases,
plain-file association proof and search-index progress retain their existing
contracts.

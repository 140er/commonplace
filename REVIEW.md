# Review guide

Commonplace is a multi-tenant gateway (accounts, workspaces, scoped tokens, boards, rules) in front of MemPalace,
a verbatim local-first memory. What must never break: stored words stay verbatim, one workspace never sees
another's data, a token never gets more than its policy grants, and existing data survives every change.

Severity tags: **Important** = blocks merge. **Nit** = mention, never blocks. **Pre-existing** = problem already on the base branch; note it, never block this PR for it.

## Important
- Tenant isolation: every new or changed route, MCP tool, search, export or cache must scope by workspace and by
  the token's read/write wings and scopes. Look for a lookup by bare id, a cache keyed without workspace, a
  member or `read_wings` restriction that a list, search or direct-object path skips.
- Route classification: a new HTTP route must authenticate before it acts and be classified in
  `tests/test_commonplace_public_surface.py`; a person-only action must refuse an agent token, and a write must
  not be reachable by a read-only token.
- Verbatim promise: no summarising, truncating, normalising or re-encoding of stored user text on the write
  path. Display-only cutting (Memory page excerpts) must leave the stored drawer untouched.
- Data safety: storage migrations, store switches, backup, restore, encryption and prune code must be
  incremental, crash-safe and never delete or overwrite the only copy. A failed step must leave the old data
  readable. Check that a full disk or a half-finished run fails cleanly.
- Secrets and tokens: no token, key, cookie or email link in logs, errors, audit rows, fixtures or responses
  (use `commonplace.redact`); tokens are stored hashed and shown once.
- Auth and sign-in: the email link must not create a session on GET, links are single-use and expire, return
  paths stay on safe app paths, social providers are off unless fully configured.
- Concurrency and processes: SQLite stores opened without the documented lock discipline, work done while
  holding the gateway lock (it starves other requests), unbounded threads, queues or caches, and any code that
  stops a process by name or pattern instead of a PID it started. Per-workspace workers must stop when idle.
- Local-first: no new network call, telemetry or external provider on a default path; external services are
  explicit opt-in per owner.
- Budgets and limits: request size, rate limits, plan quotas (storage, voice) and the hook/startup time budgets
  must not be loosened or bypassed; a refusal keeps a retryable, actionable answer.
- Docs obligations: a change to a module's paths updates its block under `docs/modules/` and a `docs/system/`
  note; a new root or `commonplace/` entry is listed in exactly one block. Never use a docs waiver without an
  owner-approved id in `SYSTEM.md`.
- Bug fixes need a regression test that fails on the base and passes on the head; new behaviour needs a test
  of the behaviour, not of the wiring. No `sleep`, polling or retry to make a test pass.

## Nit
- Ruff style and naming beyond what `ruff check` and `ruff format` already enforce.
- Wording in UI text and docs: plain language, no jargon the owner would not use.
- Missing screenshot for a purely visual change when the description is clear.

## Pre-existing
- `CHANGELOG.md`, `README.md` and `REPORT.md` still use the older MemPalace name and some stale figures.
- Wall-clock budget tests (`timing` marker) are informational and can fail on a busy host; never block on them.
- Long modules such as `commonplace/gateway.py` and `commonplace/boards/service.py` carry accumulated debt.
- Report any other pre-existing problem as Pre-existing, not Important.

## Rules the reviewer enforces
- The PR ran the verify skill (`.claude/skills/verify/SKILL.md`) or says why it could not; claims of "verified" need evidence in the PR body.
- A PR never changes the checks or rules that judge it (REVIEW.md, gate/check config, CI recipes, test thresholds) to make itself pass. The gate reads REVIEW.md from the base branch, never from the PR head.
- No secrets, tokens or credentials in code, logs, fixtures, commits or PR text.

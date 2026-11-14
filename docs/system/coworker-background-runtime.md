### Coworker background runtime

The worker starts with the chat installation and stops before database close.
Four bounded front threads rotate registered workspaces and preserve each
conversation/bot's order; at most two calls enter one configured provider endpoint.
Model requests have a 3.5 second timeout, bounded input/output and atomic per-day
attempt accounting. This is a responsiveness limit, not measured real-provider
latency. An endpoint circuit opens after repeated failures; no silent provider
fallback or automatic failed-turn replay occurs.
Board-less conversations use local providers only; inherited workspace hosted
settings are refused with `needs_configuration` before context preparation,
credential resolution or provider calls. Hosted conversations associated with
a board still require the existing project Turn on; no coworker Turn on exists.

The internal Store.runtime_job rechecks the exact originating session, live
membership, bot binding, running state and memory visibility before context is
sent and before publication. Its session digest is never in public job views.
Only the conversation's bounded history and explicit verbatim memory enter model
context. Recall ranks the at-most-100 visible memories by query word overlap
with recency as a tie breaker, examines at most 32 ranked candidates, and includes
at most five whole records totaling 4000 characters; no record is summarized
or cut to fit. This bounded lexical selection does not promise exhaustive recall. No workspace-wide memory search or tool execution is granted to model
output. Explicit `/task TITLE` plus optional exact body admits a canonical todo
card on the conversation's authorized board; task_created acknowledges admission,
not task completion. When existing orchestration and a runner are configured,
the internal person-owner start_chat_task checks the opaque canonical origin,
current card/configuration and caller authority, then uses the canonical fenced
runner admission with a stable hashed origin key. No broad control/boards lock
spans admission. Replay reuses the same runner job; configuration changes conflict.
Unconfigured or refused admission preserves the real card and a typed reason.
Runner queued/leased/starting/started/uncertain/cancelled facts are distinct from
the finished front turn; the UI names these admission outcomes and links the
canonical task for current progress. This retains the existing runner admission
snapshot/fence semantics, not a new instantaneous revocation guarantee.
Execution still requires existing accepted runner configuration and actual worker
launch. These tests exercise durable admission, not a real runner process. A lost acknowledgement does not trigger
blind replay. Tests use blocked synthetic providers; no production model latency
or real worker launch is claimed by these checks.

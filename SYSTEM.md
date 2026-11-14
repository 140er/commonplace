# System

Decisions, numbers, stores and accepted residuals that hold across modules.
`MODULES.md` (the blocks are in `docs/modules/`) says which module owns each
path; this file, with the sections in `docs/system/`, says what the whole
system promises. A change that alters a decision, a number, a store or a
residual updates this file in the same change; `docs-check` enforces that
(see "Keeping this file current").

Labels: **M** = measured (from code or a run), **T** = target, **G** = guess
that rollout must measure. `file:line` references are to the tree in which the
entry was written.

## Decisions

### Coworker UI lifecycle

Moved to [`docs/system/coworker-ui-lifecycle.md`](docs/system/coworker-ui-lifecycle.md); edit it there.

### Coworker background runtime

Moved to [`docs/system/coworker-background-runtime.md`](docs/system/coworker-background-runtime.md); edit it there.

### Coworker conversation storage and REST boundary

Moved to [`docs/system/coworker-conversation-storage-and-rest-boundary.md`](docs/system/coworker-conversation-storage-and-rest-boundary.md); edit it there.

### Coworker task intake idempotency

Moved to [`docs/system/coworker-task-intake-idempotency.md`](docs/system/coworker-task-intake-idempotency.md); edit it there.

### Canonical work progress in coworker conversations

Moved to [`docs/system/canonical-work-progress-in-coworker-conversations.md`](docs/system/canonical-work-progress-in-coworker-conversations.md); edit it there.

### Targeted coworker controls

Moved to [`docs/system/targeted-coworker-controls.md`](docs/system/targeted-coworker-controls.md); edit it there.

### Compute management REST and durable authority

Moved to [`docs/system/compute-management-rest-and-durable-authority.md`](docs/system/compute-management-rest-and-durable-authority.md); edit it there.

### Compute management interface

Moved to [`docs/system/compute-management-interface.md`](docs/system/compute-management-interface.md); edit it there.

### Passive gateway cache discovery component

Moved to [`docs/system/passive-gateway-cache-discovery-component.md`](docs/system/passive-gateway-cache-discovery-component.md); edit it there.

### Typed helper protocol and script runtime components

Moved to [`docs/system/typed-helper-protocol-and-script-runtime-components.md`](docs/system/typed-helper-protocol-and-script-runtime-components.md); edit it there.

### Credential custody and pinned SSH transport components

Moved to [`docs/system/credential-custody-and-pinned-ssh-transport-components.md`](docs/system/credential-custody-and-pinned-ssh-transport-components.md); edit it there.

### Coworker task dispatch component

Moved to [`docs/system/coworker-task-dispatch-component.md`](docs/system/coworker-task-dispatch-component.md); edit it there.

### Compute control startup configuration

Moved to [`docs/system/compute-control-startup-configuration.md`](docs/system/compute-control-startup-configuration.md); edit it there.

### Compute execution component

Moved to [`docs/system/compute-execution-component.md`](docs/system/compute-execution-component.md); edit it there.

### Compute discovery service component

Moved to [`docs/system/compute-discovery-service-component.md`](docs/system/compute-discovery-service-component.md); edit it there.

### Compute operator recovery component

Moved to [`docs/system/compute-operator-recovery-component.md`](docs/system/compute-operator-recovery-component.md); edit it there.

### Workspace page controllers for Compute and Coworkers

Moved to [`docs/system/workspace-page-controllers-for-compute-and-coworkers.md`](docs/system/workspace-page-controllers-for-compute-and-coworkers.md); edit it there.

### Durable Compute execution component

Moved to [`docs/system/durable-compute-execution-component.md`](docs/system/durable-compute-execution-component.md); edit it there.

### Durable passive discovery

Moved to [`docs/system/durable-passive-discovery.md`](docs/system/durable-passive-discovery.md); edit it there.

### Immutable script bundles and confined target helper

Moved to [`docs/system/immutable-script-bundles-and-confined-target-helper.md`](docs/system/immutable-script-bundles-and-confined-target-helper.md); edit it there.

### Isolated Compute custody and SSH transport

Moved to [`docs/system/isolated-compute-custody-and-ssh-transport.md`](docs/system/isolated-compute-custody-and-ssh-transport.md); edit it there.

### Compute discovery domain boundary

Moved to [`docs/system/compute-discovery-domain-boundary.md`](docs/system/compute-discovery-domain-boundary.md); edit it there.


### Inert area configuration preview

Moved to [`docs/system/inert-area-configuration-preview.md`](docs/system/inert-area-configuration-preview.md); edit it there.

### Legacy client memory safety stop

Moved to [`docs/system/legacy-client-memory-safety-stop.md`](docs/system/legacy-client-memory-safety-stop.md); edit it there.

### Runner-start void semantics and limits

Moved to [`docs/system/runner-start-void-semantics-and-limits.md`](docs/system/runner-start-void-semantics-and-limits.md); edit it there.

### Rules initialization deadline

Moved to [`docs/system/rules-initialization-deadline.md`](docs/system/rules-initialization-deadline.md); edit it there.

### Rules for reviewers and at session start

Moved to [`docs/system/rules-for-reviewers-and-at-session-start.md`](docs/system/rules-for-reviewers-and-at-session-start.md); edit it there.

### Operator-blind token slots: primitives with production locked

Moved to [`docs/system/operator-blind-token-slots-primitives-with-production.md`](docs/system/operator-blind-token-slots-primitives-with-production.md); edit it there.


### Compact rules index

Moved to [`docs/system/compact-rules-index.md`](docs/system/compact-rules-index.md); edit it there.

### Chroma session rebuilds

Moved to [`docs/system/chroma-session-rebuilds.md`](docs/system/chroma-session-rebuilds.md); edit it there.

### Email session cookie domain migration

Moved to [`docs/system/email-session-cookie-domain-migration.md`](docs/system/email-session-cookie-domain-migration.md); edit it there.

### Release email login readiness

Moved to [`docs/system/release-email-login-readiness.md`](docs/system/release-email-login-readiness.md); edit it there.

### Restricted-origin release

Moved to [`docs/system/restricted-origin-release.md`](docs/system/restricted-origin-release.md); edit it there.

### Owner manual assignment starts

Moved to [`docs/system/owner-manual-assignment-starts.md`](docs/system/owner-manual-assignment-starts.md); edit it there.

### Privacy scanner timing checks

Moved to [`docs/system/privacy-scanner-timing-checks.md`](docs/system/privacy-scanner-timing-checks.md); edit it there.

### Rules initialization timing checks

Moved to [`docs/system/rules-initialization-timing-checks.md`](docs/system/rules-initialization-timing-checks.md); edit it there.

### Orchestrator no-progress wake

Moved to [`docs/system/orchestrator-no-progress-wake.md`](docs/system/orchestrator-no-progress-wake.md); edit it there.

### Orchestrator trigger sweep never wedges

Moved to [`docs/system/orchestrator-trigger-sweep-never-wedges.md`](docs/system/orchestrator-trigger-sweep-never-wedges.md); edit it there.

### Board store opens take no write lock (#428)

Moved to [`docs/system/board-store-opens-take-no-write-lock-428.md`](docs/system/board-store-opens-take-no-write-lock-428.md); edit it there.

### Card context in a start

Moved to [`docs/system/card-context-in-a-start.md`](docs/system/card-context-in-a-start.md); edit it there.

### Runner wings and the owner runner block

Moved to [`docs/system/runner-wings-and-the-owner-runner-block.md`](docs/system/runner-wings-and-the-owner-runner-block.md); edit it there.

### Workspace invites

Moved to [`docs/system/workspace-invites.md`](docs/system/workspace-invites.md); edit it there.

### Run ledger

Moved to [`docs/system/run-ledger.md`](docs/system/run-ledger.md); edit it there.

### Raw test runs on a shared test host

Moved to [`docs/system/raw-test-runs-on-a-shared-test-host.md`](docs/system/raw-test-runs-on-a-shared-test-host.md); edit it there.

### Mirror and search-index test waits

Moved to [`docs/system/mirror-and-search-index-test-waits.md`](docs/system/mirror-and-search-index-test-waits.md); edit it there.

### Console activity query checks

Moved to [`docs/system/console-activity-query-checks.md`](docs/system/console-activity-query-checks.md); edit it there.

### Agent questions

Moved to [`docs/system/agent-questions.md`](docs/system/agent-questions.md); edit it there.

### Completion reviewer

Moved to [`docs/system/completion-reviewer.md`](docs/system/completion-reviewer.md); edit it there.


### Project seat wake recommendations

Moved to [`docs/system/project-seat-wake-recommendations.md`](docs/system/project-seat-wake-recommendations.md); edit it there.

### Card-lifecycle rule replay

Moved to [`docs/system/card-lifecycle-rule-replay.md`](docs/system/card-lifecycle-rule-replay.md); edit it there.

### Restricted-origin card content

Moved to [`docs/system/restricted-origin-card-content.md`](docs/system/restricted-origin-card-content.md); edit it there.

### Token-slot refusal verification

Moved to [`docs/system/token-slot-refusal-verification.md`](docs/system/token-slot-refusal-verification.md); edit it there.


### Browser GitHub connection (experimental, default off)

Moved to [`docs/system/browser-github-connection-experimental-default-off.md`](docs/system/browser-github-connection-experimental-default-off.md); edit it there.

### Generic agent skills

Moved to [`docs/system/generic-agent-skills.md`](docs/system/generic-agent-skills.md); edit it there.

### Optional agent orchestration

Moved to [`docs/system/optional-agent-orchestration.md`](docs/system/optional-agent-orchestration.md); edit it there.

### Optional project completion and stuck reports

Moved to [`docs/system/optional-project-completion-and-stuck-reports.md`](docs/system/optional-project-completion-and-stuck-reports.md); edit it there.


### Orchestrator config as code

Moved to [`docs/system/orchestrator-config-as-code.md`](docs/system/orchestrator-config-as-code.md); edit it there.

### Foreground refresh rendering

Moved to [`docs/system/foreground-refresh-rendering.md`](docs/system/foreground-refresh-rendering.md); edit it there.

### Starts on a configured board

Moved to [`docs/system/starts-on-a-configured-board.md`](docs/system/starts-on-a-configured-board.md); edit it there.

### Bots from a person's own app (`bots:chat`, `bots:confirm`)

Moved to [`docs/system/bots-from-a-person-s-own-app-bots-chat-bots-confirm.md`](docs/system/bots-from-a-person-s-own-app-bots-chat-bots-confirm.md); edit it there.

### Owner-granted answers from the owner's own apps (`cards:decide`)

Moved to [`docs/system/owner-granted-answers-from-the-owner-s-own-apps-cards.md`](docs/system/owner-granted-answers-from-the-owner-s-own-apps-cards.md); edit it there.

### Explicit imported-history reader consent

Moved to [`docs/system/explicit-imported-history-reader-consent.md`](docs/system/explicit-imported-history-reader-consent.md); edit it there.


### Triggers

Moved to [`docs/system/triggers.md`](docs/system/triggers.md); edit it there.

### Durable orchestrator

Moved to [`docs/system/durable-orchestrator.md`](docs/system/durable-orchestrator.md); edit it there.

### Thread-search ranking

Moved to [`docs/system/thread-search-ranking.md`](docs/system/thread-search-ranking.md); edit it there.

### Owner-configured task types on cards (slice 1)

Moved to [`docs/system/owner-configured-task-types-on-cards-slice-1.md`](docs/system/owner-configured-task-types-on-cards-slice-1.md); edit it there.

### Goals tree in card context

Moved to [`docs/system/goals-tree-in-card-context.md`](docs/system/goals-tree-in-card-context.md); edit it there.

### Opt-in blockers, waiting reasons and split delivery

Moved to [`docs/system/opt-in-blockers-waiting-reasons-and-split-delivery.md`](docs/system/opt-in-blockers-waiting-reasons-and-split-delivery.md); edit it there.

### Foreground refresh rendering

Moved to [`docs/system/foreground-refresh-rendering-2.md`](docs/system/foreground-refresh-rendering-2.md); edit it there.

### Routines create cards on a schedule

Moved to [`docs/system/routines-create-cards-on-a-schedule.md`](docs/system/routines-create-cards-on-a-schedule.md); edit it there.

### Tool policy: Allowed, Ask first or Off per action

Moved to [`docs/system/tool-policy-allowed-ask-first-or-off-per-action.md`](docs/system/tool-policy-allowed-ask-first-or-off-per-action.md); edit it there.

### Webhook dispatcher recovery

Moved to [`docs/system/webhook-dispatcher-recovery.md`](docs/system/webhook-dispatcher-recovery.md); edit it there.

### Revoked-token socket guard

Moved to [`docs/system/revoked-token-socket-guard.md`](docs/system/revoked-token-socket-guard.md); edit it there.

### Design before code

Moved to [`docs/system/design-before-code.md`](docs/system/design-before-code.md); edit it there.

### Defaults chosen for slice 1

Moved to [`docs/system/defaults-chosen-for-slice-1.md`](docs/system/defaults-chosen-for-slice-1.md); edit it there.

### Live chat delivery state

Moved to [`docs/system/live-chat-delivery-state.md`](docs/system/live-chat-delivery-state.md); edit it there.

### Board coordinator settings

Moved to [`docs/system/board-coordinator-settings.md`](docs/system/board-coordinator-settings.md); edit it there.


### Thread-derived mutations and citations

Moved to [`docs/system/thread-derived-mutations-and-citations.md`](docs/system/thread-derived-mutations-and-citations.md); edit it there.

### Coordinator release authority

Moved to [`docs/system/coordinator-release-authority.md`](docs/system/coordinator-release-authority.md); edit it there.

### Eval archives (slice 1)

Moved to [`docs/system/eval-archives-slice-1.md`](docs/system/eval-archives-slice-1.md); edit it there.

## Numbers

| Label | Number | Source |
|---|---|---|
| M | `max_agents` range 1–100 (`MAX_AGENTS`; older tables are widened on the next write), counting distinct connection assignees | `commonplace/projects/limits.py` |
| M | Runner `min_free_memory_mb` default 256 (0 disables): below it the runner fails the lease `low_memory` before any launch | `commonplace/runner/agent.py` |
| M | One worker PAT per machine | `commonplace/runner/registry.py:61` |
| M | Runner store writes use `BEGIN IMMEDIATE`; busy timeout 10 s | `commonplace/runner/jobs.py:68-95` |
| M | One active start per card | `commonplace/runner/jobs.py:47-50` |
| M | A non-person start within 24 h of the last start is refused | `commonplace/runner/jobs.py:236-243` |
| M | Started jobs hold machine in-flight capacity and per-machine project exclusion while their card remains doing with that machine's worker as assignee; failed/cancelled jobs and cards no longer doing or reassigned release capacity, irrespective of elapsed time | `commonplace/runner/jobs.py:lease` |
| M | Hourly start-rate limits, the 24 h started_recently refusal and the separate 3600 s orchestrator admission hold remain unchanged | `commonplace/runner/jobs.py:_hourly,create_in`, `commonplace/orch/runner_bridge.py:_cap_count` |
| M | T3 launch timeout 2–60 s; a timed-out launch is `uncertain` and never re-queued | `commonplace/runner/agent.py:127,333-336` |
| M | Runner hub timeout 15 s | `commonplace/runner/agent.py:247` |
| M | Machine queue limit 8 queued jobs | `commonplace/runner/jobs.py:16` |
| M | `request_key` at most 128 characters | `commonplace/runner/tools.py:92,157` |
| M | T3 launch message at most 120,000 characters | T3 MCP launch schema |
| M | Eval run at most 16 MiB (rows, artifacts, manifest); 256 KiB chunks and read pages; 1 GiB of active runs per workspace | `commonplace/evals/__init__.py` |
| M | Eval key retention 30 days after purge; eval lock waits 5 s | `commonplace/evals/__init__.py` |
| T | A resume is one call; zero duplicate jobs after a lost acknowledgement | acceptance tests |
| T | Config at most 128 KiB, at most 16 roles, a prompt at most 32 KiB | `commonplace/orch/schema.py` |
| T | Per board at most 8 experiments and 20 open proposals, at most 5 per principal | `commonplace/orch/store.py` |
| T | `step_key` matches `[A-Za-z0-9._:-]{1,80}`, so `orch:<role>:<step_key>` fits 128 | `commonplace/orch/runner_bridge.py` |
| G | Lease TTL 1800 s; waits in slices of at most 10 min with heartbeats | rollout |
| G | Wait budgets: start 10 min, gate 90 min, merge 10 min, thread 4 h | rollout |
| G | The owner reviews at most 5 config changes a week | rollout |

## Stores

| Store | Scope | Holds | Backed up |
|---|---|---|---|
| `control.sqlite3` | hub | users, workspaces, memberships, connections, model settings, audit, sign-up waitlist (`signup_waitlist`, additive) | yes |
| `boards.sqlite3` | workspace | boards, cards, `task_events` (append-only) | yes |
| `runner.sqlite3` | workspace | runner jobs and their run ledger; orchestrator configs, cursors, leases, steps, journal, restore flag | yes |
| `rules.sqlite3` | workspace | rules, append-only `rule_versions`, suggestions, served state, and rule evidence: `rule_events` (capped at 50 per writer and 200 per rule) with `rule_stats` counters that keep the full history | yes |
| `topology.sqlite3` | workspace | Compute map facts: nodes, links, site routes, their observations (observer, verbatim evidence, expiry) and conflict records; proposals and applied changes with each fact's value before and after (`topology_proposals`); never secrets | yes |
| `topology.sqlite3` | workspace | Compute map facts: nodes, links, site routes, their observations (observer, verbatim evidence, expiry) and conflict records; never secrets | yes |
| `evals/runs.sqlite3` | workspace | eval runs, their chunks and payload-free purge tombstones | yes |
| palace and other workspace SQLite files | workspace | verbatim memory, indexes, threads | yes |

- Backups copy every SQLite file in the data directory (`commonplace/backup.py`).

- Online writer scans resolve the gateway PID's ancestor chain in the scanned `/proc`, so inherited data settings on supervisors do not falsely block a backup started separately from the gateway. Unrelated writers, including identical wrapper commands, remain refused; worker PID exemptions still end after pause. Offline scans retain only the backup's own ancestry exemption.

- Online enumeration permits only SQLite sidecars (`-wal`, `-shm`, `-journal`) to disappear between listing and metadata lookup: the live gateway may remove them even while file creation is frozen, and snapshots omit them.

- Missing user files, symlinks and special files still abort the backup; offline enumeration remains strict.

- The gateway names the workspace path when entering the creation gate: an existing directory lookup can finish inside a writer transaction while frozen, so it cannot deadlock against the backup's all-database barrier.

- Creating a new workspace directory still waits for thaw.

- `restore()` publishes a verified copy into a new directory and leaves a restore marker there.

- Deploy (`deploy/commonplace/deploy.sh`) requires a new successful backup invocation, a fresh snapshot and backup verification before any Compose call.

- A transient sleep service with `After=<backup>.service` keeps the backup unit referenced from before invocation capture through validation, independently of an enabled timer.

- The ordering dependency does not trigger a backup; it prevents garbage collection from discarding even a fast oneshot's invocation and execution time.

- The holder uses `Type=exec`, is stopped before Compose, and is released by EXIT and signal traps on failure or timeout.

- Missing proof always refuses deployment.

- SIGKILL or a shell crash can leave a holder that the operator must stop before retrying; see the deployment runbook.

## Restore runbook

1. Stop the service that uses the old data directory. Never run two services
   on copies of the same data.
2. Restore into a new directory (`commonplace restore BACKUP NEW_DIR`); the
   destination must not exist. Keep the old directory unchanged and read-only.
3. Start the service on the restored directory. On the first explicit open of
   the orchestrator store (a surface, the CLI, a lease call or a start; never
   the trigger sweep, which skips the workspace while a marker is pending), each
   workspace's restore marker becomes a durable flag in `runner.sqlite3` (new
   incarnation, all orchestrator leases dropped); the marker file is removed
   only after that commit.
4. While the flag is set, lease claims, every config-driven start and the
   trigger sweep are refused. Boards without a `[runner]` config are unaffected.
5. Check the truth sources for anything that may have happened after the
   backup time T (merged PRs, gate verdicts, launched threads, card states).
6. Acknowledge as the owner, typing the exact text:
   `commonplace orch restore-ack --text "effects after <T> may be unknown"`.
   This clears the flag and re-arms the trigger cursor of every configured
   board, so events from before the acknowledgement never fire.
7. Resume orchestrators with `orch_resume`; its checkpoint lists unstarted
   `todo` cards so nothing that was skipped is silently lost.

### Authenticated owner progress scope, State lifetime and quota causality

Moved to [`docs/system/authenticated-owner-progress-scope-state-lifetime-and.md`](docs/system/authenticated-owner-progress-scope-state-lifetime-and.md); edit it there.

## Residuals

- **Eval guard A4 is a lint:** the scan that no code rewrites thread
  identity or deletes thread-store content rows (the seven content tables
  `threads`, `messages`, `line`, `message`, `block`, `blob`, `dict`) reads SQL
  string literals, f-strings, `+` chains and `%`-formatted literals. It never
  catches less than the original per-piece scan, allowlist included (entries
  are scoped to a file and a statement kind). Known limits include `str.join`
  or `+=` building, `%(name)s` formatting, comments inside or before keywords,
  `INDEXED BY` before `SET`, tuple `SET`, quoted schemas, dynamic `SET`
  columns, lower-case dynamic statements with an alias tail, `TRUNCATE`,
  `ALTER TABLE … DROP COLUMN` and Postgres `DELETE FROM ONLY`. Prose with a
  placeholder followed by the end, `;`, `)`, `WHERE` or `RETURNING` is flagged
  as an accepted false positive and is never allowlisted (`docs/evals.md`).

- **Eval archives:** slice 1 covers handoff transcripts and ACL imports only:
  0% of live captures and grant imports, the automatic high-volume trace path;
  the share by count is unmeasured. Purge does not erase retired store
  generations, backups, WAL or free pages, and restoring an older generation can
  bring runs back. Anyone who can read `runs.sqlite3` or its backup can confirm
  a guessed key. A run whose source disappears stays unlisted until purged by
  its retained ID. Single-part imports, palace writers and handoff filing do not
  measure plan storage under the thread-store lock, so eval's plan-storage bound
  holds only against writers that do. Privacy flags miss doubly escaped JSON and
  health hints split across strings; they are signals, not gates.

- **Admin observations:** All seven sections are implemented; operational sections
  request data only with instance eligibility. Health validates the existing public
  `healthz` response independently from installed versions and packaged build SHA.
  Token activity indicates recorded contact, not a live process. Machine and worker
  contact remain separate; owner attestation is always unverified. There is no
  workspace concurrency cap. Off uses distinct doing agents (including unlimited);
  on reuses accepted-only slot accounting, preserving actual use above the cap and
  stored worker assignments on disabled machines. The reused needs-me route has a read-first limitation: `/api/needs-me` can initialize legacy stores and bind
  rule-store metadata; the no-write promise here covers only the new Admin GETs.
  SQLite may create WAL/SHM sidecars during reads, so no-write tests compare logical
  tables, schema and non-sidecar files rather than byte equality of WAL files.
  Section requests are independent observations and can race ongoing activity.
  Runtime/limits/stuck query unarchived boards through the generation-aware supervisor
  resolver, opening existing encrypted board/runner stores with mode=ro, query_only,
  a shared 2 second request budget, at most 250 ms connect/busy waits and a progress
  handler. Missing coordinator and heartbeat columns read as unconfigured/unknown without migration.
  Cursor slugs accept the creation limit of 100 characters. Only enabled, unrevoked
  machines contribute active grants; historical assignments still count toward usage.
  Runtime tables label connection state and token-use activity explicitly; next-page
  source errors preserve earlier rows and retry the same cursor. Typed runtime
  identifiers and enums reject malformed stored values.
  Missing optional files/tables remain absent; corrupt sources fail safely. Only Admin opts into missing runner input; normal admission retains its strict input contract.
  No application/writer locks, store constructors, seat creation, project GET,
  lock-taking settings/registry methods or webhook/coordinator wrappers run on these
  paths. Parameterized workspace-scoped joins verify current membership, disabled
  users, lifecycle, runner kind and subscription policy/wing eligibility. Wakeability
  is eligibility only, never delivery. The injected Admin UTC is captured once for
  lifecycle, stale/lease predicates and recent-start accounting; monotonic time alone
  bounds reads. Independent runtime subresults preserve completed sources and mark
  unattempted sources as errors on budget exhaustion. Client loads including JSON
  decoding retain the 5,000 ms deadline and generation/epoch disposal guards.
  Needs-me keeps its exact server count and queue semantics in a separate panel
  from the deduplicated union of expired leases, active blockers, waiting-person
  facts and raw stale-agent facts, including children. Admin actions are GET reads
  or navigation to existing details/settings; no new admission or mutation API.


- **Backup receipt limits:** Opt-in CLI receipts record the last completed online or
  offline instance snapshot attempt, outside every data/staging/snapshot tree.
  Final-mode reporting suppresses a rejected offline probe when online fallback is
  taken. Check, cleanup and restore do not report. A same-directory 0600 temporary
  file is fsynced, atomically replaced, then its parent fsynced; reporting faults
  emit only a fixed diagnostic and never alter backup outcomes or retries. SIGKILL
  before reporting leaves the previous attempt; no historical backfill, running
  indicator or remote-upload/retention verification is claimed. The gateway only
  reads canonical UTC timestamps with mandatory date/time seconds and optional
  one-to-six fractional digits. Parent receipt refreshes advance child generations
  and cancel older per-mode retries before applying results. The reader
  reads fixed names through no-follow/nonblocking descriptors, fstat regular-file
  checks, strict schema/enums/timestamps and a 16 KiB cap; corrupt modes fail independently.
  The hosting operator provisions one UID/GID 10001:10001, 0700 directory and 0600
  files. The optional overlay mounts it at `/run/commonplace-backup-status-ro` RO
  for the gateway's configuration and `/run/commonplace-backup-status-rw` RW for a
  snapshot-only CLI environment override. Compose must not create the host path.
  Both aliases are visible in the shared container namespace: the configured gateway
  path is RO and its code only reads, but this is not OS isolation from the RW alias.
  This accepted residual requires no alternate container or host reconfiguration by
  the application. Build identity is a validated 40/64 hex SHA installed as package
  data; absent/invalid markers remain unknown, never runtime git/environment identity.


- Accepted, known gaps.

- D9.2 = A is LOCKED: hidden storage may cause `deferred_storage` only for
  actually quota-blocked readable pending work; incomplete progress proof stays
  unavailable without partial counters or a global fallback.

- Each is a reason a check can pass while the system is still wrong.

### Scoped wing counts and search fan-out

Moved to [`docs/system/scoped-wing-counts-and-search-fan-out.md`](docs/system/scoped-wing-counts-and-search-fan-out.md); edit it there.

### Visible logical drawer pagination

Moved to [`docs/system/visible-logical-drawer-pagination.md`](docs/system/visible-logical-drawer-pagination.md); edit it there.

### Hallway read eligibility and visible pagination

Moved to [`docs/system/hallway-read-eligibility-and-visible-pagination.md`](docs/system/hallway-read-eligibility-and-visible-pagination.md); edit it there.

### Authenticated room and status counts

Moved to [`docs/system/authenticated-room-and-status-counts.md`](docs/system/authenticated-room-and-status-counts.md); edit it there.

### Thread-boundary residuals

Moved to [`docs/system/thread-boundary-residuals.md`](docs/system/thread-boundary-residuals.md); edit it there.

## Open dependencies (D1–D3)

- These are owner decisions or captures tracked in `docs/runner.md` ("Owner checklists").

- Until each is closed, the residual above stands.

- **D1 worker identity.**

  - "Attested, not verified" or a dedicated provider instance per worker; stronger identity needs a per-thread credential.

- **D2 launch shape.**

  - One real launch capture settles the result fields, the workspace strategy and `modelSelection`.

- **D3 recovery.**

  - The thread-list keys must be captured before any automatic reconcile; until then `uncertain` and unproven launches go to a person.

## Keeping this file current

- Add a NEW bullet next to the related decision, number, store or residual; do
  not extend a shared paragraph or collect unrelated additions at one section end.
  Keep each topic on its own lines, separated by blank lines. Independent
  insertion positions merge cleanly; edits at the same position can still conflict.
  In the module's file in `docs/modules/`, add a NEW indented sub-bullet to each
  touched module block instead of extending its **Promises** or **Tests** line.

- Parallel changes must not edit the same file. Module blocks live one per file
  in `docs/modules/<module>.md`; `MODULES.md` holds only the format. A new
  decision, number, store or residual can be added as its own file
  `docs/system/<slug>.md`, starting with a `### <title>` heading and written like
  a section of this file; it counts as the change to `SYSTEM.md` that `docs-check`
  asks for, and its place in this file is given by the heading (name the section
  it belongs to in its first sentence). Prefer a new `docs/system/` file over an
  insertion into `## Decisions` here. `commonplace orch docs-check --print system`
  and `--print modules` show the assembled documents.

- `commonplace orch docs-check --base <ref> --head <ref>` exits non-zero on a changed path no `MODULES.md` block owns, a touched module whose block did not change, no change to this file or `docs/system/` (a whitespace-only edit is no change), an empty or missing block, a bad ref or any error.

- The test suite runs it against the merge base with the development branch and fails closed when the base is missing.

- The only exception is a commit trailer `Docs-waiver: W<n>` whose id is listed below **in the base commit**; an id added in the same change does not count.

## Waivers

- Owner-approved waiver ids, one per line as `W<n>: <scope>`.

- None are approved yet.

### Topology is one store of structured facts

Moved to [`docs/system/topology-is-one-store-of-structured-facts.md`](docs/system/topology-is-one-store-of-structured-facts.md); edit it there.

### Bots keep conversations in the workspace store

Moved to [`docs/system/bots-keep-conversations-in-the-workspace-store.md`](docs/system/bots-keep-conversations-in-the-workspace-store.md); edit it there.

### Bots hear about their work without being asked

Moved to [`docs/system/bots-hear-about-their-work-without-being-asked.md`](docs/system/bots-hear-about-their-work-without-being-asked.md); edit it there.

### Bots use machines only through the runner's own checks

Moved to [`docs/system/bots-use-machines-only-through-the-runner-s-own-checks.md`](docs/system/bots-use-machines-only-through-the-runner-s-own-checks.md); edit it there.

### After Done: archive hides, never deletes

Moved to [`docs/system/after-done-archive-hides-never-deletes.md`](docs/system/after-done-archive-hides-never-deletes.md); edit it there.

### Reassigning active work

Moved to [`docs/system/reassigning-active-work.md`](docs/system/reassigning-active-work.md); edit it there.
### Legacy surface comparison and agent guidance

Moved to [`docs/system/legacy-surface-comparison-and-agent-guidance.md`](docs/system/legacy-surface-comparison-and-agent-guidance.md); edit it there.

### Compatibility fixture coverage boundary

Moved to [`docs/system/compatibility-fixture-coverage-boundary.md`](docs/system/compatibility-fixture-coverage-boundary.md); edit it there.

### Refused Done moves and handoffs

Moved to [`docs/system/refused-done-moves-and-handoffs.md`](docs/system/refused-done-moves-and-handoffs.md); edit it there.

### Canonical Space REST foundation

Moved to [`docs/system/canonical-space-rest-foundation.md`](docs/system/canonical-space-rest-foundation.md); edit it there.


### Authorization-only provenance spelling recognition

Moved to [`docs/system/authorization-only-provenance-spelling-recognition.md`](docs/system/authorization-only-provenance-spelling-recognition.md); edit it there.


### Current typed provenance association proof

Moved to [`docs/system/current-typed-provenance-association-proof.md`](docs/system/current-typed-provenance-association-proof.md); edit it there.


### Memory map citation omission

Moved to [`docs/system/memory-map-citation-omission.md`](docs/system/memory-map-citation-omission.md); edit it there.

### Memory map mode and private visibility

Moved to [`docs/system/memory-map-mode.md`](docs/system/memory-map-mode.md); edit it there.
### Explicit tunnel read proof

Moved to [`docs/system/explicit-tunnel-read-proof.md`](docs/system/explicit-tunnel-read-proof.md); edit it there.


### Explicit tunnel concurrency and import qualification

Moved to [`docs/system/explicit-tunnel-concurrency-and-import-qualification.md`](docs/system/explicit-tunnel-concurrency-and-import-qualification.md); edit it there.


### Graph projection before traversal and ranking

Moved to [`docs/system/graph-projection-before-traversal-and-ranking.md`](docs/system/graph-projection-before-traversal-and-ranking.md); edit it there.


### Readable legacy graph parity

Moved to [`docs/system/readable-legacy-graph-parity.md`](docs/system/readable-legacy-graph-parity.md); edit it there.


### Graph compatibility residuals

Moved to [`docs/system/graph-compatibility-residuals.md`](docs/system/graph-compatibility-residuals.md); edit it there.


### Local worker allocation observation

Moved to [`docs/system/local-worker-allocation-observation.md`](docs/system/local-worker-allocation-observation.md); edit it there.


### D1-visible KG pages and aggregates

Moved to [`docs/system/d1-visible-kg-pages-and-aggregates.md`](docs/system/d1-visible-kg-pages-and-aggregates.md); edit it there.


### Scoped KG search cost

Moved to [`docs/system/scoped-kg-search-cost.md`](docs/system/scoped-kg-search-cost.md); edit it there.

### Search enrichment compatibility note

Moved to [`docs/system/search-enrichment-compatibility-note.md`](docs/system/search-enrichment-compatibility-note.md); edit it there.
### Bounded memory for overview reads

Moved to [`docs/system/bounded-memory-for-overview-reads.md`](docs/system/bounded-memory-for-overview-reads.md); edit it there.
### Outgoing provenance aliases and coherent ranges

Moved to [`docs/system/outgoing-provenance-aliases-and-coherent-ranges.md`](docs/system/outgoing-provenance-aliases-and-coherent-ranges.md); edit it there.

### Authorized room and status count responses

Moved to [`docs/system/authorized-room-and-status-count-responses.md`](docs/system/authorized-room-and-status-count-responses.md); edit it there.

## Person private-read containment

Person sessions derive private read authority only from the exact current
workspace owner role. A missing, omitted or unexpected role grants no private
read scope. Membership alone is insufficient. The admin callback eligibility
reader uses that same role-aware policy. No stored membership or data migration
is involved; owner policies retain their existing scopes and behavior.

Members temporarily cannot read any private wing, including their own private
notes, private chats, thread mirrors and diaries. Workstation wing aliases that
require every wing, private-sensitive facts, secret/unwinged facts and the map's
people overlay are also unavailable. Existing input-authorized projections retain
shared overview, graph, KG, tunnel, hallway and drawer listings without hidden
labels, ranks or counts. Wildcard policies can reach those projections without
private scope; literal-wing policies keep their existing tool visibility.

A room, or a diary topic, names a thread when any copy of its value contains a
thread ID or a T3 source ID. The copies are the raw value and every result of
percent-decoding followed by NFKC normalisation, repeated until the value stops
changing (at most four rounds). The thread ID may appear as a bare ID, in an
`/app/threads/` path or URL, in a `t3-thread://v1/` link, after `mcp:`, or next
to letters, digits or underscores (`notes_thr_…`, `thr_…_notes`); fullwidth
spellings count after NFKC, and an upper-case `THR_` or hex spelling names the
stored lower-case thread, so its readers keep the room. A T3 source ID may contain
any character (`mcp:github.com/o/r`, spaces, quotes), so each `mcp:` occurrence
(any case) names the whole remaining value, which is resolved through the
workspace thread store: every stored source ID, or hash-variant base, that is a
prefix of that remainder counts, including `mcp:<source>_notes`. Source IDs are
compared after NFKC; only the namespace case-folds. A stored source is keyed as
NFKC first, then its variant base (cut at `#sha256:` in any case), then the
namespace folded; the room's whole value is NFKC-normalised with its namespace
folded and never cut at a hash marker. The tested stored spellings that match
`mcp:x` are `MCP:x`, `Mcp:x`, a fullwidth `ｍｃｐ:x`, and the mixed-width
`mｃｐ:x`, `mcｐ:x` and `mｃｐ：x`; any other ASCII and fullwidth mix folds the
same way through NFKC. A stored `mcp:x#` matches a room `mcp:x#sha256:...`, a
stored variant marked with a fullwidth number sign or an upper-case marker
matches its base, and a bare stored `mcp:` source is a prefix of every `mcp:`
value (it fails closed). Apart from the namespace, case is exact: `mcp:Probe`
and `mcp:probe` are different sources (thread IDs, by contrast, match in any
case).

Filing a thread or naming a successor thread accepts only canonical thread IDs:
the ID equals its NFKC form, has no control or format characters, no leading or
trailing whitespace and no percent-encoding, names `mcp:` (any case, after NFKC)
only as its leading namespace, and writes any `#sha256:` marker in lower case.
Other IDs are refused with a `thread_id ...` error before any side effect. T3
thread IDs (`[A-Za-z0-9][A-Za-z0-9._:@-]*`) and their `mcp:` forms pass. Each
stored thread is keyed in an indexed side table (`thread_source_keys`: NFKC
source, match key, legacy flag), written with the thread and backfilled for
older stores; a room check probes the room's candidate prefixes and its whole
NFKC value with indexed lookups, cached for the response. Sources filed before
this check that are not canonical are legacy rows: they form a small index built
once per response from those rows only, keyed by every `mcp:` remainder the room
resolver could extract after percent-decoding and NFKC rounds with control and
format characters removed and trailing whitespace stripped, cut at a variant
marker in any case; rooms are matched against it by prefix. A room value equal
to any stored source ID after NFKC is also hidden from readers who cannot read
that thread. If a source lookup fails, every value that is not a bare thread ID
is hidden. Write-side room checks share one reader per gateway call, final
tunnel pages share one reader, and a checkpoint takes at most 500 items.

One definition of a thread ID (`provenance_refs.THREAD_ID`) and one room
check (`provenance_refs.room_references`, used through
`mirror_access.private_thread_reference`) cover every route that returns or
writes a room or topic. Reads: drawer reads, search (structured and
CLI-compatible), drawer listings, room lists and status (overview projection),
taxonomy, tunnels, graph traversal and statistics, hallways, duplicate checks and
diary reads, including drawer `metadata.topic`. Writes: add, update, delete,
checkpoint items and diary, tunnel creation and diary writes. A reader who cannot
read every named thread, including every thread filed under a named source ID,
receives the drawer or entry as not found, and the room is omitted from listings,
exactly as for a bare thread-ID room. Readers of all those threads, including
owners, keep the room verbatim. A source ID naming no stored thread names
nothing; a source lookup failure hides the value.

Counts and flags describe only what the reader receives. Diary reads probe the
entry topics first, and the worker excludes entries whose topic names an
unreadable thread before it counts or windows them, so `total` and `showing`
cover visible entries; the final gate recounts any entry it still drops. The
probe reports which store served it (the SQLite fast path or the collection),
and the read is pinned to that store; a store change between the two refuses the
read in either direction. The probe reports at most 1000 distinct topics; beyond
that every read of that diary fails closed with an error saying the agent has too
many diary topics to check. An empty hidden-topic list leaves the read's SQLite
page path unchanged. A diary read never falls
back to an unscoped count: a probe validation error is returned as the read's
answer; paused or unavailable memory is reported exactly as on other routes (an
error, with `retry_after_s` during an online-backup pause); any other probe fault
is logged by class only and answered with an explicit error, for owners and
everyone else. The diary `thread_scope` is server-only: the gateway drops any
client value before every route and always sets its own, and the worker accepts
it without listing it. A diary entry hidden by its topic is hidden from diary
reads, drawer reads and listings, structured search (including unified media and
code search), CLI search text and `check_duplicate` matches: search rows and
duplicate matches carry the entry's `topic`, so the gate drops them.
`check_duplicate` recomputes `is_duplicate` from the matches that pass the gate. Search acquisition counters
and truncation flags (`total_before_filter`, `filtered_by_max_distance`,
`date_filter_pool_truncated`, `candidates_truncated`,
`distinct_results_truncated`) precede authorization and are omitted for every
principal.

Residual costs of failing closed. A hex run shorter than 16 digits directly
followed by an ASCII letter is an ordinary word (`thr_deadline`, `thr_notes`).
Any other `thr_<hex>` run counts as an ID, so hex-looking ordinary names such
as `thr_cafe` or `notes thr_add` resolve to no thread and are hidden from every
reader, owners included. Room-name validation refuses `/`, `:`, `%` and control
characters in rooms written through the tools, so link, `mcp:` and undecodable
forms reach storage through older or imported data and through owner
configuration: the project miner files drawers under room names taken from the
project's `mempalace.yml` without that validation. Values that cannot be decoded
(control or format characters, invalid percent-encoded UTF-8, more than four
percent levels in one round, still changing after four decode/NFKC rounds, or
over the citation length limit) are also hidden from every reader. Wing names
are outside this check: a wing named like a thread ID is filtered by wing policy
alone.

Known residuals. A legacy stored source whose `mcp:` is not the leading
namespace (for example `xmcp:x`, filed before thread IDs were checked) still
counts as naming `mcp:x`, so a room `mcp:x` is hidden from readers who can read
a canonical `mcp:x` thread but not that legacy twin; no new such twin can be
filed. The diary probe and the read are two worker calls: a hidden
entry filed between them with a `filed_at` older than the newest `last_n`
entries is neither excluded nor in the window, so `total` can count it once.
Its words and topic are never returned.

`mcp:` source IDs outside rooms. The room rule (whole value, prefix-matched,
every matching thread readable) also covers the other places a thread can be
named: provenance fields that name a thread (`source_thread`, `source_url`,
`source_link`, `origin_id`, `origin_link`, `approval_link`) on knowledge-graph
facts and their sources and on drawer metadata, `source_kept.thread` and the
`source` bundle of an add-drawer replay, and tunnel labels. A reader who cannot
read every matching thread loses that field (the fact, drawer or tunnel stays;
a tunnel whose label names such a thread is hidden, as for `thr_` references);
a value naming no stored thread is unchanged. `source_file`, `source_path`,
`source_drawer_id` and `source_closet` keep their existing checks.

CLI rendering rule (replaces the earlier rule that
every-wing policies keep worker-rendered CLI output and search diagnostics):
CLI-compatible search, for every principal, acquires structured hits and renders
only after the same drawer gate as structured search: room, topic and provenance
checks; body text is not scanned. A see-all reader may still lack a thread that a
room names, so no principal receives worker-rendered CLI text. This applies with
a literal wing and with no wing. Both CLI paths use the same pure formatter. The
gateway explicitly renders flat rows, so import-authorization metadata cannot
replace a readable row's wing, room or source. CLI responses contain only query,
CLI text, an empty results array, optional searched wings and non-empty facts.

Owner-visible changes from this rule set. Search acquisition counters and
truncation flags are no longer returned to owners. Owner CLI search uses the
gateway's flat rendering, so an empty result reads `No results found for:
"<query>"`, and owner CLI searches with `source_file`, `min_similarity`,
`max_distance`, `context`, `include_media` or a non-search `query_task` are
refused with the same errors as for other principals. Structured search rows and
`check_duplicate` matches for diary entries carry their `topic`. Diary reads cost
one extra metadata-only worker call (the topic probe). Rooms and topics naming a
thread the owner cannot read (another person's capture thread, a hex-looking
name, an undecodable value) are hidden from owners too.

Content placed in an ordinary shared room is visible on every route. Body text
and arbitrary provenance labels are not scanned for private words or fingerprinted;
recognized protected references in source_file, source_path and media path/title
aliases are still checked.

Provenance containing control or format characters (for example a tab or zero-width
space) is omitted even for ordinary paths. This remains fail-closed because hidden
or encoded characters can make reference interpretation ambiguous; readable drawer
text is retained without rewriting the path to guess its meaning.

Unprojected whole-palace operations remain unavailable to members: mesh estate,
duplicate detection, checkpoint, source-wide deletion, tunnel/hallway deletion,
memories-filed-away, and low-level coordination event/artifact/patch operations.
They have the same unknown-operation refusal as unavailable operations. Explicit
private reads remain independent of private data presence. This containment does
not define a restricted estate response or restore filer-specific private reads.

### Event, write and durable-token boundaries

Moved to [`docs/system/event-write-and-durable-token-boundaries.md`](docs/system/event-write-and-durable-token-boundaries.md); edit it there.

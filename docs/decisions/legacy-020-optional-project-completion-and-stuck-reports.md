### Optional project completion and stuck reports

Owner-enabled orchestration adds a durable `project.report` route for transitions
from a non-done state to done and for observed stuck episodes. It is off by
default and carries only board, card number, report id, reason, generation and
captured coordinator. No card words, exception contents or credentials are sent.
Reports grant no start, split, claim or other authority.

Connect the project seat again after enabling, then remove and re-add its webhook
using Connect's returned event list
(`["space_message", "card.moved", "card.created", "project.report"]`).
Enabling alone never edits subscriptions. Setup gaps do not replay old reports.
Raw subscriptions retain their behavior: a legacy completion hook still gets its
raw wake; subscribing to both channels can deliver raw plus report. Cross-channel
deduplication is deferred.

Done capture shares the source transaction. An unavailable settings/coordinator
snapshot rejects the transition with `settings_unavailable` (503), before a lesson
is saved. Ordinary edits remain available. A board created by the same transaction
has no coordinator and stays history only when the snapshot is available; an
unavailable snapshot still rejects a relevant done transition. This is fail-closed,
not a guarantee to undo arbitrary external memory side effects after a lesson.
Existing raw event emission can still fail after the board commit.

Report installation registers recovery without resolving workspace directories.
Recovery installs the report reset trigger only when all watched task columns
exist and removes an existing incomplete trigger otherwise. For sources missing
only `heartbeat_at`, this keeps raw status writes usable until BoardStore adds
that column and installs the trigger. The other watched columns belong to the
base schema; BoardStore does not add them during this migration. Until migration,
no episodes are recorded, while reporting is enabled, a pass that reaches a coordinated task logs an error, and watched writes do
not bump `report_reset_rev`. Product writers open BoardStore before writing.
The first dispatcher scan recovers and migrates existing board databases without creating
workspace directories, followed by monotonic 30-second scan deadlines independent
of requests and webhook traffic. Source initialization is cached by database path,
inode and SQLite schema version. Zero-byte files are deferred without opening a
connection; a read-only probe defers nonempty files without both base tables
(`boards` and `tasks`). Neither case opens a write transaction or runs report
migrations; a later pass picks up the source after its creator initializes it.
Ready sources have their schema version read through the same read-only probe.
Ordinary data writes preserve the cache; a new inode or a changed schema version
triggers initialization again, including repair of dropped report tables.
Failures are isolated per workspace and logged with exception type and stack
locations, excluding exception text and local values. Each workspace pass reads
at most 100 cards, publishes at most 100 source rows separately and cleans at most
100 deleted episode keys. Persisted pagination provides fair continuation; a cycle can
take longer than 30 seconds. The first observation in a generation baselines
existing staleness without sending it. Heartbeat and leave/reclaim revisions
separate later observed episodes, even at equal timestamps across restart.
Episodes beginning and ending entirely between observations are lost. There is
no historical-done replay. A workspace failure leaves other workspaces progressing.

Source access closes before control publication. Event and receipt commit together;
source acknowledgement happens later. Receipts are retained without age pruning
because a publisher may still retry an old source row. Report reconcile saves an
immutable per-event routing envelope atomically with cursor advancement. Unknown
routing facts defer only that report with persisted 1/2/4/8/16/30-second capped
backoff, while later raw events and due HTTP deliveries keep progressing. Enqueue
and retry-work deletion are atomic. Inability to persist control work, cursor or
retry metadata uses the existing infrastructure-error path and can delay dispatch.

Send rechecks the captured coordinator, current connection/wing authority, board
and activation/binding generations across separate control/source/control phases;
HTTP holds no database lock. Positive invalidation cancels only reports. Busy,
locked, wrong-key and unknown failures retry indefinitely without using HTTP
attempts or producing dead-letter failures. Existing delivery inspection shows
`error=report_authorization_retry`, `count` and stable `retry_since`; ALLOW and
cancellation clear them. All unresolved auth rows remain visible beyond the latest
100 ordinary deliveries. This unresolved set is unbounded by design; inspection
reads all rows for that hook. Operators should repair authority/storage or cancel
the affected hook. Exception contents never enter inspection or payloads.

A receiver deduplicates `report_id`, fetches the current card and version, and
reports progress through existing authorized tools. For done, inspect the current
result and continue only work already authorized. For stuck, inspect the current
holder and person-wait state before an authorized follow-up. HTTP is at least
once: duplicate requests must not repeat the receiver's action. Broader receiver
playbooks and the original wider orchestration acceptance remain separate work.



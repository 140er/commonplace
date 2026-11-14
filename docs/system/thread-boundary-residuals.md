### Thread-boundary residuals

- Token-free plain-file citations require complete exact local association proof on read.
  Typed drawer/closet citations now resolve current local metadata as described
  below; foreign identifiers already stored before this change are not purged.
- Non-canonical spellings outside the token grammar remain possible, including
  inserted characters that are neither Cc nor Cf (combining marks or Unicode
  spaces), fullwidth letters, and missing underscores. Subject, predicate and
  object text stays verbatim and is outside provenance parsing.
- `add_drawer` source publication is outside KG citation checks. External
  `ext-`, `mcp:` and `import:` ids hidden only by wing policy remain ungated.
  Drawer update/id-delete closet cleanup still follows the complete source
  across rooms; scoped replies omit its closet count.
- An any-readable-source fact remains mutable despite other hidden sources.
  A single-valued save leaves an unreadable rival open; a new racing row can
  produce a duplicate. Scoped cleanup can leave stale unassociated legacy
  index rows, while unscoped local cleanup retains its broader purge.

- **Same OS user.**

  - Anyone who runs as the service's OS user can use the CLI (which is `orch_owner`) and read the data directory.

- **Restore outside `restore()`.**

  - A hand-copied data directory or a deleted `runner.sqlite3` carries no restore marker: the journal resets and nothing is blocked.

- **Effects after T.**

  - After a restore, effects made after the backup time are unknown to the journal; the truth checks in step 5 are the only guard.

- **Outside the journal.**

  - External effects and gate runs started outside the journal are invisible to resume.

- **Revocation window on trigger starts.**

  - The trigger's requester and grants are read in a control snapshot before the fenced transaction.

  - A grant or requester revocation that commits between that snapshot and the in-lock insert is not re-read; the start can still be inserted.

  - The runner re-checks the requester and grants at `starting`, so the job is refused before launch.
  - The runner re-checks requester, grants and binding epoch at `starting`, including idempotent retry. Each job snapshots the validated machine epoch; legacy jobs have NULL snapshots and are refused.

  - Topic/wing reset, worker rotation and revoke increment the machine epoch once. Same-provider confirmation and project-only edits preserve it; changing an attested provider returns 409 `provider_change_requires_reset` until explicit reset and owner review.

  - Board prechecks finish before runner admission; fresh control authority is then held through the guarded starting write and runner commit in both switch modes. A committed reset before the write refuses; one after the write leaves starting work to become uncertain at the next sweep, retaining capacity and never relaunching. Owner resolution takes runner first, then control audit.

  - Stop old hub processes before upgrading/restarting: old reset writers do not increment epochs, so mixed-version writers cannot uphold this fence.

- **Same-second ties are listed.**

  - Job starts and board events are stamped in whole seconds, so a start in the same second as a card's latest firing event cannot be ordered against it.

  - Resume lists such a card as unstarted; it may be a false positive.

- **Skipped events.**

  - Board events committed between a configuring accept (or a restore acknowledgement) and arming are skipped, not replayed.

  - Resume lists the affected `todo` cards as unstarted.

- **Refused triggers are not retried.**

  - A `todo` card whose trigger start is refused (`busy`, `at_cap`, `started_recently` or any other code) is a final miss: the cursor moves past it and nothing re-fires it, even after the queue drains or a slot frees up.

  - It shows in the checkpoint's unstarted list and as a miss; move the card out of `todo` and back to retry.

- **Orchestrator tools on every workspace.**

  - The `orch_*` tools appear in the tool list on every workspace, also where no board has a config (runner children never see them; owner-only tools are listed for owners only).

  - Without a config only proposing and reading do anything.

- **Docs-check checks change, not accuracy.**

  - It refuses a touched module whose block did not change and a change without a `SYSTEM.md` edit (whitespace-only edits do not count); it never judges whether the new text is right.

- **Cap overshoot.**

  - The cap reads card state from `boards.sqlite3` as a snapshot, so a concurrent claim outside the runner can overshoot it.

  - Started jobs hold machine in-flight capacity and per-machine project exclusion only while their cards are doing with that machine's worker as assignee, even after an hour.

  - A terminal job state, a card leaving doing, or reassignment releases that capacity.

  - The lease resolves the worker identity before its runner transaction and reads cards through the read-only boards helper inside it, without constructing BoardService/BoardStore or acquiring a boards write lock.

  - Card state can change after that read; the next lease reads it again.

- **D1 shared worker PAT.**

  - All runner children use the machine's worker PAT, so per-child provenance and per-child limits are not available.

- **D2 launch shape.**

  - Whether T3 accepts `modelSelection` at runtime is unverified; when it is given, effort comes from T3's defaults.

- **D3 thread-list keys.**

  - An empty thread list is no proof that a launch did not happen; such steps go to `needs_person`.

- **Plaintext job lease tokens.**

  - The runner's job lease token is stored in `runner_jobs` and in the machine journal (pre-existing).

- **Waiver list.**

  - Approval of the waiver list below is a manual owner review.

- **Capture hook spool.**

  - The capture hook stores the hook JSON as received (session metadata and a transcript path, no transcript text) in `~/.commonplace/spool` and an uploader reads the transcript later. A transcript deleted or rewritten before the upload is lost to the hub; the item is set aside under `rejected/` with a log line. The spool is bounded (500 items, 8 MiB) and drops the oldest items first, logging each drop.

- **Scheduled prompts:** A schedule's prompt is delivered as an owner-authored message on the board the agent coordinates, through the ordinary `space_message` wake; delivery is at most once per due time (a missed or interrupted due time is recorded, never replayed), is re-authorised at each due time, and is limited to coordinators. Non-coordinator agents have no existing prompt channel that avoids creating a card, so they are not supported yet. The worker polls every 30 seconds, so a prompt can arrive up to that much after its due time.

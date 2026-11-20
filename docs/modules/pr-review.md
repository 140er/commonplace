## pr-review

- **Paths:** `commonplace/pr_review/`, `tests/test_pr_review.py`
- **Owns:**

  - Auto-addressed PR review comments, slice 1 (card #613, design in `docs/system/pr-review-loop.md`): comments of a pull request in the workspace file `pr_review.sqlite3` (`pr_review_prs`, `pr_review_comments`, `pr_review_events` append-only, `pr_review_boards`, `pr_review_people`), their six statuses (`new`, `working`, `addressed`, `answered`, `needs_you`, `skipped`) with fixed reasons and the words people read (`status.py`), the idempotent intake of a feeder's batches (`intake.py`, `store.py`), PR to card mapping and the fork and branch rules (`mapping.py`), the work trigger (`trigger.py`), the one question per PR (`needs_me.py`), `/api/pr-review` (`routes.py`) and the Coworker tools `pr_review_list` and `pr_review_report` (`tools.py`). The hub never calls GitHub: a feeder outside the hub posts the comments.

- **Promises:**

  - A comment is handled once: `(repo, PR, kind, GitHub id)` is unique, the same edit time is a no-op, an edit with a changed body is the next revision and puts the comment back to `new`, an edit with the same words changes nothing, and a removed comment is `skipped` (`removed_by_reviewer`). A request is checked whole and stored in one transaction, so one bad comment stores nothing.

  - Comment text is stored exactly as sent; every projection of it (the lists, the Coworker's context, errors, logs) is redacted, the original is one read away for a person who can open the card, and a log line carries ids, counts and reason codes, never a body or a reply.

  - Only comments from trusted authors (repository `OWNER`, `MEMBER`, `COLLABORATOR`, or a login the owner listed) become work; the Coworker's own replies, verdict lines and strangers are shown as `skipped` with a reason. A comment edit from a stranger never becomes work.

  - The feeder token has the one scope `pr_review:write` (owner, no parent, one live per workspace) and reaches only the intake and watch routes; every other route answers it 403. Card reads and the decision follow card visibility with one uniform 404, agent tokens use the tools (403 on the people routes), and the tools offer no merge, no thread resolving and no review dismissal.

  - A round is the Coworker taking the waiting comments (at most 25) with `pr_review_list take=true` while it holds the card; a PR has two rounds by default (1 to 3 per board), a run that reports nothing gives its round back, and a comment nobody reports returns to `new` once and then asks a person. A fix is `addressed` only when its commit is the PR head the feeder saw and the feeder reported that head's changed files; the file list is never carried over from an earlier head (a new head without one makes it unknown), a fix whose files stay unknown after the grace asks a person (`files_unknown`), and a fix that touches the CI, gate, review-rule or lint files is held (`touches_checks`). One trigger pass looks at 20 PRs at most, longest-waiting first.

  - The trigger acts only on boards where an owner switched the loop on (off by default), never for a fork, another branch, a merged or closed PR, a card in `todo` or `doing`, or a card waiting for a person's own answer; a finished card waiting in a completion review is handed back for more work as the person who switched the loop on, which fires the ordinary todo trigger. Reasons no start can fix become `needs you` comments and at most one question on the card.

- **How it fails:**

  - A refused batch is a plain 400 with nothing stored, a full PR or workspace is a 429 `intake_limit` that drops nothing, a stale revision or a comment someone else holds is refused (`comment_changed`, `not_working`), and every `needs you` reason has fixed text and a next step in `status.py`. The trigger's own failures are logged by type and never fail an intake.

- **Tests:**

  - `tests/test_pr_review.py` covers idempotent intake and edits, validation, the feeder token's reach, trusted and untrusted authors, mapping (none, one, ambiguous) and uniform 404, redaction and the log rule, taking a round and every report outcome and refusal, rounds and the person's answers, the stale-run requeue, and the trigger (off by default, hands a finished card back once, leaves a held card, fork, other branch, merged PR, no Coworker setup, one question).

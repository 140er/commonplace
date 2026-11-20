## pr-review

- **Paths:** `commonplace/pr_review/`, `tests/test_pr_review.py`, `tests/test_pr_review_loop.py`, `tests/_pr_review_fakes.py`
- **Owns:**

  - Auto-addressed PR review comments, slice 1 (card #613, design in `docs/system/pr-review-loop.md`): comments of a pull request in the workspace file `pr_review.sqlite3` (`pr_review_prs`, `pr_review_comments`, `pr_review_events` append-only, `pr_review_boards`, `pr_review_people`), their six statuses (`new`, `working`, `addressed`, `answered`, `needs_you`, `skipped`) with fixed reasons and the words people read (`status.py`), the idempotent intake of a feeder's batches (`intake.py`, `store.py`), PR to card mapping and the fork and branch rules (`mapping.py`), the work trigger (`trigger.py`), the one question per PR (`needs_me.py`), `/api/pr-review` (`routes.py`) and the Coworker tools `pr_review_list` (read), `pr_review_take` and `pr_review_report` (writes) (`tools.py`). The hub never calls GitHub: a feeder outside the hub posts the comments.

  - Slice 2: the review-comment feeder (`feeder.py`, `commonplace pr-review poll`), which reads watched PRs on GitHub and posts them to the intake from outside the hub; the Coworker's role text (`guide.py`), the review section of a Coworker's start context (`context.py`, read by `runner.start_context`), the reply mode of a PR (`store.reply_mode`: replies are posted on a private repository, and on a public or unknown one only when the owner switched `post_replies` on for the board; otherwise they are shown on the card) and the store's close path (`store.close_all`, run before the control database closes).

- **Promises:**

  - A comment is handled once: `(repo, PR, kind, GitHub id)` is unique, the same edit time is a no-op, an edit with a changed body is the next revision and puts the comment back to `new`, an edit with the same words changes nothing, and a removed comment is `skipped` (`removed_by_reviewer`). A request is checked whole and stored in one transaction, so one bad comment stores nothing.

  - Comment text is stored exactly as sent; every projection of it (the lists, the Coworker's context, errors, logs) is redacted, the original is one read away for a person who can open the card, and a log line carries ids, counts and reason codes, never a body or a reply.

  - Only comments from trusted authors (repository `OWNER`, `MEMBER`, `COLLABORATOR`, or a login the owner listed) become work; the Coworker's own replies, verdict lines and strangers are shown as `skipped` with a reason. A comment edit from a stranger never becomes work.

  - A person sees only what belongs to a card they can read: the PR list drops every PR whose card they cannot read (and every PR with no card; only a workspace owner sees those, with the reason) before any repository, number, count or time is added, the board settings and trusted logins are the owner's pages, and a failed board read is its own reason (`lookup_failed`), never folded into `no_card`. The card view pages its comments (200 by default, 500 at most, with a total and `has_more`), the PR list and the list tool are bounded, and a trigger pass looks at 20 PRs at most.

  - A changed path is normalised (backslashes, `./`, repeated slashes, case; a path with `..` counts as protected) before the protected-path rule is applied.

  - A workspace has one store object per file: the first requests of a workspace may race, and `open_review` builds the store under a lock.

  - The feeder token has the one scope `pr_review:write` (owner, no parent, one live per workspace) and reaches only the intake and watch routes; every other route answers it 403. Card reads and the decision follow card visibility with one uniform 404, agent tokens use the tools (403 on the people routes), and the tools offer no merge, no thread resolving and no review dismissal.

  - A round is the Coworker taking the waiting comments (at most 25) with `pr_review_take` (a checked write, so a stale rules sequence refuses it) while it holds the card; a PR has two rounds by default (1 to 3 per board; the board's setting alone is the cap, and "try again" gives that many back), a run that reports nothing gives its round back, and a comment nobody reports returns to `new` once and then asks a person. A fix is `addressed` only when its commit is the PR head the feeder saw and the feeder reported that head's changed files; the file list is never carried over from an earlier head (a new head without one makes it unknown), a fix whose files stay unknown after the grace asks a person (`files_unknown`), and a fix that touches the CI, gate, review-rule or lint files is held (`touches_checks`). One trigger pass looks at 20 PRs at most, longest-waiting first.

  - The trigger acts only on boards where an owner switched the loop on (off by default), never for a fork, another branch, a merged or closed PR, a card in `todo` or `doing`, or a card waiting for a person's own answer; a finished card waiting in a completion review is handed back for more work as the person who switched the loop on, which fires the ordinary todo trigger. Reasons no start can fix become `needs you` comments and at most one question on the card.

  - The feeder reads nothing until its read-only GitHub token file exists (it says so and exits 3 with `--once`), never writes to GitHub, never logs a token or a comment's words, uses conditional requests and pagination, posts a comment it saw before and no longer finds as removed, keeps a PR's new state only after every request for it was posted, and pauses while GitHub's rate limit is below 50 requests. A PR's head, state and fetch time move only with a read that is not older than the stored one.

  - A started Coworker reads the waiting comments of its card's PR (at most 25, quoted, redacted) under the rules it must follow; a failure to read them never stops a start and logs no words. A report that says a reply was posted is refused where the board shows replies on the card only (`dry_run`), and every recorded reply keeps whether it was posted.

  - The feeder survives bad input (card #629): a watch answer or a GitHub answer that is not the JSON it expects is a fixed code (`hub_bad_answer` stops the pass, `github_bad_answer` skips that PR and keeps nothing of the read), a watch entry that is not a repository and a whole PR number is left out, an unexpected error skips only its PR and is logged by type, and the polling loop outlives any bad pass. A report batch is checked whole and stored in one transaction: one refused report (a posted reply in a dry run, a changed comment, a missing commit, reply or note, an unknown comment) stores none of the batch.

- **How it fails:**

  - A refused batch is a plain 400 with nothing stored, a full PR or workspace is a 429 `intake_limit` that drops nothing, a stale revision or a comment someone else holds is refused (`comment_changed`, `not_working`), and every `needs you` reason has fixed text and a next step in `status.py`. The trigger's own failures are logged by type and never fail an intake.

- **Tests:**

  - `tests/test_pr_review.py` covers idempotent intake and edits, validation, the feeder token's reach, trusted and untrusted authors, mapping (none, one, ambiguous) and uniform 404, redaction and the log rule, taking a round and every report outcome and refusal, rounds and the person's answers, the stale-run requeue, and the trigger (off by default, hands a finished card back once, leaves a held card, fork, other branch, merged PR, no Coworker setup, one question).

- **Owns:**

  - The `pr_review:write` scope (`policy.PR_REVIEW_SCOPE`, with `policy.FEED_SCOPES`): the review-comment feeder's own token. `ControlDB.create_token` mints a feed scope only alone, for a workspace owner, with no parent and at most one live token per feed scope; the gateway refuses every request but `POST /api/pr-review/comments` and `GET /api/pr-review/watch` to a connection that holds it (403, before any route or rate check), and the extension `commonplace.pr_review` is installed with the others.

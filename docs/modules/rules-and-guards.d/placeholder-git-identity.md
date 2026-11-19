- **Owns:**

  - The pre-push hook runs `python -m commonplace.guards.identity --pre-push` on the commits being pushed before the quick check. Placeholder author or committer identities fail the push and the message says to set `git user.name` and `git user.email`, then amend or rebase.

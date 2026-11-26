- **Owns:**

  - `commonplace/guards/identity.py` rejects placeholder git author and committer identities. The pre-push hook runs `python -m commonplace.guards.identity --pre-push` on the commits being pushed before the quick check. A rejection says to set `git user.name` and `git user.email`, then amend or rebase.

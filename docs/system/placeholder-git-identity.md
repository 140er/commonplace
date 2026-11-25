### Placeholder git identities are rejected

- The pre-push hook rejects an author or committer with an empty name, a generic name such as "Project contributor", or an email at example.invalid, example.com, or localhost. It checks the commits being pushed before running the quick check. The same check accepts an explicit commit range through `python -m commonplace.guards.identity --range`.

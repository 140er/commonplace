### Placeholder git identities are rejected

This belongs to the Decisions section.

- The pre-push hook and CI reject an author or committer with an empty name, a generic name such as "Project contributor", or an email at example.invalid, example.com, or localhost. CI checks the pull request's commits, so `git push --no-verify` does not bypass the check.

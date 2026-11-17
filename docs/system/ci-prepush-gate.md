### CI runs the pre-push gate

This belongs to the Decisions section.

- Pull requests and pushes to `develop` and `main` run `python -m commonplace.guards.prepush --ci`: affected tests, lint, and the four guard files. The pre-push hook calls the same module. A skipped or cut-off step fails the CI run.

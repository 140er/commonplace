### CI runs the pre-push gate

- Pull requests and pushes to `develop` and `main` run `python -m commonplace.guards.prepush --ci`: affected tests, lint, and the four guard files. The pre-push hook calls the same module. A skipped or cut-off step fails the CI run.
- A mandatory guard that is missing or is not a regular file fails the gate before pytest starts. The repository checks affected tests, smoke and all four guards; it never runs the full suite.

- **Owns:**

  - `python -m commonplace.guards.prepush --ci` is the CI form of the pre-push check: the same lint, format, docs-check and affected-test selection, plus the four guard files. A skipped or cut-off step fails that run.
  - A missing or nonregular mandatory guard fails before pytest starts; guard files are never silently dropped from the selection.

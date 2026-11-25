### Technical guide command migration

The copy-edit guard permits one exact command correction: the existing Bash block containing the broad pytest invocation followed by Ruff lint and format checks may become the single command `python -m commonplace.guards.prepush --manual --base origin/develop`.

The guard recognizes complete Markdown fences before matching the block. It requires one original block, no pre-existing approved block, one approved replacement and no remaining original block. It substitutes only that matched span before running the unchanged technical-token comparison. This rule applies independently of the guide path.

Every token outside the matched block remains subject to the existing comparison, including URLs, routes, filenames, flags, environment variables, configuration sections and keys, protocol values and shell commands. The separate anchor and inbound-fragment checks remain in force. Unsupported options, a different base or module, partial migrations, arbitrary command removal and newly added broad commands are rejected.

Regression cases invoke the actual copy-edit guard with synthetic documents and exercise anchor preservation alongside the migration. Broad command strings are test data only. Verification uses affected test files and the repository guard files, never a full test suite.

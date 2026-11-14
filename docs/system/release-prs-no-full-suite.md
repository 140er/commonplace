### Release pull requests follow the no-full-suite rule too

This belongs to the Decisions section.

- A pull request from `develop` into `main` is checked like any other: a gate PASS, the affected tests, and the fresh-user release journey on the exact head. No check runs the full test suite for it, as for every other pull request, batch or release. The owner's go still decides the merge. The rule text lives in `CLAUDE.md` (`AGENTS.md` links to it).

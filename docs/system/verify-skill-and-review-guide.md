### Verify skill and review guide

This belongs to the Decisions section.

- `.claude/skills/verify/SKILL.md` says to run `python -m commonplace.verify` and paste its evidence block into the PR description. The command runs lint and format checks on changed Python files, affected tests, and all four repository guard files, then prints a Verify evidence block that includes the git SHA. Test selection never falls back to the full suite. `.claude/` is ignored by `.gitignore`, so the skill is tracked with `git add -f`.

- `REVIEW.md` tells the merge gate what to weigh in this codebase. The gate reads it from the base branch, so a PR cannot change the rules that judge it.

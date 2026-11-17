### Verify skill and review guide

This belongs to the Decisions section.

- `.claude/skills/verify/SKILL.md` says to run `python -m commonplace.verify` and paste the output. The command runs the narrow checks and prints a Verify evidence block that includes the git SHA. `.claude/` is ignored by `.gitignore`, so the skill is tracked with `git add -f`.

- `REVIEW.md` tells the merge gate what to weigh in this codebase. The gate reads it from the base branch, so a PR cannot change the rules that judge it.

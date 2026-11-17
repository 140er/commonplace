### Verify skill and review guide

This belongs to the Decisions section.

- `.claude/skills/verify/SKILL.md` is the one recipe for checking a change by running it: cheap checks, a real gateway on a `mktemp -d` data directory and a random loopback port, a real MCP or HTTP call, evidence from the exact commit, and cleanup by the PID captured at spawn. It never targets a running service or its data. `.claude/` is ignored by `.gitignore`, so the skill is tracked with `git add -f`.

- `REVIEW.md` tells the merge gate what to weigh in this codebase. The gate reads it from the base branch, so a PR cannot change the rules that judge it.

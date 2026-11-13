### Agents improve their own instructions only through reviewed edits

This belongs to the Decisions section.

- An agent writes down a lesson with the outcome that taught it. When the same lesson repeats it proposes a small edit to its own instruction file as a PR or staged diff. It never rewrites instructions silently and never merges its own instruction PR; the owner decides.

- An agent may file a suggestion card (problem, proposal, expected benefit, cost) for a better way of working or a feature idea. It never acts on its own suggestion: the card goes to the coordinator, then the owner, and work starts only after the owner's yes.

- The hard rules still win: no secrets, no merges without the owner, no bypassed gates, no full test suite, and lean usage (no extra long threads, no polling). The rule text lives in `CLAUDE.md` (`AGENTS.md` links to it).

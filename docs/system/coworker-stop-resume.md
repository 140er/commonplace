### Stop and Resume the Coworker from Project settings

- The owner can stop a Coworker (the existing owner-only force-release) and resume it (new `owner-resume` route) from the project's Coworker row, each behind a confirm. The checkpoint now returns a short `history` of lease claims, releases, owner stops and owner resumes, and the row lists failed card starts with their reason.
- Decision: Resume runs the ordinary `resume` and hands the claim straight back inside the same call, so the owner never holds a lease and no token leaves the server; it is a web action, not an MCP tool, and stays owner-only. The history reuses the append-only journal, with no new table.
- Not changed: voiding a step, reassigning it and retiring an experiment stay API-only; they are the follow-ups.

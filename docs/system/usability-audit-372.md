### Web app usability audit (slice 1)

- A browser-only walk-through of the owner and member flows found that the controls for config accept/reject with a diff, release, dropping a reservation and priority all exist, but sit in places nothing points to and use different words from the agent side. Config proposals and release waits do not reliably reach Today.
- Decision: record the findings and a ranked gap list in `docs/usability-audit-372.md`; the board owns the follow-up cards. No product code changed.
- Not changed: starting agent runs (needs a registered machine) was not exercised.

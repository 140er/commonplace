- **Owns:**

  - The recovery view under the coordinator status in Project settings (`web/static/orchstatus.js`): the restore status, the project's open findings, the role's steps with the run and config version they belong to, and the one owner action it offers, acknowledging a restore.
- **Promises:**

  - Reading comes first and writes nothing. The restore, findings and steps lists come from the existing restore status and checkpoint reads; the page claims no lease, sends no heartbeat, stamps no result and voids, reassigns or retires nothing.
  - Acknowledging a restore is owner-only in the page (members see why there is no button) and in the server. The owner first reads a plain-language preview of what it does (every configured project re-checks its Todo cards from the start; no lease is claimed and nothing is started; work from the time of the backup may have run twice or not at all), then types the exact text the server shows. Cancelling sends nothing and forgets the typed text. The outcome is shown after the status is read again.
  - Findings explain where each went and why this page cannot acknowledge it: only the owning role, with its own lease, can, and an ownerless finding says nobody can. Unknown states, parked reasons and step states are named in words, never dropped or left empty; an unreadable restore status or a server without findings says so instead of showing an all-clear.
  - Voiding a step, reassigning it and retiring an experiment are not offered; the page says so. Ending a lease keeps its existing confirm.
- **How it fails:**

  - A refused or failed acknowledgement keeps the restore pending and says nothing was changed (not the owner, text does not match, already acknowledged, project gone, other failure); the owner can retry with the preview still open.
  - An unreadable restore status shows its own error while the rest of the block still renders.
- **Tests:**

  - `tests/orchstatus.mjs` (run by `tests/test_orchstatus.py`) covers the restore preview and exact-text gate, cancel, single send, refusals that keep the old state, the member view, unknown and unreadable states, findings and steps lists, and that no other action is offered.

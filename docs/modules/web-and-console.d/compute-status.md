- **Owns:**

  - The status line and job list at the top of Compute in the Deck look (`deck-compute-work.js`, mounted by `deck-compute.js`): one plain sentence with the counts of running, waiting and stuck jobs and the last activity, and under it one row per job with its card (a link to the card), the bot of its board, the machine and its state. The numbers are counts of the start jobs that `GET /api/topology/node` reports under `agents.runs` for every machine node recorded as a runner (up to twenty), read again on each refresh; the bot name comes from `GET /api/bots`.
  - The Management tab keeps the technical settings in one closed "Advanced" disclosure (`compute.js`, `compute.css`): Gateways, Targets and Passive scopes, each opening with a one-line plain explanation. Scripts, passive observations and execution proposals stay outside it, and the plain "Add a machine" entry is unchanged.
- **Promises:**

  - Running is a started job with no end reported, waiting is a queued, leased or starting job, and stuck is a job whose outcome is unknown or that no machine picked up for thirty minutes. Last activity is the newest job update or worker connection use among the machines that could be read; a machine poll never counts. A stuck job is flagged with a next step that links to its card, where the existing Runs section cancels or resolves it; the screen itself writes nothing.
  - What cannot be counted is said, never shown as zero: with no runner machine on the topology, or when no machine could be read, the line says running work is not reported yet, and when only some could be read it says so.
- **How it fails:**

  - A failed machine read leaves that machine out and adds "Some machines could not be read"; a failed bot read falls back to the project name; a refusal or an aborted read leaves the status of the previous refresh until the next one.
- **Tests:**

  - `tests/commonplace_deck_compute.mjs` covers the classification, the status line for busy, idle, stuck, unknown and partial reads, the job rows with card links and bot names, stuck flags with their next steps, the enrollment fallback and escaping; `tests/compute_ui.mjs` (case `advanced`) covers the Advanced grouping, the plain explanations and that the open state survives a re-render.

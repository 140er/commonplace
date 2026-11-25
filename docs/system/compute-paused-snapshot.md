### Pausing the Overview freezes its last completed server snapshot

Pause stops every automatic Overview read, including a read requested by an Add a machine check. A response already in flight cannot replace the list, its read time or the open details after Pause. A late reply also cannot clear a newer request's loading state or resume polling.

Refresh and opening details remain explicit reads while paused. Resume reads immediately, even if an older automatic request is still pending. Normal session and workspace checks remain authoritative; an expired-session refusal still reaches the sign-in handler.

The shared UI contract and real-controller regressions are recorded in `docs/modules/web-and-console.d/compute-paused-snapshot.md`.

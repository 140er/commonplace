### Invite confirmation keeps its Origin without exposing a referrer

A native form POST from the no-referrer invite page can send an opaque Origin,
which the same-origin guard correctly refuses. The confirmation now uses a
same-origin fetch from an external script allowed by the existing CSP. A
successful redirect opens the app with the replacement member session; a refusal
shows its readable invite page and next action. Network failures offer retry.

Join additionally requires an explicit valid Origin. Missing and opaque Origins
are refused even with same-origin fetch metadata. The shared guard's behavior for
other routes, the route inventory, invite lifetime and single-use transaction,
email binding, response policies and session rotation are unchanged. Confirmation
requires JavaScript; the page explains the next step when it is disabled.

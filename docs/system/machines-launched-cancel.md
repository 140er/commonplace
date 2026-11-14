### Cancelling a launched agent through the machines layer

- The runner now accepts a cancel for a launched agent as a stop request (confirmed worker stop): the start stays `started` and carries a stop status until the runner confirms the stop.

- `machines.cancel_start` passes that state through, so a launched agent answers `started`, not a refusal; before the change it refused with `already_finished`.

- A start that is already cancelled or failed is still refused as finished, and an unknown start as not found.

- The Coworker stop tool still only offers queued, leased and starting starts and reports a launched one as already launched; widening it to request stops is a separate decision.

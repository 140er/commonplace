### Webhook dispatcher recovery

- Each delivery pass contains unexpected errors from reconciliation, due-delivery lookup and delivery.

- An error is logged without its private message and the dispatcher waits on its stop event before continuing: 5 s initially, doubling after consecutive errors to a 300 s cap.

- A successful pass resets the backoff; stopping interrupts the wait.

- A `sqlite3.ProgrammingError` ends the dispatcher silently only when its ControlDB is closed; otherwise it follows the logged, bounded-backoff recovery path. Exit always removes the event listener.

- This keeps a temporary failure from silently ending background delivery.


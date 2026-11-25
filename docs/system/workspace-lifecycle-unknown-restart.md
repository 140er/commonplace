### Restart requests need a reported workspace phase

A legacy cloud workspace can have a saved state of ready without a lifecycle row or an executor report. The overview treats its effective phase as running but displays Unknown because nothing has reported whether it is running. Restart admission must check that effective phase against the report time, rather than treating the default lifecycle phase of not_started as trustworthy.

For this case the action description and confirmation preview refuse restart with state_unknown, and the request endpoint returns 409 without creating a request or changing its version basis or audit trail. The owner may still request a stop. A stop only records a request: no lifecycle executor or start and stop buttons are delivered by this change.

The regression covers the existing ready-state seam through the request and preview handlers. Additional checks refuse members and agent credentials, including an agent credential sent with an owner cookie, before any body read on start, stop, restart, withdraw, drain and resume. If writing the workspace audit fails, both a new start request and withdrawal roll back with their original request and version basis intact.

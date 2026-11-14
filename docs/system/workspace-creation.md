### Workspace creation from the switcher

A signed-in person can create another empty isolated workspace from Switch workspace. Name and confirmation precede the write; the result offers Open workspace separately. Only the creator joins, as owner, and the old workspace is unaffected. Classic has equivalent behavior; Deck uses the shared visual tokens.

Creation reuses signup's workspace provisioning function and plan hook. The endpoint requires a browser session and an explicit same-origin request, refuses bearer credentials, validates names through the shared sanitizer, audits provisioning and refusals, and commits a person-scoped request id with the workspace. Replays create nothing further. Five creations per rolling hour is a durable person limit; closed signup also closes new creation. Plan-hook refusals roll back the write and show their reason. Creation request rows are included in the account export/deletion classification.

The Deck design round keeps creation secondary to switching. Workspace names and roles are separate row columns, the current row has a tick, and the shared dialog has a 400px desktop width with 24px padding. Creation refusals replace the confirming controls with a reason and Close. Name entry catches overlong and already-listed names in the browser. Classic and the server contract retain their previous behavior.

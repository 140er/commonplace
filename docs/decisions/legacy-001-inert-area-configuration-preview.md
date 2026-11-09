### Inert area configuration preview

Area declarations can be parsed, rendered, diffed and proposed, with up to eight
distinct sub-orchestrators directly under the single parentless orchestrator.
Area, parent and kind stay frozen in experiments. Store acceptance and direct
write reparse then refuse any area-bearing config with `area_not_supported`
(400, path `roles`) before effects, for live and experiment channels alike.
Refused accepts preserve the proposal; refused writes roll back their insert.
Accepted versions, cursors/generations, leases, jobs and events stay unchanged.
This preview creates no area seats, credentials, subscriptions or runtime, adds
no activation switch, and keeps existing owner/local CLI and no-area behavior.


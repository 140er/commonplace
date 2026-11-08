# Instance console

Open **Instance console** from the account menu, or use `/app/#/instance-admin`.
It is available to a signed-in person whose current server user has the instance
admin flag, including an admin who is only a member of their current workspace.
Workspace ownership and agent permissions alone do not grant this authority.
The separate workspace Admin page retains its existing owner requirements.

This release is read-only. It shows management metadata across workspaces:
people and legacy memberships, connection lifecycle metadata, spaces/projects,
effective settings and limits, and filtered audit entries. A Space and a project
share one board inventory. No membership is created for the viewer, and viewing
grants no content access. Memory taxonomy, memory text, cards, messages, files,
credentials, login links, repository URLs and raw logs are excluded even if the
viewer is also a member. There is no impersonation, session switch or content
drill-down. Unavailable future actions are explained in text, without mutation
buttons or endpoints.

The existing installation bootstrap creates the first admin. Ordinary account
creation and workspace owner grants do not promote an instance admin. There is
currently no supported account promotion command in the inspected interface.

## Read API

Every path below is relative to `/api/instance-admin/` and accepts GET only:

| Path | Management metadata and optional filters |
| --- | --- |
| `workspaces` | Workspace identity, account association, plan limits and orchestration state |
| `people` | One account/membership per row, including accounts without memberships; `user_id`, `role`, `disabled` |
| `connections` | Credential-free connection metadata; `user_id`, `state` |
| `workspaces/{workspace_id}/spaces` | Existing active and archived boards, coordinator, safe settings and project caps |
| `settings` | Effective operator settings, explicitly read-only |
| `workspaces/{workspace_id}/settings` | Account-plan association, effective limits, orchestration and paginated project caps |
| `audit` | `actor_user_id`, `actor_connection_id`, `action`, `decision`, `from`, `to` |

All reads accept an exact `workspace_id` filter, `limit` (1–100, default 50)
and an opaque `cursor` where pagination applies. Instance settings have no
cursor. Filters are exact; duplicate or unsupported keys are rejected. Audit
time bounds use UTC `YYYY-MM-DDTHH:MM:SSZ`. Actions are the delivered
`instance_admin.read.*` actions and `instance_admin.denied`. Audit decisions
use fixed categories; historic entries with unknown categories display `legacy`.
Raw historic reasons and unrecognized target identifiers never reach clients.
Audit pagination pins its sequence ceiling before its own read audit is appended.
Inventories order by creation time then stable identity; membership ties use workspace identity.
Cursors bind the actor, endpoint, target and filters, and expire on process restart.

Legacy membership removal is not tracked and is returned as null. Missing pause
columns likewise project null; GETs never migrate a store. An absent workspace
store is `unknown` / `not_configured`, with an unknown count, rather than a zero.
A corrupt or unavailable source produces safe 503 `source_unavailable`.
Missing targets are 404 `not_found`; invalid input is 400 `invalid_request`.
Successful responses are `no-store` and include one request observation time.

Plan assignments are effective only when plans are enabled. Shared workspaces
without an account have no account-plan limit. Project caps preserve accepted
configuration constraints, configured caps and existing default precedence.
There is no new global numeric quota or console limit editor. Operator rate and
request settings are read-only. Ordinary content quota enforcement is unchanged;
authorized console reads skip home-account storage and load admission so a full
home account cannot block management metadata access.

## Authority, waits and audit retention

The server checks the session person and current exact user flag for every
request, before query, target or body inspection. `/api/me.can_manage_instance`
is navigation only. A capability lookup failure preserves `/api/me` 200 with
both instance flags false; `can_admin` retains its person/owner meaning.
Protected console and operational endpoints still fail closed with 503.

Authenticated authority failures use exactly 403
`{"error":"Instance administrator access required"}`. Registered signed-out
routes use the existing 401 challenge. Host/framing checks and exhausted rate
budgets precede authority denial. The existing identity bucket is charged once
before writing a target-free denial audit; exhaustion returns 429 with
`Retry-After` and no extra denial audit. Namespace authority precedes same-origin
JSON validation; eligible session writes retain that protection. Responses close
the connection before refusals that leave body bytes unread.

Control and existing board/config metadata are read through bounded, read-only
connections without store construction, claim expiration or seat creation. A
request has a two-second server budget, including coordinated audit lock and
SQLite waits. Every accepted read/filter appends its action with the real actor,
the target workspace or instance, and a fixed decision/reason. Success requires
the audit append: audit failure returns 503 `audit_unavailable` without metadata.
Denial remains 403 if its audit is unavailable; a fixed diagnostic records that
residual. Sessions use a null audit connection ID; connection denials retain the
actual credential identity.

Audit storage is append-only and unbounded: N accepted reads per day add at least
N rows per day. Rate limits bound bursts, not retention. Plan database and backup
capacity accordingly; disabling rate limits retains the operator's explicit risk.
The console does not poll. Sections have independent five-second deadlines over
transport and JSON decoding, refresh/retry controls and pagination. Target changes,
navigation, sign-out, expiry and capability loss clear metadata and abort requests;
late results cannot repopulate the previous workspace.

## Later membership actions

Membership mutations are not shipped here. The agreed boundary for a later
release is explicit target management: invitations, role changes, disable and
reactivation affect the target's normal membership, never the acting admin's.
Normal target member/owner access includes memory and cards after verified
sign-in. Same-user self-grants must be refused; owner selection must be explicit,
confirmed and audited, with self/last-owner/last-usable-admin guards preserved.
An admin may control another invited email address and sign in as that target.
That accepted residual cannot be closed by comparing user IDs; the invite must
be audited. No instance flag delegation is planned in this console.

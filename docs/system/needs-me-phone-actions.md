### Phone card actions in Needs me

With the existing phone-actions switch enabled, `GET /api/needs-me` uses the
website's card queue builder for a current person whose OAuth connection holds a
live `cards:decide:person` or `cards:approve:person` capability. This is an additive
use of the existing response fields: no replacement field, new route, table,
OAuth scope or consent wording is introduced. The authenticated bearer remains
non-person outside this projection.

The person's current readable boards and website decision rules apply before
projection, including the chosen completion reviewer and independent proposal or
plan review. An unreadable card contributes neither content nor count. The queue
does not include person-only setup or administrative attention groups. Connections
without a live card capability, including unconsented native clients, ordinary
OAuth clients and non-person credentials, retain their previous response or
refusal. The switch is checked on every read.

The existing card buckets are `asks`, `done_review`, `backlog_from_agents`,
`ready_to_merge`, `stale`, `plans` and `approvals`, each a non-null array of card
objects. `count` is the integer number of distinct cards, including cards with no
advertised phone action; `approvals_truncated` is a non-null boolean. A card may
appear in more than one bucket.

| Card field | Type and nullability | Meaning |
| --- | --- | --- |
| `number` | positive integer, non-null | Card identifier used in the action URL. |
| `version` | positive integer, non-null | Current version, copied into the write body. |
| `actions` | array of strings, non-null, possibly empty | Existing web action IDs, in web order, filtered by the live capabilities below. |
| `why` | string, non-null | The web queue's explanation for this person. |
| `ask` | string or null | The question, when present. |
| `ask_options` | array of strings, non-null, possibly empty | Exact offered choice labels, submitted unchanged as `choice`. |
| `ask_blocks` | array of objects, non-null, possibly empty | Existing structured question details; option labels match `ask_options`. |
| `review_kind` | string or null | Existing `decision` or `completion` kind. |
| `proposal_status` | string or null | Existing proposal state, including `pending`. |
| `approval_kind` | optional string, omitted when inapplicable | Existing approval classification, such as `origin` or `origin_configuration`. |
| `labels` | array of strings, non-null, possibly empty | Existing card labels, including `plan` when applicable. |

`actions` is the permission-filtered affordance list. Other existing card fields,
including `can_approve`, describe the person's web context and do not grant the
phone another action. Labels and kinds retain their website meanings:

| Action ID | Website label / kind | Required capability | Write |
| --- | --- | --- | --- |
| `answer` | Offered choice label or Answer / decision or completion | `cards:decide:person` | `POST /api/person/cards/<number>/decide` with `version` and `choice` or `reply`. |
| `accept` | Accept / completion | `cards:decide:person` | Same decide route, with `version`, `reply` and `then: "done"`. |
| `approve` | Approve / proposal or plan; Release origin / origin | `cards:approve:person` | `POST /api/person/cards/<number>/approve` with `version`, `approve: true` and an optional `reason` (required for origin release). |
| `reject` | Decline / proposal; Reject / plan | `cards:approve:person` | Same approve route, with `version`, `approve: false` and optional `reason`. |

Other web action IDs, including navigation, recovery and configuration, are not
advertised to the phone by these capabilities. With only the decide capability,
approval items have `actions: []`; with only approve, questions and completion
reviews have `actions: []`.

Example card in `asks` (other existing card fields omitted):

```json
{"number":17,"version":3,"actions":["answer"],"why":"The owner asks you to choose.","ask":"Proceed?","ask_options":["Proceed","Wait"],"ask_blocks":[],"review_kind":"decision","proposal_status":null,"labels":[]}
```

The client creates one `Idempotency-Key` per intended write: 1–128 ASCII letters,
digits or `._~-`. There is no server-issued action token or extra idempotency
field. Send the exact listed version, for example a decide body
`{"version":3,"choice":"Proceed"}`. Retry with the same key and identical body to
receive the original receipt; a changed body under that key returns 409. A stale
listed version returns 409. The write routes revalidate current consent, bearer,
person, card visibility and web authority, including on retries. A withdrawn
capability on an otherwise valid bound bearer returns 403; an unconsented bearer
retains its existing 404, and a revoked OAuth token returns 401. Listing an action
does not reserve it or authorize a later write.

The existing checked-write inventory and public-surface pins are unchanged: this
read projection adds no write branch or route. All table references remain literal.

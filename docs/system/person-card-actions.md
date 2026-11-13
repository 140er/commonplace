### Person-consented card actions

Phone card actions are an instance opt-in (`COMMONPLACE_PHONE_ACTIONS=on`). Enable
the setting before starting the service to install the protected prefix. The
switch is also checked at consent, code creation and use. With it off, the scopes
are absent from discovery, no new form can request them, and installed handlers
refuse existing grants. Re-enabling resumes unrevoked grants; disconnecting the
OAuth connection revokes its access and refresh tokens.

`cards:decide:person` means answer card questions and completion reviews using
the signed-in person's existing web rights. `cards:approve:person` means approve
or reject proposals and plans using those same rights. The legacy owner-delegated
`cards:decide` scope keeps its existing meaning. Neither new scope is a default
and neither expands another scope. Both require a fresh OAuth consent grant;
older tokens remain unchanged, including after a refresh.

The exact unticked consent strings are:

- "Allow this app to answer card questions and completion reviews as you in this workspace. It can answer only items you may answer on the website. Each answer records your identity and this app's name. Disconnect this app to stop access."
- "Allow this app to approve or reject card proposals and plans as you in this workspace. It can act only on items you may review on the website. Each action records your identity and this app's name. Disconnect this app to stop access."

The owner must approve the final scope names and consent strings before merge.

The only new HTTP surface is the authenticated `POST /api/person/cards/` prefix:
`<number>/decide` answers a question or completion review, and `<number>/approve`
reviews a proposal or plan. **New public routes: none.** The private prefix is
needed to keep person-consented actions separate from ordinary app and web
routes. Existing public-route pins are unchanged.

Both actions require `Idempotency-Key` (1–128 ASCII letters, digits or
`._~-`) and a positive integer `version`. Decide accepts the existing `choice`,
`reply` and `then` fields; approve requires boolean `approve` and accepts
`reason`. Other fields are refused, including identity or workspace selectors.
Card numbers resolve only within the token's consented workspace. Errors use
the existing board refusal forms. An exact retry returns the original result;
a reused key with different arguments returns 409. Live token, consent and
visibility checks still apply to retries.

Every action uses the person's current web principal for the existing operation.
Chosen completion reviewers keep their existing authority; members cannot answer
owner-only asks or approve owner-only proposals. Agent-started, child, machine,
worker, project-seat, coordinator, PAT and unconsented OAuth connections have no
authority here. These scopes do not authorize account data, events, settings or
other person-only routes. The existing authenticated request budget also covers
these routes.

The control database stores consent associations in `oauth_person_grants`,
classified for account export and deletion. Receipts live in
`person_card_receipts` in the board database so they commit atomically with card
changes. History records the person and client name; control audit rows record
each granted scope and successful action. No access or refresh bearer is written
to either table or included in an audit reason.

This is slice S1 of card #497. The per-person event feed and the fresh web
re-authentication deletion token belong to later slices. Neither authority is
introduced here, and the existing account deletion safeguards stay unchanged.

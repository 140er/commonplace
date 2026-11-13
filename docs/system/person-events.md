### Person-consented phone event feed

S2 of card #497 adds the separate `events:person` consent capability. The OAuth
scope remains exactly `memory:read`; existing clients and tokens gain nothing.
Only a new native PKCE consent can grant it, through an initially unticked choice.
The proposed exact consent text requires the owner's approval before merge:

> Allow this app to read your notifications and activity feed as you in this workspace. It can show only what you can see on the website. Nothing can be changed through this. Disconnect this app to stop access.

`GET /api/events?cursor=<opaque>&limit=50` reuses the existing web route. With
this capability it returns the same person's web event sources and order
(increasing event-log order), projected to minimal phone fields. The member visibility query is
shared with the web feed: only readable active boards, with the existing extra
restriction for space-message events. Owners retain their web workspace log.
Private bot threads are not event sources. Push notification processing continues
to consume the same event log internally; no push settings or delivery authority
is added. The feed is activity, rather than a replacement for the web Needs me
queue or a promise that every event requires action.

The instance opt-in and live kill switch are the existing
`COMMONPLACE_PHONE_ACTIONS=on`. Without consent or while off, the route retains
its existing refusal for ordinary phone OAuth. Person, membership, workspace,
client, live token and consent bindings are checked on use. Other bearer kinds
cannot acquire this phone response. The capability grants no writes or unrelated
routes; the authenticated OAuth principal remains a connection globally.

Response fields are always present and non-null:

- `events`: array of `{id: string, kind: string, at: string, summary: string, target: string}`.
- `cursor`: opaque string for the next poll or page, including an empty page.
- `has_more`: boolean; continue immediately with the cursor when true.
- `reset`: boolean; when true the page is empty and the client should refresh its open view before adopting the cursor.

`id` is stable for an event and consented connection, including after refresh and
restart, and discloses no workspace sequence. `at` is UTC ISO 8601 ending in `Z`.
`summary` uses the web event's title when present, otherwise its kind; it does not
return event payloads, message bodies or actor metadata. It reuses the phone chat
preview: normalize HTML and entities, apply the web redactor, then truncate to
240 Unicode characters without splitting a redaction marker. `target` is a
validated in-app route (`#/card/<number>`, `#/space/<slug>`, or `#/`), never an
external URL. Clients render summaries as literal plain text.

```json
{"events":[{"id":"evt_0123456789abcdef0123456789abcdef","kind":"card.updated","at":"2026-11-13T19:00:00Z","summary":"Ready for review","target":"#/card/12"}],"cursor":"opaque-token","has_more":false,"reset":false}
```

An omitted cursor starts from the beginning of the currently visible history.
Limit defaults to 50 and accepts integers 1–100. Unknown, duplicate or malformed
arguments return 400. Cursors are authenticated with an independent per-grant
secret and bound to the person, workspace, client and connection; invalid or
cross-binding cursors return 400. Readable-set or role changes reset using the
same visibility epoch behavior as the web. Hidden member activity changes no
content, cursor, id, count or ordering.

There are no new routes: the existing authenticated `GET /api/events` is extended
so phone and web visibility share one implementation. Surface inventories and
disabled baselines are unchanged. Existing gateway rate limits apply. Grants
use the existing consent audit row; reads add no audit rows, matching the web.
No bearer, cursor, summary or cursor secret is logged or added to audit.

No new table is introduced. The nullable `cursor_secret` column belongs to the
already classified `oauth_person_grants` credential rows; exports redact it and
account deletion removes it with those rows. Existing rows migrate with null
secrets and acquire no event capability. All added SQL uses literal table names.
The account-deletion token remains S3 and is not authorized by this capability.

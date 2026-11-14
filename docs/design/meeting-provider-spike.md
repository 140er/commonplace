# Meeting-join bots: first provider spike

Card #463, slice 6, spike only. Status: research note; no adapter code, no account, no app
registration. It answers the first half of section 13 item 6 and section 14 question 1 of
`docs/design/meeting-join-bots.md`: which provider's official interface can be the first adapter.
Everything below comes from public vendor documentation read on 2026-11-13; each fact names its
page. "Unknown" means the documentation I could read does not say, not that the answer is no.

## 1. Bar a provider must clear

From the design note and the repo's local-first rules:

- (a) a named non-human participant that can join a meeting (the bot is visible, with a name that
  says what it is);
- (b) an audio or caption stream the hub can capture, so transcription stays on the owner's machine;
- (c) chat send (the fail-closed announcement of section 6 needs it) and chat receive (addressed
  answers of section 9 need it);
- official interfaces only, no headless browser, no screen capture (section 15);
- minimum scopes (section 10), BYOK-style opt-in, no hosted relay run by us, audio leaving the machine
  only through the provider's own stream.

## 2. Candidates

Four were read in depth: a workplace chat and meeting suite (Teams), a conferencing service (Zoom),
an office suite's meeting product (Google Meet), and a team chat product's audio feature (Slack
huddles). A fifth, Webex, could not be verified (see the table).

| Provider | (a) Named non-human participant | (b) Audio or caption stream | (c) Chat send and receive in the meeting |
|---|---|---|---|
| Microsoft Teams (calling and meeting bot, application-hosted media) | **Yes.** A calling bot joins a meeting through the Graph calls API. The roster label is not stated in the docs I read, so whether the server-built name of section 6 can be set is unverified. | **Yes, mixed.** 16 kHz, 16-bit audio in 20 ms frames; the docs name the active speakers per frame but describe no unmixed per-participant streams. Transcripts exist only after the meeting, from Microsoft. | **Send: likely yes**, a bot in the meeting chat is documented for the `groupchat` scope, but no send permission is named for meetings. **Receive:** all chat messages with resource-specific consent `ChatMessage.Read.Chat` for group chats; the docs do not say outright that this covers a meeting chat. |
| Zoom (Realtime Media Streams, RTMS) | **Partly.** No separate bot participant: an RTMS app is tied 1:1 to a participant, who is shown to everyone through an "active app" notice. The Linux Meeting SDK page says it "does not support bots or AI notetakers". | **Yes, strong.** Mixed or per-speaker audio, L16 or Opus, 8 to 48 kHz, plus a per-speaker transcript from Zoom. | **Receive only, public chat only.** No way to send chat into a meeting from RTMS or a Zoom App; staff replies on the developer forum say so. |
| Google Meet | **No.** The Media API that allowed an app to join was **discontinued and will not reach general availability**; its docs now return 404. The Meet REST API creates and inspects meetings only. Add-ons run in an iframe. | **No live stream.** Transcripts are post-meeting files in the organizer's Drive. | **No** chat API for in-meeting chat in the REST docs or add-on SDK. |
| Slack huddles | **No** API to join a huddle. | **No** audio scope or API. | **No** huddle chat API. Only a `user_huddle_changed` status event exists. |
| Webex | Unknown. Bots are documented for messaging spaces only; the meeting pages did not render. | Unknown. | Unknown. |

Sources:

- Teams: [calls and meetings bots overview](https://learn.microsoft.com/en-us/microsoftteams/platform/bots/calls-and-meetings/calls-meetings-bots-overview),
  [registering a calling bot](https://learn.microsoft.com/en-us/microsoftteams/platform/bots/calls-and-meetings/registering-calling-bot),
  [real-time media concepts](https://learn.microsoft.com/en-us/microsoftteams/platform/bots/calls-and-meetings/real-time-media-concepts),
  [application-hosted media requirements](https://learn.microsoft.com/en-us/microsoftteams/platform/bots/calls-and-meetings/requirements-considerations-application-hosted-media-bots),
  [join a call (Graph)](https://learn.microsoft.com/en-us/graph/api/application-post-calls),
  [bots in meeting chat](https://learn.microsoft.com/en-us/microsoftteams/platform/apps-in-teams-meetings/build-extensible-conversation-for-meeting-chat),
  [receive all chat messages](https://learn.microsoft.com/en-us/microsoftteams/platform/agents-in-teams/enable-receive-all-chat-messages),
  [meeting transcripts](https://learn.microsoft.com/en-us/microsoftteams/platform/graph-api/meeting-transcripts/overview-transcripts).
- Zoom: [RTMS overview](https://developers.zoom.us/docs/rtms/),
  [meeting experience](https://developers.zoom.us/docs/rtms/meetings/ux-overview/),
  [working with streams](https://developers.zoom.us/docs/rtms/meetings/work-with-streams/),
  [data types](https://developers.zoom.us/docs/rtms/data-types/),
  [Linux Meeting SDK](https://developers.zoom.us/docs/meeting-sdk/linux/),
  developer forum staff replies (not documentation):
  [post to in-meeting chat](https://devforum.zoom.us/t/allow-chatbot-to-post-message-to-users-in-in-meeting-chat/33561),
  [send messages in the meeting chat](https://devforum.zoom.us/t/will-we-soon-be-able-to-send-messages-in-the-meeting-chat/116075).
- Google: [Meet REST API overview](https://developers.google.com/workspace/meet/api/guides/overview),
  [Meet add-ons concepts](https://developers.google.com/workspace/meet/add-ons/guides/concepts),
  [Media API samples repository, deprecation notice in its README](https://github.com/googleworkspace/meet-media-api-samples)
  (archived 2026; the overview page it links, https://developers.google.com/meet/media-api/guides/overview, returned 404 on 2026-11-13).
- Slack: [Calls API](https://docs.slack.dev/apis/web-api/using-the-calls-api.md),
  [`user_huddle_changed`](https://docs.slack.dev/reference/events/user_huddle_changed.md),
  [scopes](https://docs.slack.dev/reference/scopes.md).
- Webex: [developer docs index](https://developer.webex.com/explore/docs) (bots in spaces).

## 3. Requirements, consent and time

| Provider | Scopes and permissions | Hosting and tier | Per-meeting consent and disclosure | Approval time |
|---|---|---|---|---|
| Teams | Graph application permissions to join (`Calls.JoinGroupCall.All`, or the resource-specific `Calls.JoinGroupCalls.Chat`) plus `Calls.AccessMedia.All` for media; chat receive `ChatMessage.Read.Chat` (resource-specific). The calls and online-meetings permissions "require admin consent"; changing them repeats it. The resource-specific variants are consented by a chat owner or organizer per the permission table, though the same page also says only administrators or resource owners can grant app-only ones (conflict in the docs). | Bot registration with an Entra and Azure account; an HTTPS callback; **application-hosted media runs only on Windows Server with .NET, "in Azure" for production, with a public IP per instance and at least two cores**; the bot "can't be deployed as an Azure web app". The organizer needs a Microsoft 365 account and a Teams license. | Admin or chat-owner consent at install, not per meeting. Recording through the media API requires the policy-based `updateRecordingStatus` call; compliance recording notifies participants (visual or audio). Whether a bot from one tenant can join another tenant's meeting is not stated. | Sideloading in your own tenant needs no store review; the docs give no time for anything. |
| Zoom RTMS | `rtms:read` for events; media scopes such as `meeting:read:meeting_audio`, `meeting:read:meeting_transcript`; granular scopes cannot be optional; user-managed app. | Public HTTPS webhook (CA-signed certificate, signed with an HMAC, answer the URL validation within 3 seconds); outbound WebSockets for the streams; **Developer Pack credits (paid) needed**; Basic (free) accounts "not supported". | **Host approves each app** per meeting ("Always allow in my meetings" exists); Zoom shows a disclosure to everyone and anyone can open "View apps"; host can stop it. | App Review is "required" for anyone outside your own account; no time stated. A private app within one account needs none. |
| Google Meet | Meet REST scopes are sensitive or restricted (verification, a security assessment when stored on a server). Moot: no live media. | Cloud project with billing, Pub/Sub for events. | n/a | n/a |
| Slack | n/a | n/a | n/a | n/a |
| Webex | Not verified. | Not verified. | Not verified. | Not verified. |

Sources: the Teams, Zoom and Google pages above, plus
[resource-specific consent](https://learn.microsoft.com/en-us/microsoftteams/platform/graph-api/rsc/resource-specific-consent),
[`updateRecordingStatus`](https://learn.microsoft.com/en-us/graph/api/call-updaterecordingstatus),
[compliance recording](https://learn.microsoft.com/en-us/microsoftteams/teams-recording-compliance),
[Zoom RTMS add features](https://developers.zoom.us/docs/rtms/meetings/add-features/),
[Zoom App Review for RTMS](https://developers.zoom.us/docs/rtms/meetings/submit-app-review/),
[Zoom webhooks](https://developers.zoom.us/docs/api/webhooks/),
[Zoom RTMS troubleshooting](https://developers.zoom.us/docs/rtms/troubleshooting/),
[Zoom Developer Pack](https://zoom.us/pricing/developer),
[Meet REST authorization](https://developers.google.com/workspace/meet/api/guides/authenticate-authorize).

## 4. Fit with the local-first rules

- **No candidate is a clean pass.** Only one provider meets (a), (b) and (c) on paper. It is the
  heaviest to host.
- **Teams.** Meets all three, but application-hosted media is a Windows Server process, in Azure for
  production, reachable on a public IP. A hub on a person's own machine cannot be that process. The
  audio would pass through a cloud VM the owner runs before it reaches the hub, which is
  more than "the provider's own stream" and needs an explicit owner decision (D3). The same docs
  call the real-time media platform "in developer preview", say it is for "managed partners", and say
  such bots "are not recommended for AI agent scenarios". The calls and media permissions need tenant
  administrator consent.
- **Zoom.** Best audio and transcript surface and a visible disclosure Zoom itself shows to everyone, with
  host approval per meeting. But chat can only be read, never sent, and the app is not a separate named
  participant. Section 6 of the design note makes a delivered chat announcement the gate for capture
  and refuses adapters without `chat_send`. Choosing Zoom would mean amending the design to accept the
  provider's own disclosure notice as the announcement and dropping `bot stop` for Zoom meetings. That
  is a change to a consent rule, not an implementation detail, so it is an owner decision (D5).
  It also needs a paid Developer Pack and a public webhook endpoint.
- **Google Meet, Slack.** Out: no official live interface exists (Meet's was discontinued).
- **Webex.** Not enough verified information to compare; add a later spike only if the owner's
  meetings use it.
- **Zoom's Meeting SDK bot route** is not used: the SDK page says it is for human use cases, and
  `docs/design/meeting-join-bots.md` section 15 excludes hiding a bot or faking a person.

## 5. Recommendation

**Microsoft Teams, conditionally.** It is the only candidate whose official interface gives a joining
bot, an audio stream and in-meeting chat, and the only one that can satisfy section 6 without a design
change. It is also the heaviest: a Windows Server media host, a public endpoint, a tenant administrator
consent and a developer-preview platform. If the owner does not accept the hosting in D2 and D3, **park
slice 6**: no other candidate meets the bar without amending the consent rules, and the right
response is to say so rather than build an adapter that cannot announce itself. Zoom RTMS is the
fallback if the owner chooses to amend section 6 (D5); it would be the cheaper adapter to host
(no Windows server, outbound sockets) but needs paid credits and a different disclosure rule.

Risks to confirm in the first hour of slice 6, before any code: that the bot's display name can be
set on join; that `ChatMessage.Read.Chat` delivers messages of a meeting chat (not only a group chat);
that the resource-specific consent path works for a bot joining a meeting without a tenant-wide grant;
how callbacks from Microsoft are authenticated (needed for `verify`); and the current link shapes
(`parse_link` will accept an exact allowlist of Microsoft's meeting hosts and paths, nothing else).

## 6. Owner decisions needed

Only the owner can make these; none was done in this spike (no account, no registration).

1. **D1, go or park.** Accept Teams as the first provider on the terms below, or park slice 6.
   Design note section 14 question 1: pick the suite your own meetings use. If that is not Teams, say
   which.
2. **D2, accounts.** A Microsoft 365 tenant with Teams licenses for the people who will invite the
   bot, and an Entra and Azure account in it to register the bot and manage consent.
3. **D3, where the media host runs.** A Windows Server host with a public IP, and "in Azure" for
   production per Microsoft. Either an Azure VM run by the owner (audio passes through it; accept it
   as the owner's own machine in the design's sense, or refuse), or an on-premises Windows host for
   development and testing only. Also who writes and maintains the .NET media bridge: it cannot be
   Python.
4. **D4, tenant consent.** A tenant administrator grants the application permissions (or the owner
   confirms that resource-specific consent by the chat owner is enough), for the join, media and chat
   scopes. Also: own-tenant meetings only in v1, since external-tenant meetings are not documented.
5. **D5, only if Zoom replaces Teams.** Amend section 6 so Zoom's own in-client disclosure and host
   approval count as the announcement, for a provider without chat send. Plus: paid Developer Pack
   credits, a public webhook endpoint, and an app registration (private within one account; App Review
   if used outside it).
6. **D6, public callback.** Accept one public HTTPS endpoint on the hub for provider callbacks
   (`caps.transport = 'webhook'`), or wait for the socket transport follow-up of section 13.
7. **D7, a test meeting.** One dedicated meeting in the test tenant for the manual live check, run
   by the owner; the automated suite never touches the network.

## 7. Proposed slice plan for the adapter PR

One PR for the adapter, built in this order, after D1 to D4 are answered. The bridge in step 5 may
become its own PR if it lives outside Python.

1. **Contract suite first.** One parametrized suite over `[fake, teams]` in `tests/`, using hand-written
   generic payload fixtures. It covers `parse_link`, `join` idempotency with the same key,
   `find`, `leave` idempotent, `events` normalization (`joined`, `admitted`, `participant_*`, `chat`
   with `addressed` and `bot` set, `removed`, `meeting_ended`, `tick` at least every few seconds),
   `audio` as 16 kHz mono PCM frames with an optional `ext_participant`, `post_chat` splitting to
   `caps.max_text`, escaping and neutralizing broadcast mentions, and `test` returning only the fixed
   `REASONS`. RED on base: the real adapter is missing.
2. **No live network.** An autouse fixture blocks sockets and name resolution in these tests; the
   adapter takes its HTTP client and clock as arguments, so tests inject recorded fixtures.
3. **`parse_link` refuses foreign hosts.** Cases: another host, a look-alike suffix
   (`teams.microsoft.com.evil.example`), user-info (`@`), a port, `http`, uppercase or Unicode
   look-alikes, a redirect target, and an empty or oversized path. The accepted shapes are an exact
   allowlist confirmed in the first hour; nothing is fetched while parsing.
4. **Webhook verification.** `verify` rejects: a missing or malformed credential header; a wrong issuer or
   audience; a bad signature; an expired token (stale); a body for another connection or another
   meeting (cross-workspace confusion); and a replay (same `(meeting, ext_event)`). Each rejection
   returns a fixed reason and logs no token or body.
5. **Control plane.** `test`, `join`, `leave`, `find`, `post_chat` over the injected client, with
   sealed credentials read only inside the call. The audio stream and call-state events come from the
   media bridge through a narrow local contract, so the Python side never needs a Windows runtime.
6. **Registration, off by default.** The adapter registers only when the owner has configured a
   connection; a clean workspace still lists no provider (the clean-workspace test of every slice).
7. **Re-run slices 2 to 5 unchanged** against the real adapter through the same parametrization: the
   announcement gates capture, removal and `bot stop` discard, no frame before `sent`, an addressed
   message answers once, an unaddressed one is never stored.
8. **Manual live check**, owner-run in the test meeting (D7), recorded in the PR as a checklist; never
   in CI.

Proof for the adapter PR: RED on base (adapter and contract tests missing), GREEN on head; the contract
suite shows the fake and the real adapter passing the same cases; the foreign-host, forged-webhook,
stale-webhook and replay tests each fail when their check is removed.

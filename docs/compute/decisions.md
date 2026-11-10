# Overnight build decisions

2026-11-09. These implementation decisions define contributor-facing product,
runtime and transport boundaries. They do not replace independent review or
provide deployment permission. The source contract is pinned to
`18227ce51175dd906479fd1d79aa01c7c85b2660`.

## Deliver the coworker journey first

Create a persistent named bot, open a private DM, submit a goal, receive a fast
response, observe durable work while continuing to chat, answer an authorized
decision, receive evidence in the originating chat, and reconnect without losing
state. Groups, explicit recipients and scoped verbatim memory belong to this
core, not a later decorative phase. Compute M1 develops alongside it; real SSH
or hardware provisioning is not a dependency of chat persistence or responsiveness.

Keep existing vanilla-JS UI, authenticated users/connections, canonical boards,
model configuration and memory facilities. The new conversation store supplies
the previously absent DM/group audience boundary. A shared persona shares
configuration, not another conversation's private memories or transcript.

## Separate front conversation from background work

Adopt `coworker-contract.md`: persist message and addressed reply job together;
return before any provider call; use bounded concurrent front workers and
conversation/bot ordering; execute long work through a separate durable task/run
bridge. Preserve accepted messages rather than silently replacing older ones.
Provider absence produces an explicit configuration state without fake replies.
Never silently configure an external provider or spend API credit.

Use explicit selected bot or an unambiguous mention; a sole bot may be inferred.
An unaddressed multiple-bot group message is stored without waking every bot.
Bot replies, recalled text and worker events never create autonomous reply loops.

Private memory is explicit, verbatim and conversation-scoped. Bounded relevant
context and current audience checks precede model use and reply publication.
Workspace wildcard memory access is not proof of permission to disclose into a
group. Removal hides a memory from future context through a tombstone; it does
not claim physical erasure of the original transcript or backups.

The internal person-only `BoardService.ensure_chat_task` binds an opaque dispatch
origin to its canonical card in one boards transaction. Exact replay finds the
same card; changed content conflicts. Private text reaches a shared board only
through explicitly authorized task intent. The front queue, a created card,
worker admission, actual execution, completion and confirmed cancellation are
separate facts, reflected truthfully in UI and persisted state.

## Choose a constrained SSH gateway for Compute

The default transport design is a dedicated forced-command SSH gateway helper
with independently pinned nested target SSH. Targets remain SSH-only; outbound
Go runners stay in M2. Typed bounded requests and approved numeric destinations
replace generic forwarding, arbitrary shell or an unrestricted network proxy.

Credential custody needs a narrowly scoped encrypted provider and separate
gateway/target bindings. Provisioning, confirmed fingerprints, rotation/revocation
and recovery must be explicit. Never use ambient credentials or plaintext
fallback. Real credentials and owner-installed gateway keys are future execution
inputs, not reasons to stop building isolated policy and fixture paths.

Discovery, enrollment and execution grants remain separate. Exact approved action
digests bind targets, credentials, arguments, script, paths, privilege, limits and
expiry. Missing transport, custody or confinement keeps only that remote effect
disabled; it does not block new messages, task state, cancellation or other chats.

Pure scope validation is now implemented without exposing transport or granting
authority. Authenticated storage, aggregate dispatch accounting, gateway helper,
credential provider and script execution are not implied by that module.

## Evidence and readiness

Use deterministic fakes to prove scheduling, persistence, privacy, deduplication,
cancel/recovery and control boundaries. Measure message receipt separately from
model reply, dispatch and worker completion. A deliberately blocked fake worker
can prove that another message completes before its release; it does not require
a ten-minute test or establish real model latency. Real provider quality/latency
and two-hop SSH require their own evidence before those capabilities are claimed.

Validate storage/API, runtime and UI changes together. Independent review is
required for readiness/merge claims; review by the implementing agent is not a
substitute. This document authorizes no deployment, live-device use, protected-branch
change, GitHub Actions, paid fallback or merge.

When changing these defaults, consider product, runtime and transport requirements
together and preserve the shared interfaces described in these contracts.

### Targeted coworker controls

`POST /api/coworkers/jobs/<originating-job-id>/controls` accepts only explicit
follow_up/stop intents with actor/operation request keys. Same-origin person
sessions, current conversation/origin/viewer/bot/visibility and exact canonical
origin/run bindings are rechecked before admission, effect and publication. A
workspace-crypto-aware task_controls journal commits a conservative uncertain
receipt before crossing stores; exact retries return that receipt and never
repeat possibly applied effects. Changed payloads conflict. Limits:4000 instruction
characters,100 immutable intents per job and10 projected receipts per viewer.

Follow-ups save only the explicit exact words as canonical task comments and
report that worker application is unsupported. Queued/leased stops use canonical
runner cancellation to fence launch. Starting stops record intent with termination
unconfirmed; already-started worker stop is unsupported by current runner APIs.
No control launches replacement work, approves a decision or treats task/front
completion as proof of termination. The UI retains stable attempts through lost
acknowledgements and reconciles receipts on reload while keeping the composer usable.

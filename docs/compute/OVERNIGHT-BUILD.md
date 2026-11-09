# Overnight build checkpoint

The isolated feature branch contains working application code and synthetic
integration evidence. Nothing was deployed or merged. Independent review and
release gates have not passed.

## Implemented

- Persistent named coworker personas, private DMs/groups, explicit routing,
  conversation-scoped verbatim memory and reconnect-safe message intake.
- Bounded asynchronous fronts and explicit /task admission into existing canonical
  cards and configured runner ledgers, while other messages remain usable.
- Per-viewer canonical task progress, comments, results, decisions and evidence
  in the originating conversation. No board result is copied into shared chat
  history. Current origin/viewer/audience/task authority fences publication.
- Explicit targeted follow-ups and stop requests with durable replay receipts.
  Follow-ups save exact task comments. Queued/leased stops fence canonical launch;
  starting stops remain intent, and ongoing-worker control is not supported by
  the current runner protocol. Front completion never implies worker termination.
- Owner Compute inventories, pending gateway/target enrollment, explicit passive
  scopes, exact proposals/approvals/cancellation and bounded execution evidence.
- Protected encrypted credential custody, independently pinned outer/inner SSH,
  participating local cross-process authority fencing and actual one-use
  challenge/permit admission on the existing authenticated SSH subsystem.
- Separate passive-purpose SSH admission, bounded cache observations, durable
  scope reservations, safe replay/recovery and owner collection/history UI.
- Immutable UTF-8 Python bundles, source/manifest inspection, exact script/runtime
  approval bindings, independently signed runtime receipts and strict target
  upload/result wire over both pinned SSH hops. The target runtime defaults off
  and requires concrete namespace/cgroup/tmpfs/read-only-runtime confinement.

## Verification and its limits

Latest Compute selection:907 passed,3 skipped in109.28s. Two optional browser
checks were covered separately by the real browser round:4 passed in15.36s.
The remaining skip is the actual disposable sandbox positive. Script fixtures
exercise real journals, authority, custody and SSH framing, but their explicitly
wire-only target never executes host scripts or proves production confinement.
Compilation of the changed Compute and coworker packages passed.

Earlier affected chat checks:220 passed with SQLCipher enabled, including durable
controls; final UI/docs checks14 passed. Real-browser journeys cover concurrent
chat intake, scoped memory/reload/group isolation, actual canonical task results,
exact follow-up receipts and script upload/inspection/approval/reload/mobile layout.
The final broader affected chat/boards/gateway/web/runner/orchestration regression
passed1603 checks with2 optional browser skips in450.39s. Those browser journeys
were covered by the separate Chromium round. This is an affected-surface result,
not a claim that the entire repository suite ran.

## External prerequisites and remaining limits

The coordinator host has UID1000, AppArmor's unprivileged-userns restriction=1,
no writable cgroup root and no provisioned disposable confinement fixture. Those
mandatory checks prevent actual confined execution here. No kernel settings,
mounts, cgroups, users, operator keys, live caches or devices were changed.
Provision and independently verify a disposable target before enabling scripts.
Targets also need separately authorized SSH helper/account/key/runtime setup,
protected persistent journals and reconciliation before restoring old state.

Claude review capacity was reported exhausted. Sol has not reviewed or supplied
any gate verdict. The branch must not be called release-ready or merged without
an authorized independent review and the required checks. No paid fallback or
GitHub Actions were used.

Real provider speed/quality, a live long-running worker process and actual remote
termination have not been demonstrated. Named bots are presentation/configuration,
not fabricated registered agent identities. /task is the explicit work-sharing
path; ordinary model text never becomes tool authority. Broader Go runners, VMs,
desktop control and infrastructure work remain roadmap scope rather than claims
made by this build. See RUNNING.md for isolated local usage instructions.

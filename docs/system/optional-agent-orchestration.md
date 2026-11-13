### Optional agent orchestration

- **Default off.**

  - The workspace owner explicitly enables orchestration; absent settings mean off.

  - A settings read error refuses admission.

  - Off retains the existing claim/start behavior and board JSON shape.

- **One project count.**

  - Claims, queued starts, leases and configured starts share a runner transaction.

  - Ordinary agents count by connection, workers by card; card/job overlaps count once.

  - Default cap is 3; project rows override it, configured boards use the smaller config/row cap.

  - Board `used` shows active slots, excluding the queued reservations counted at start creation.

- **Authority.**

  - Unconfigured starts require the active board coordinator PAT or a signed-in owner.

  - Owners separately trust seats and grant machines atomically; switching off demotes all seats and removes only their requester ids.

  - Trusted seats cannot create boards.

  - Connect alone never grants this trust.

  - A trusted project seat may hold the orchestrator lease of its own board and seat role while the board has no `[runner]` config; no config or requester edit is needed. Its direct starts are unchanged. Demotion, switch-off or revocation ends that authority at the next lease call.

  - A project seat never claims another board's lease, also when that board's runner machine lists it as a requester.

- **Lock order.**

  - Control snapshots precede waits.

  - Claims hold boards RESERVED then runner admission until board commit; runner admissions read boards SHARED.

- **Residual.**

  - Without a runner or subscribed `card.created` webhook, a new card waits for the orchestrator's next session.

  - An accepted live `[runner]` with `session = true` enables a persistent session through the existing machine runner loop, without requiring a `card.todo` trigger; configs without the key are unchanged and no service is activated automatically.

  - Splitting and inbox behavior are not introduced by this switch.

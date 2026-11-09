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

- **Lock order.**

  - Control snapshots precede waits.

  - Claims hold boards RESERVED then runner admission until board commit; runner admissions read boards SHARED.

- **Residual.**

  - Without a runner or subscribed `card.created` webhook, a new card waits for the orchestrator's next session.

  - An always-running orchestrator is separate work.

  - Splitting and inbox behavior are not introduced by this switch.


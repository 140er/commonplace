### Reservation authority and panel readiness

- A signed-in member who is not the workspace owner cannot change any status of another actor's todo or doing card, including doing to todo, done and review. Handoff is also refused. Restating the current status preserves the holder. Refusals name the workspace owner; coordinator takeover and drop apply only to released restricted-origin todo reservations.
- Cross-workspace authority tests use same-numbered existing cards, require specific reservation refusals, and verify neither workspace changes. A missing card cannot satisfy the test.
- Card forms are ready when their panel is visible, even while unrelated requests remain pending. The refresh protection for open forms is already present, so the release-loop browser tests no longer wait for network idle.

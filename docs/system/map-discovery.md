### Map discovery

- "Start agent work on map" starts one normal card start per runner machine on the `compute-map` board (wing `topology`); the agent runs the read-only collector and proposes through `topology_propose`, so the owner still approves what it found. Machines that cannot be started are named in the run with a reason. The machines' grants must include the `topology` wing; a machine without it is reported, not widened.
- A proposal no longer voids when a fact it proposes already holds the proposed value (another observer got there first); only a fact that changed to something else voids it.

### Mirror and search-index test waits

- Tests that wait for background mirror or search-index work drain on the actual completion condition (no sweep active, no mirror job pending) through one shared test helper, with a 120 s stuck-job limit, rather than one fixed `wait_for_mirrors` budget that returns silently when it runs out.

- A slow-host regression re-runs the converted selections with late-starting jobs and a wait that returns at once; the production sweep, its 20-thread and unit bounds and `wait_for_mirrors` are unchanged.

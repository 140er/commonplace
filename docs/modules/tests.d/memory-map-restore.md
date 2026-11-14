- **Tests:**

  - Executable map reachability/mounting and person-session privacy regressions, with owner controls, current-role faults and hidden-versus-absent graph equality.

  - Map regressions include pending, late-owner, real API deadline, failed, null and stale identity refreshes, RuntimeError drawer lookups, ordinary drawer rooms and public/private thread-link path controls; reference caching is request-local.

  - Map citation regressions cover fact_sources rows, failed lookups, orphan people and wildcard theme refusal parity. Selection regressions run through `GET /api/memory/<id>?view=map` as a member (every thread-reference form of a drawer room, member-level null source, theme scoping, hidden-equals-absent bodies); the owner-with-unproved-role guard path (source, every topic form, unresolvable citations, private-drawer refusal equal to absent) runs through the same HTTP route by wrapping the map selection in a failed role re-check; only the people-overlay cases use the direct graph helper, since members hold no people overlay.

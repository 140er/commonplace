- **Owns:**
  The owner-facing fix text of lease refusals names the settings section and the workspace switch in the Coworker vocabulary ("Coworker start permissions", "Coworker turned on for the workspace").
- **Promises:**
  Only the wording changes; refusal codes, statuses, `details.missing` slugs and the REST path in the fix text stay as they were.
- **How it fails:**
  A refusal that spells the role as "Orchestrator" or "orchestration" in its message fails the Coworker copy guard.
- **Tests:**
  `tests/test_coworker_tool_copy.py::test_user_visible_role_copy` and `tests/test_projects_seat_lease.py`.
